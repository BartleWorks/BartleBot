import asyncio
import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal
from wizwalker.errors import MemoryReadError
from wizwalker.utils import get_window_title

from bartlebot import account_identity, recovery_readiness
from bartlebot.credentials import CredentialStore
from bartlebot.launcher import auto_select
from bartlebot.nodes.timing import (
    CLIENT_DETECT_ERROR_BACKOFF,
    CLIENT_DETECT_INTERVAL,
    CLIENT_DETECT_INTERVAL_IDLE,
)
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


def _titled(clients, title: str):
    """The first client in *clients* whose window title matches, or None.

    Titles are read live rather than cached because a window is renamed after login and
    a relaunched client comes back under a new handle with the same title.
    """
    for c in clients:
        try:
            if get_window_title(c.window_handle) == title:
                return c
        except Exception:
            pass
    return None


class ClientManager(QObject):
    """Owns client detection, slot ordering, game-view embedding, and recovery.

    Signals:
        clients_changed: Emitted whenever the live client list changes.
        wizard_names_changed: Emitted with the per-client wizard names when they change.
        recovery_error:  Emitted when recovery cannot proceed due to a user-fixable error.
    """

    clients_changed = pyqtSignal(list)
    all_clients_changed = pyqtSignal(list, list)  # (active_clients, unhooked_clients)
    # list[str | None], positionally aligned with the live client list.
    wizard_names_changed = pyqtSignal(list)
    recovery_error = pyqtSignal(str)

    def __init__(
        self,
        handler,
        game_view,
        drops_widget,
        get_recovery_state: Callable[[], str | None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._handler = handler
        self._game_view = game_view
        self._drops_widget = drops_widget
        self._get_recovery_state = get_recovery_state

        self._clients: list = []
        # Last announced wizard names, so the signal fires on change rather than per tick.
        self._last_wizard_names: list = []
        # Maps window_handle → username for all clients seen this session.
        self._hwnd_account_map: dict[int, str] = {}
        # Last title seen per unmapped window, so a client that disconnects can still be
        # named in the log after its window is gone and get_window_title returns nothing.
        self._hwnd_titles: dict[int, str] = {}
        # Account-level user id per window, once one has been read. Identifies a client
        # BartleBot did not launch, whose title carries no account tag.
        self._hwnd_user_ids: dict[int, int] = {}
        # Desired slot ordering; None marks a slot currently being recovered.
        self._client_order: list[str | None] = []
        # Slots pending recovery: original_position → username
        self._pending_recovery: dict[int, str] = {}
        # Slot each username last occupied, remembered across a disconnect so a client
        # that comes back outside the recovery flow reclaims its original position.
        self._vacated_slots: dict[str, int] = {}
        self._recovery_in_progress: bool = False
        # Last known window resolution per username; used by recovery to restore original size.
        self._client_resolutions: dict[str, tuple[int, int]] = {}
        self._unhooked_clients: list = []
        self._unhooked_hwnds: set[int] = set()
        # Windows whose hooks are still being installed. activate_hooks() blocks until the
        # character is in-game, so activation runs as a background task per client and the
        # hwnd sits here meanwhile — see _start_activation for why this can't be awaited inline.
        self._activating_hwnds: set[int] = set()
        # Windows whose hook activation raised. Kept out of the published client list for
        # good: retrying would re-prepare the autobot region the failed hooks jump into.
        self._failed_hook_hwnds: set[int] = set()
        self._activation_tasks: set[asyncio.Task] = set()
        self._client_loop_task: asyncio.Task | None = None
        self._recovery_task: asyncio.Task | None = None
        self._machine = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def clients(self) -> list:
        return self._clients

    def set_machine(self, machine) -> None:
        """Called by AppController when a bot starts (machine != None) or stops (machine == None)."""
        self._machine = machine
        if machine is not None:
            # Fresh run — clear any leftover recovery state from the previous run.
            self._pending_recovery.clear()
            self._recovery_in_progress = False

    def start(self) -> None:
        """Create the client detection loop task. Call once after the event loop is running."""
        self._client_loop_task = asyncio.create_task(self._client_loop())

    def stop(self) -> None:
        """Cancel the detection loop and any in-flight hook activations.

        Hook activation can block indefinitely (a client parked at character select never
        writes the player struct), so those tasks would otherwise outlive the manager.
        """
        if self._client_loop_task is not None:
            self._client_loop_task.cancel()
            self._client_loop_task = None
        for task in list(self._activation_tasks):
            task.cancel()
        self._activation_tasks.clear()
        self._activating_hwnds.clear()
        self._failed_hook_hwnds.clear()

    def has_client_titled(self, title: str) -> bool:
        """Whether a fully hooked client with this window title is currently live.

        This is the signal that a launched instance has finished loading in: a client only
        reaches self._clients once activate_hooks() succeeds, which requires a character
        to be in a game zone. Used as the completion check for auto-select and recovery.
        """
        return self.client_titled(title) is not None

    def client_titled(self, title: str):
        """The live client whose window title matches, or None."""
        return _titled(self._clients, title)

    def recovery_gaps(self) -> list[tuple[int, str]]:
        """Live clients that could not be recovered, as ``(position, reason)`` pairs.

        Position is 1-based and matches the client list order, so it lines up with the
        Player numbering the sidebar and game tiles show.

        Both halves of the recovery path are checked, because either one missing has the
        same outcome: an account to relaunch, and a password to log it back in. The
        account map is private and the password lookup is CredentialStore's, so this is
        the only place both can be asked at once.
        """
        gaps: list[tuple[int, str]] = []
        for position, client in enumerate(self._clients, start=1):
            username = self._hwnd_account_map.get(client.window_handle)
            if not username:
                gaps.append((position, recovery_readiness.NO_ACCOUNT))
                continue
            try:
                has_password = bool(CredentialStore.retrieve(username))
            except Exception:
                logger.exception("Could not check the stored password for '%s'", username)
                continue  # Credential Manager is unhappy; do not claim a gap we cannot verify
            if not has_password:
                display = CredentialStore.display_name(username, self._alias_for(username))
                gaps.append((position, recovery_readiness.NO_PASSWORD.format(account=display)))
        return gaps

    def _alias_for(self, username: str) -> str | None:
        """The configured alias for *username*, or None. Keeps real usernames out of the GUI."""
        for account in SettingsManager.instance().accounts:
            if account.get("username") == username:
                return account.get("alias") or None
        return None

    def pending_client_titled(self, title: str):
        """The client for this window whether or not its hooks have come up yet.

        Auto-select reads the character-select screen through this. It must be the Client
        this manager already registered — building a second one for the same process
        re-prepares WizWalker's autobot region and corrupts the hooks the first one
        installed, so there is deliberately no way to ask for a fresh one.
        """
        return _titled(self._handler.clients, title)

    async def drive_client_in(self, expected_title: str) -> bool:
        """Advance a just-launched window through to in-game, then tidy up after it.

        This is the callback the launcher is handed (see MainWindow.set_auto_select_driver);
        it lives here because the completion signal is has_client_titled, which only this
        class can answer. Recovery uses it too, so a relaunched client and a freshly
        launched one take exactly the same path in.

        The single place the auto-select setting is read. With it off the window is left
        at character select for the user, and False says so — callers that need the client
        anyway (recovery) fall back to await_client_in.
        """
        if not SettingsManager.instance().auto_select_character:
            return False
        return await auto_select.drive_client_in(
            expected_title,
            lambda: self.has_client_titled(expected_title),
            lambda: self.pending_client_titled(expected_title),
        )

    async def await_client_in(self, expected_title: str) -> bool:
        """Wait for a window to reach in-game under its own steam, pressing nothing."""
        return await auto_select.wait_for_client_in(expected_title, lambda: self.has_client_titled(expected_title))

    def swap_clients(self, idx_a: int, idx_b: int) -> None:
        """Swap the positions of two clients in the live list.

        Changes which client index bot logic routes to, updates game-view tile labels,
        and rebuilds the sidebar — without unhooking or re-hooking any WizWalker clients.
        """
        if idx_a == idx_b:
            return
        if idx_a >= len(self._clients) or idx_b >= len(self._clients):
            return
        self._clients[idx_a], self._clients[idx_b] = (self._clients[idx_b], self._clients[idx_a])
        # Keep _client_order in sync so recovery preserves the user's reordering.
        if idx_a < len(self._client_order) and idx_b < len(self._client_order):
            self._client_order[idx_a], self._client_order[idx_b] = (
                self._client_order[idx_b],
                self._client_order[idx_a],
            )
        self._drops_widget.swap_clients(idx_a, idx_b)
        self.clients_changed.emit(self._clients)
        self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

    async def unhook_client(self, index: int) -> None:
        """Unhook WizWalker from the client at the given index without closing its game window."""
        if index >= len(self._clients):
            return
        prev_clients = list(self._clients)
        await self._unhook_one(self._clients[index])
        self._finish_hook_change(prev_clients)

    async def unhook_all(self) -> None:
        """Unhook every active client at once, leaving all game windows open."""
        prev_clients = list(self._clients)
        if not prev_clients:
            return
        await asyncio.gather(*[self._unhook_one(c) for c in prev_clients])
        self._finish_hook_change(prev_clients)

    async def rehook_client(self, hwnd: int) -> None:
        """Re-activate WizWalker hooks on a previously unhooked client."""
        client = next((c for c in self._unhooked_clients if c.window_handle == hwnd), None)
        if client is None:
            return
        prev_clients = list(self._clients)
        if await self._rehook_one(client):
            self._assign_rehooked_slots([client])
            self._finish_hook_change(prev_clients)

    async def rehook_all(self) -> None:
        """Re-activate WizWalker hooks on every previously unhooked client."""
        candidates = list(self._unhooked_clients)
        if not candidates:
            return
        prev_clients = list(self._clients)
        results = await asyncio.gather(*[self._rehook_one(c) for c in candidates])
        rehooked = [c for c, ok in zip(candidates, results) if ok]
        if not rehooked:
            return
        self._assign_rehooked_slots(rehooked)
        self._finish_hook_change(prev_clients)

    # ------------------------------------------------------------------
    # Hook toggling internals
    # ------------------------------------------------------------------

    async def _unhook_one(self, client) -> None:
        """Close hooks on *client* and move it from the active list to the unhooked list."""
        hwnd = client.window_handle
        self._unhooked_hwnds.add(hwnd)

        try:
            await client.close()
        except Exception:
            logger.exception("Error while unhooking client hwnd=%d", hwnd)

        username = self._hwnd_account_map.pop(hwnd, None)
        if username:
            try:
                self._client_order.remove(username)
            except ValueError:
                pass

        self._clients = [c for c in self._clients if c.window_handle != hwnd]
        self._unhooked_clients.append(client)
        logger.info("Unhooked client hwnd=%d — game window left open", hwnd)

    async def _rehook_one(self, client) -> bool:
        """Activate hooks on *client* and move it back into the active list. Returns success."""
        hwnd = client.window_handle
        try:
            await client.activate_hooks()
        except Exception:
            logger.exception("Error while rehooking client hwnd=%d", hwnd)
            return False

        self._unhooked_clients.remove(client)
        self._unhooked_hwnds.discard(hwnd)
        self._clients.append(client)
        logger.info("Rehooked client hwnd=%d", hwnd)
        return True

    def _assign_rehooked_slots(self, rehooked: list) -> None:
        """Restore ordering slots for clients that just came back under hooks."""
        self._refresh_account_map(self._clients)
        for client in rehooked:
            username = self._hwnd_account_map.get(client.window_handle)
            if username:
                self._assign_slot(username)

    def _finish_hook_change(self, prev_clients: list) -> None:
        """Reconcile embedding, machine state, and listeners after a hook add/remove."""
        self._sync_embedded_clients(prev_clients, self._clients)
        if self._machine:
            self._machine.set_clients(self._clients)
        self.clients_changed.emit(list(self._clients))
        self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

    # ------------------------------------------------------------------
    # Client loop
    # ------------------------------------------------------------------

    async def _client_loop(self) -> None:
        while True:
            try:
                self._handler.remove_dead_clients()
                for c in self._handler.get_new_clients():
                    self._start_activation(c)

                clients = list(self._handler.clients)

                # Exclude unhooked clients — window still open but hooks deactivated —
                # plus clients whose hooks are still coming up or failed to install.
                excluded = self._unhooked_hwnds | self._activating_hwnds | self._failed_hook_hwnds
                clients = [c for c in clients if c.window_handle not in excluded]

                # Clean up _unhooked_clients whose game windows have since closed.
                handler_hwnds = {c.window_handle for c in self._handler.clients}
                self._failed_hook_hwnds &= handler_hwnds
                closed_unhooked = [c for c in self._unhooked_clients if c.window_handle not in handler_hwnds]
                if closed_unhooked:
                    for c in closed_unhooked:
                        self._unhooked_clients.remove(c)
                        self._unhooked_hwnds.discard(c.window_handle)
                    self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

                if not clients:
                    # Detect removals before clearing the list — handles the case where the
                    # last remaining client(s) crash, which would otherwise skip _on_clients_removed.
                    if self._clients:
                        removed_hwnds = {c.window_handle for c in self._clients}
                        self._on_clients_removed(removed_hwnds)
                    self._sync_embedded_clients(self._clients, [])
                    self._clients = []
                    if self._machine:
                        self._machine.set_clients(self._clients)
                    self.clients_changed.emit([])
                    self.all_clients_changed.emit([], list(self._unhooked_clients))
                    await asyncio.sleep(CLIENT_DETECT_INTERVAL_IDLE)
                    continue

                # Update the account map for any clients whose titles are now known.
                self._refresh_account_map(clients)

                # Then place whatever the titles could not: a window BartleBot did not
                # launch carries no account tag, and is identified by what it reports in
                # memory instead. Runs before the comparison below so an adopted client
                # is already placed when its slot is assigned.
                await self._refresh_account_identity(clients)

                # Compare by HWND set to detect additions and removals.
                new_hwnd_set = {c.window_handle for c in clients}
                cur_hwnd_set = {c.window_handle for c in self._clients}
                if new_hwnd_set != cur_hwnd_set:
                    # Detect removals before rebuilding the list.
                    removed_hwnds = cur_hwnd_set - new_hwnd_set
                    if removed_hwnds:
                        self._on_clients_removed(removed_hwnds)

                    # Register any newly added known clients in _client_order.
                    #
                    # Sorting by window position puts clients that appear in the same poll
                    # cycle into visual layout order rather than EnumWindows Z-order. It only
                    # decides anything for windows nobody reserved a slot for — a client
                    # opened outside BartleBot. Anything launched from here is already placed
                    # by reserve_slots, and could not be ordered by position anyway: a launch
                    # spawns the whole batch at one shared anchor, so the keys are all equal.
                    new_additions = sorted(
                        [c for c in clients if c.window_handle not in cur_hwnd_set],
                        key=lambda c: (c.window_rectangle.y1, c.window_rectangle.x1),
                    )
                    for c in new_additions:
                        username = self._hwnd_account_map.get(c.window_handle)
                        if username:
                            recovery_pos = next(
                                (pos for pos, u in self._pending_recovery.items() if u == username),
                                None,
                            )
                            if recovery_pos is not None:
                                # Recovering client — restore to original slot rather than appending.
                                self._client_order[recovery_pos] = username
                                self._vacated_slots.pop(username, None)
                            else:
                                self._assign_slot(username)

                    ordered = self._build_ordered_clients(clients)
                    self._sync_embedded_clients(self._clients, ordered)
                    self._clients = ordered
                    if self._machine:
                        self._machine.set_clients(self._clients)
                    self.clients_changed.emit(self._clients)
                    self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

                # Keep wizard names current: notice character switches, and read the name
                # for any client that does not have one. One uint64 read per client per
                # cycle, plus a behavior walk only on a genuine miss.
                #
                # Runs *after* the client list is rebuilt above, and over self._clients
                # rather than the handler's list, for two reasons: the game tiles are keyed
                # by position in self._clients, so emitting the handler's order would put
                # names on the wrong tiles once a user reorders players; and tiles for
                # newly appeared clients are only created by the sync above, so emitting
                # first would announce names to tiles that do not exist yet — and the next
                # tick would see no change and stay quiet.
                await self._refresh_wizard_names(self._clients)

                await asyncio.sleep(CLIENT_DETECT_INTERVAL)

            except MemoryReadError:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                if self._machine:
                    self._machine.set_clients(self._clients)
                self.clients_changed.emit([])
                self.all_clients_changed.emit([], list(self._unhooked_clients))
                await asyncio.sleep(CLIENT_DETECT_INTERVAL)
            except Exception:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                if self._machine:
                    self._machine.set_clients(self._clients)
                self.clients_changed.emit([])
                self.all_clients_changed.emit([], list(self._unhooked_clients))
                logger.exception("Client loop error")
                await asyncio.sleep(CLIENT_DETECT_ERROR_BACKOFF)

    def _start_activation(self, client) -> None:
        """Begin hooking a newly detected client, without blocking the detection loop.

        activate_hooks() waits for the *player struct* address to be written, and that only
        happens once a character is in a game zone — a window sitting on the login or
        character select screen never writes it, and WizWalker passes no timeout, so the
        await simply never returns. Awaiting that inline stalls the whole loop: no zone
        refresh, no embedding, no crash detection, for as long as any one window is parked
        at character select. Auto-select makes that the normal case on every launch.

        Timing out and retrying is not the fix. activate_all_hooks() installs every hook
        before it waits, so a client abandoned mid-wait leaves jmps in the game process
        pointing into WizWalker's autobot scratch region; building a fresh Client for the
        same process re-runs _prepare_autobot, which zeroes that region out from under
        them. So a failed activation is left alone — the client stays registered with the
        handler and is reaped by remove_dead_clients() when its window closes.
        """
        hwnd = client.window_handle
        if hwnd in self._activating_hwnds:
            return
        self._activating_hwnds.add(hwnd)

        async def _activate() -> None:
            try:
                await client.activate_hooks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Client activation failed for hwnd %s", hwnd)
                self._failed_hook_hwnds.add(hwnd)
            finally:
                self._activating_hwnds.discard(hwnd)

        task = asyncio.create_task(_activate())
        self._activation_tasks.add(task)
        task.add_done_callback(self._activation_tasks.discard)

    async def _refresh_wizard_names(self, clients: list) -> None:
        """Keep cached wizard names current, and announce them when they change.

        Two jobs. Character switches invalidate a cached name: the window handle the cache
        is keyed by survives a switch, so without this a cached name goes on naming the
        previous wizard for the rest of the session — which is what sends a group invite to
        the wrong character. And any client without a name gets one read from memory, so
        the sidebar and the game tiles have something to show without anything having asked.

        Memory only — never the character page. Filling a label is not worth opening the
        character UI on a live client.

        Never raises: a stale name degrades grouping, but an exception here would take down
        the client loop that owns hook lifecycle and crash recovery.
        """
        from bartlebot import wizard_names  # noqa: PLC0415

        try:
            changed = await wizard_names.fill_names_from_memory(clients)
        except Exception:
            logger.debug("Could not refresh wizard names", exc_info=True)
            return

        names = [wizard_names.cached_name(c) for c in clients]
        if changed or names != self._last_wizard_names:
            self._last_wizard_names = names
            self.wizard_names_changed.emit(list(names))

    async def _refresh_account_identity(self, live_clients: list) -> None:
        """Tie each client to an account by the user id it reports, learning or adopting.

        Two directions, and which one runs depends on whether the title already placed
        the client:

        - **Learn.** A client the title matched was launched by BartleBot, so its account
          is certain — we typed those credentials. Its fingerprint is written to that
          account, which is the only moment the association can be established at all.
        - **Adopt.** A client the title could not place is matched by fingerprint against
          the configured accounts. This is what makes a window launched elsewhere and
          adopted here recoverable.

        Never raises: this runs inside the client loop, whose MemoryReadError branch
        silently empties the client list, so an exception escaping here would look like
        every client vanishing at once.
        """
        settings = SettingsManager.instance()
        accounts = settings.accounts
        if not accounts:
            return

        # An account already answering for a live window cannot also be another one's.
        # Checked against live clients rather than the account map, which keeps every
        # hwnd seen this session including long-closed ones.
        claimed = {
            self._hwnd_account_map[c.window_handle] for c in live_clients if c.window_handle in self._hwnd_account_map
        }

        for client in live_clients:
            hwnd = client.window_handle
            user_id = await self._read_user_id(client)
            if not user_id:
                continue  # not logged in yet; nothing to identify it by

            try:
                fp = account_identity.fingerprint(user_id, account_identity.account_key())
            except Exception:
                logger.exception("Could not fingerprint the user id for hwnd=%d", hwnd)
                return  # the key is broken, so the whole pass is pointless

            known = self._hwnd_account_map.get(hwnd)
            if known:
                if account_identity.stored_fingerprint(accounts, known) != fp:
                    accounts = account_identity.with_fingerprint(accounts, known, fp)
                    settings.accounts = accounts
                    # The setter only updates the in-memory dict — every other caller
                    # pairs it with an explicit save. Without this the association would
                    # last exactly as long as the process, which is the opposite of the
                    # point: it exists so a later session can recognise a window this one
                    # launched.
                    settings.save()
                    logger.info(
                        "Learned the identity of account '%s' (user id %s) — clients adopted from "
                        "outside BartleBot can now be recognised as this account",
                        known,
                        account_identity.mask(user_id),
                    )
                continue

            username = account_identity.match_account(accounts, fp)
            if username is None:
                continue
            if username in claimed:
                logger.warning(
                    "Client hwnd=%d reports account '%s', which another live client is already "
                    "answering for — leaving it unidentified rather than guessing",
                    hwnd,
                    username,
                )
                continue

            self._hwnd_account_map[hwnd] = username
            claimed.add(username)
            self._assign_slot(username)
            logger.info(
                "Adopted client hwnd=%d as account '%s' by its user id (%s)",
                hwnd,
                username,
                account_identity.mask(user_id),
            )

    async def _read_user_id(self, client) -> int:
        """This client's account-level user id, cached once a real one has been read.

        Zero means the client has not finished logging in; that is re-read every cycle
        until it resolves. A non-zero id cannot change without a new process, so it is
        read exactly once per window — the first read also resolves GameClient's base
        address by pattern scan, which is far too expensive to repeat per second.
        """
        cached = self._hwnd_user_ids.get(client.window_handle)
        if cached:
            return cached
        try:
            user_id = await client.game_client.user_id()
        except Exception:
            return 0
        if user_id:
            self._hwnd_user_ids[client.window_handle] = user_id
        return user_id or 0

    def _refresh_account_map(self, live_clients: list) -> None:
        """For each live client with an unrecognised HWND, try to match window title to an account."""
        import ctypes  # noqa: PLC0415
        import ctypes.wintypes  # noqa: PLC0415

        settings = SettingsManager.instance()
        for client in live_clients:
            if client.window_handle in self._hwnd_account_map:
                continue  # already known
            try:
                title = get_window_title(client.window_handle)
            except Exception:
                continue
            # Remembered whether or not it matches an account: the ones that never match
            # are exactly the ones _on_clients_removed has to explain itself about.
            self._hwnd_titles[client.window_handle] = title
            for account in settings.accounts:
                username = account["username"]
                alias = account.get("alias") or None
                display = CredentialStore.display_name(username, alias)
                if title == f"[{display}] Wizard101":
                    self._hwnd_account_map[client.window_handle] = username
                    try:
                        rect = ctypes.wintypes.RECT()
                        ctypes.windll.user32.GetClientRect(client.window_handle, ctypes.byref(rect))
                        w, h = rect.right - rect.left, rect.bottom - rect.top
                        if w > 0 and h > 0:
                            self._client_resolutions[username] = (w, h)
                    except Exception:
                        pass
                    break

    def _on_clients_removed(self, removed_hwnds: set) -> None:
        """Handle client removal: update _client_order and queue recovery if bot is running."""
        machine = self._machine
        bot_running = machine is not None and not machine.is_stopped
        for hwnd in removed_hwnds:
            # Dropped first: unlike the title, nothing below reports the user id, and the
            # unmapped path returns early.
            self._hwnd_user_ids.pop(hwnd, None)
            username = self._hwnd_account_map.get(hwnd)
            if not username:
                self._log_unrecoverable_removal(hwnd, bot_running)
                continue
            orig_pos = next((i for i, u in enumerate(self._client_order) if u == username), None)
            if orig_pos is not None:
                self._client_order[orig_pos] = None
                # Remember the slot even when recovery is queued: recovery can be
                # abandoned (launch failure, login timeout) while the client still
                # shows up later, and it should reclaim this slot rather than be
                # appended past the end of the ordering.
                self._vacated_slots[username] = orig_pos
                if bot_running:
                    self._pending_recovery[orig_pos] = username
                    logger.warning(
                        "Client at slot %d (account: %s) disconnected — queuing recovery",
                        orig_pos,
                        username,
                    )
            elif bot_running:
                # Mapped to an account, but holding no slot — the other way a removal can
                # skip recovery in silence.
                logger.warning(
                    "Client hwnd=%d (account: %s) disconnected, but holds no slot in the client "
                    "ordering — recovery is being SKIPPED",
                    hwnd,
                    username,
                )
            self._hwnd_titles.pop(hwnd, None)
        if self._pending_recovery:
            # Pause immediately to prevent FAILURE results from accumulating before
            # recovery resumes the machine. Idempotent if already paused.
            if bot_running:
                machine.pause()
            if not self._recovery_in_progress:
                self._recovery_in_progress = True
                self._recovery_task = asyncio.create_task(self._run_recovery_queue())

    def _log_unrecoverable_removal(self, hwnd: int, bot_running: bool) -> None:
        """Say out loud that a disconnected client has no account, and so cannot be recovered.

        The account map is keyed on the ``[account] Wizard101`` window title, and that
        title is written by launch_profile during login — so only a client BartleBot
        launched carries one. A window started from the game's own launcher and adopted
        here never enters the map, and every recovery decision downstream is keyed on a
        username this client does not have.

        Until this existed the miss was a bare ``continue``: an overnight run lost every
        client, queued nothing, and left five hours of silence in the log with no
        indication that recovery had been skipped rather than attempted.
        """
        title = self._hwnd_titles.pop(hwnd, None)
        described = repr(title) if title else "unreadable"
        if not bot_running:
            logger.debug("Client hwnd=%d (title %s) closed; it has no account mapping", hwnd, described)
            return
        logger.warning(
            "Client hwnd=%d (title %s) disconnected, but no account is mapped to it — "
            "recovery is being SKIPPED and the bot will stay paused. Only clients launched "
            "by BartleBot carry the '[account] Wizard101' title that ties a window to an "
            "account; one started elsewhere and adopted here has no credentials to log back in with.",
            hwnd,
            described,
        )

    def reserve_slots(self, usernames: list[str]) -> None:
        """Pin the slot order for a batch of accounts about to be launched.

        Slot order is otherwise inferred from the windows themselves, and nothing about a
        window carries the order the user asked for. A launch profile is the one place that
        order is stated explicitly, so it is stated here, before the windows exist.

        Three things were deciding the order instead, none of them the profile:

        - Clients are published as each one's own hooks come up, so _assign_slot ran in
          whatever sequence the characters finished loading in — a 3-15 s race, different
          every run.
        - launch_profile pairs accounts to windows through a set difference, so account 0's
          credentials go to an arbitrary one of the new windows.
        - The positional sort in _client_loop cannot break the tie, because the whole batch
          is spawned at one shared anchor and every window has the same coordinates.

        Reserving up front makes all three irrelevant: _assign_slot returns early for an
        account already in the ordering, so each client keeps the slot named for it here
        however late it loads in. Accounts already running keep their slots, and slots
        being held for a client in recovery are left alone.
        """
        held_for_recovery = set(self._pending_recovery)
        for username in usernames:
            if username in self._client_order:
                continue
            # An explicit launch overrides whatever slot this account last sat in: the
            # profile is a statement of intent, the vacated slot is only a memory.
            self._vacated_slots.pop(username, None)
            free = next(
                (i for i, u in enumerate(self._client_order) if u is None and i not in held_for_recovery),
                None,
            )
            if free is not None:
                self._client_order[free] = username
            else:
                self._client_order.append(username)
        logger.info("Reserved slot order for launch: %s", self._client_order)

    def _assign_slot(self, username: str) -> None:
        """Place *username* into _client_order without growing the ordering needlessly.

        Preference order: the slot this client last occupied, then any slot left empty
        by a client that disconnected, and only then a new slot at the end.

        Appending unconditionally is what caused slot drift: a disconnect always nulls
        the client's slot, but recovery does not always run (bot stopped) or always
        succeed (login timeout). Each such round left a permanent hole and appended the
        returning client past the end, so the ordering grew past the client count —
        four clients reporting slots 0-4.
        """
        if username in self._client_order:
            return
        prev_slot = self._vacated_slots.pop(username, None)
        if prev_slot is not None and prev_slot < len(self._client_order) and self._client_order[prev_slot] is None:
            self._client_order[prev_slot] = username
            return
        free = next((i for i, u in enumerate(self._client_order) if u is None), None)
        if free is not None:
            self._client_order[free] = username
        else:
            self._client_order.append(username)

    def _build_ordered_clients(self, live_clients: list) -> list:
        """Build the client list in _client_order order; unknown-account clients go at the end."""
        username_to_client = {
            self._hwnd_account_map.get(c.window_handle): c
            for c in live_clients
            if self._hwnd_account_map.get(c.window_handle)
        }
        unknown_clients = [c for c in live_clients if self._hwnd_account_map.get(c.window_handle) is None]
        # Clients in desired order; skip None slots (awaiting recovery)
        ordered = [username_to_client[u] for u in self._client_order if u is not None and u in username_to_client]
        # Append manually-launched clients whose accounts are not tracked
        for c in unknown_clients:
            if c not in ordered:
                ordered.append(c)
        return ordered

    def _sync_embedded_clients(self, prev_clients: list, new_clients: list) -> None:
        """Reconcile the game view's tiles with the new client ordering.

        Runs in two phases: every slot whose occupant changes is vacated first, then
        the new occupants are embedded. This ordering matters because GameView keys
        its tiles by slot index, not by HWND — `unembed_client(idx)` tears down
        whatever tile currently sits at `idx`. Interleaving unembeds and embeds (the
        obvious per-client loop) lets one client's unembed destroy a tile another
        client was already moved into, and `embed_client` silently no-ops when its
        slot is still occupied. Either way a game window is left floating unembedded.

        Multi-client recovery is where this bites: clients come back one at a time and
        _build_ordered_clients compacts out the not-yet-recovered slots, so every step
        renumbers the clients that already returned.
        """
        prev_by_idx = {i: c.window_handle for i, c in enumerate(prev_clients)}
        new_by_idx = {i: c.window_handle for i, c in enumerate(new_clients)}

        # Phase 1 — vacate every slot that is losing its current occupant.
        for idx, hwnd in sorted(prev_by_idx.items()):
            if new_by_idx.get(idx) != hwnd:
                try:
                    self._game_view.unembed_client(idx)
                except Exception:
                    # Routine when the window is already gone (crash), so keep it quiet.
                    logger.debug("unembed_client(%d) failed", idx, exc_info=True)

        # Phase 2 — fill the vacated slots. Every index reached here is free: it was
        # either unembedded above or never occupied.
        for idx, hwnd in sorted(new_by_idx.items()):
            if prev_by_idx.get(idx) != hwnd:
                try:
                    self._game_view.embed_client(hwnd, idx)
                except Exception:
                    logger.exception("embed_client(hwnd=%d, idx=%d) failed", hwnd, idx)

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def _run_recovery_queue(self) -> None:
        """Process _pending_recovery sequentially — one client at a time.

        The FSM is kept paused for the entire duration (across all clients). Once all
        pending recoveries complete successfully, the FSM is jumped to recovery_state
        and resumed exactly once.
        """
        try:
            while self._pending_recovery:
                orig_pos, username = next(iter(self._pending_recovery.items()))
                try:
                    await self._reconnect_client(orig_pos, username)
                except Exception:
                    logger.exception("Unhandled error in recovery for slot %d — stopping bot", orig_pos)
                    self._pending_recovery.clear()
                    if self._machine:
                        self._machine.stop()
                    return
                self._pending_recovery.pop(orig_pos, None)

            # All pending clients are back in-game — regroup, then jump to recovery state
            # and resume. Grouping happens while the FSM is still paused so no node is
            # competing for the mouse, and before the state jump so the bot resumes with
            # the group already restored.
            await self._regroup_after_recovery()

            machine = self._machine
            if machine and not machine.is_stopped:
                recovery_state = self._get_recovery_state()
                if recovery_state:
                    machine.force_state(recovery_state)
                    machine.resume()
                    logger.info("All clients recovered — FSM resuming at %r", recovery_state)
                else:
                    # Unreachable via a loaded bot: BotConfig requires recovery_state and
                    # validates it against the states list. Only a run with no config at
                    # all lands here, which means something else has already gone wrong.
                    logger.error("Recovery finished with no bot config loaded — stopping.")
                    machine.stop()
        finally:
            self._recovery_in_progress = False

    async def _regroup_after_recovery(self) -> None:
        """Rebuild the in-game group after a recovery, if the user has that enabled.

        A crashed client is dropped from its group by the game and comes back ungrouped,
        which silently costs the fleet the guaranteed first turn in combat. Grouping is
        done through the friends list, which works across zones, so this runs correctly
        wherever the recovered client happens to have landed.

        Never raises and never stops the bot: running ungrouped is a degraded run, but
        stopping outright turns one crash into a dead overnight session.
        """
        from bartlebot import group_manager, wizard_names  # noqa: PLC0415

        if not SettingsManager.instance().auto_group_players:
            return

        clients = list(self._clients)
        if len(clients) < 2:
            logger.debug("Skipping post-recovery grouping — fewer than two clients connected")
            return

        try:
            # Survey once and hand the result to form_group — see the matching comment in
            # AppController._form_pre_run_group.
            states = await group_manager.read_group_states(clients)
            if all(s.grouped for s in states) and group_manager.same_group(states) is not False:
                logger.debug("Skipping post-recovery grouping — all clients are already in one group")
                return
            names = await wizard_names.resolve_names(clients)
            result = await group_manager.form_group(clients, names, states=states)
            if result.success:
                logger.info("Post-recovery grouping: %s", result.summary())
            else:
                logger.warning(
                    "Post-recovery grouping did not fully succeed (%s) — the bot will resume ungrouped",
                    result.summary(),
                )
        except Exception:
            logger.warning("Post-recovery grouping failed — the bot will resume ungrouped", exc_info=True)

    async def _reconnect_client(self, orig_pos: int, username: str) -> None:
        """Relaunch a disconnected client, navigate to in-game, then resume the FSM.

        The screens between auto-login and in-game are driven by drive_client_in, the same
        path a normal launch takes — see launcher/auto_select.py for why that has to be
        done blind. Recovery differs only in what failure means: a bot mid-run cannot
        continue a player short, so a drive that gives up stops the machine.
        """
        from bartlebot.launcher.launch_profile import launch_account  # noqa: PLC0415

        machine = self._machine
        if machine is None:
            return

        password = CredentialStore.retrieve(username)
        if not password:
            logger.error("No stored password for '%s' — stopping bot", username)
            self.recovery_error.emit(
                f"No stored password for account '{username}'.\n\n"
                "Open Settings → Accounts and save the password, then restart the bot."
            )
            machine.stop()
            return

        settings = SettingsManager.instance()
        accounts_meta = {a["username"]: a for a in settings.accounts}
        alias = accounts_meta.get(username, {}).get("alias") or None
        display_name = CredentialStore.display_name(username, alias)
        expected_title = f"[{display_name}] Wizard101"

        # Use the last known resolution for this client, captured while the window was alive.
        # Falls back to None (global setting) if the client was never seen before recovery.
        crashed_resolution = self._client_resolutions.get(username)

        logger.info("Relaunching '%s' for recovery at slot %d", display_name, orig_pos)
        try:
            await launch_account(username, password, display_name, resolution=crashed_resolution)
        except Exception:
            logger.exception("Failed to launch account '%s' — stopping bot", display_name)
            machine.stop()
            return

        if SettingsManager.instance().auto_select_character:
            reached = await self.drive_client_in(expected_title)
        else:
            # The user has opted out of automatic character selection, so recovery waits
            # for them rather than pressing anything — but it still has to wait, since the
            # run cannot resume a player short.
            logger.warning(
                "Auto-select is off — '%s' has been relaunched and is waiting at character "
                "select. Log it in to resume the run.",
                display_name,
            )
            self.recovery_error.emit(
                f"'{display_name}' has been relaunched and is waiting at character select.\n\n"
                "Log it in to resume the run, or turn on Settings → General → "
                "Automatically select a character to have this done for you."
            )
            reached = await self.await_client_in(expected_title)

        if not reached:
            logger.error("Timed out waiting for reconnected client '%s' — stopping bot", display_name)
            machine.stop()
            return

        logger.info("Client '%s' recovered and in-game, slot %d restored", display_name, orig_pos)

        # Restore the slot so the next _client_loop rebuild puts the client back in order.
        # machine.clients is kept in sync by _client_loop automatically.
        self._client_order[orig_pos] = username
