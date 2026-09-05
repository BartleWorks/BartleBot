import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal
from wizwalker.errors import MemoryReadError
from wizwalker.utils import get_window_title

from bartlebot import account_identity, recovery_readiness, relog
from bartlebot.credentials import CredentialStore
from bartlebot.launch_tracker import LaunchStage, LaunchTracker
from bartlebot.launcher import auto_select
from bartlebot.nodes.timing import (
    CLIENT_DETECT_ERROR_BACKOFF,
    CLIENT_DETECT_INTERVAL,
    CLIENT_DETECT_INTERVAL_IDLE,
    RECOVERY_BATCH_SETTLE,
    RELOG_PARK_TIMEOUT,
    RELOG_WATCH_INTERVAL,
)
from bartlebot.settings_manager import SettingsManager
from bartlebot.tile_slots import TileSlot, build_tile_slots
from bartlebot.utils.process_resources import gdi_object_count
from bartlebot.utils.window_kill import force_close_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RecoveryTarget:
    """One slot being relaunched, with everything the launcher needs to bring it back."""

    orig_pos: int
    username: str
    password: str
    display_name: str
    title: str
    resolution: tuple[int, int] | None


def _group_by_resolution(
    targets: list[_RecoveryTarget],
) -> list[tuple[tuple[int, int] | None, list[_RecoveryTarget]]]:
    """Split targets into batches that can share one config.xml patch.

    The game reads its window resolution from a single file at startup, so a batch can
    only hold clients that want the same one. Order within a group is preserved, and so is
    the order the groups themselves are launched in, so a fleet with one resolution — the
    normal case — comes back as one batch in slot order.
    """
    groups: dict[tuple[int, int] | None, list[_RecoveryTarget]] = {}
    for target in targets:
        groups.setdefault(target.resolution, []).append(target)
    return list(groups.items())


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
        # Character roster per account, loaded from keyring on first use. Cached because
        # the learning pass runs every client-loop tick and a keyring read is not free;
        # this process is the only writer, so the cache cannot go stale under us.
        self._rosters: dict[str, list[dict]] = {}
        # Desired slot ordering; None marks a slot currently being recovered.
        self._client_order: list[str | None] = []
        # Accounts with a launch running right now. Separate from the ordering above
        # because a reservation outlives the launch that made it -- see launch_tracker.
        self._launches = LaunchTracker()
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
        # Windows being closed on purpose for a relog. Consumed by _on_clients_removed so
        # scheduled maintenance is not filed in the log as a fault -- see #156.
        self._relogging_hwnds: set[int] = set()
        self._relog_watch_task: asyncio.Task | None = None
        self._relog_in_progress: bool = False
        self._machine = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def clients(self) -> list:
        return self._clients

    @property
    def unhooked_clients(self) -> list:
        """Clients that were unhooked but whose game windows are still open."""
        return self._unhooked_clients

    def set_machine(self, machine) -> None:
        """Called by AppController when a bot starts (machine != None) or stops (machine == None)."""
        self._machine = machine
        if machine is not None:
            # Fresh run — clear any leftover recovery state from the previous run.
            self._pending_recovery.clear()
            self._recovery_in_progress = False
            self._relogging_hwnds.clear()
            self._relog_in_progress = False
            self._start_relog_watch()
        else:
            self._stop_relog_watch()

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

        The single place the auto-select setting is read. With it off the loading screens
        are still cleared — they carry no decision — but the window is then left sitting at
        character select for the user to pick from, and False says so: the client is not
        in-game. Callers that need it anyway (recovery) fall back to await_client_in.
        """
        if not SettingsManager.instance().auto_select_character:
            await auto_select.drive_to_character_select(
                expected_title,
                lambda: self.has_client_titled(expected_title),
                lambda: self.pending_client_titled(expected_title),
            )
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
        # Both numbers moved — the slots the tiles sit in and the player indices their
        # captions read from — so the view is re-derived rather than relabelled. The tiles
        # themselves are re-keyed, not rebuilt, so no game window is re-parented.
        self._sync_tiles()
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

    async def shutdown(self, *, close_windows: bool) -> list[int]:
        """Release every client on the way out, optionally closing their game windows.

        Quitting with clients still embedded destroys them: ``ClientTile`` re-parents each
        game window under the Qt window with ``SetParent``, so Qt takes its children down
        with it when the window is torn down (#155). Unhooking is what releases them —
        the same path the Unhook All button already runs — so this unhooks whichever way
        the user answered, and only then closes the windows it was told to close.

        Returns the hwnds whose process survived the close sequence; always empty when
        *close_windows* is False.
        """
        # The detection loop re-hooks and re-embeds on every tick, so it goes first:
        # anything it did after the unhook would be a window embedded on the way out.
        self.stop()
        # pids are read while the clients still exist — force_close_client falls back to
        # reading one from the window, which fails once the window is gone.
        targets = (
            [(c.window_handle, c.process_id) for c in self._clients + self._unhooked_clients] if close_windows else []
        )
        try:
            await self.unhook_all()
        except Exception:
            logger.exception("Error while unhooking clients on shutdown")
        # Belt and braces: a tile left behind by a failed unhook would still take its
        # game window down with the Qt window, which is the whole bug.
        self._game_view.unembed_all()
        if not targets:
            return []
        results = await asyncio.gather(*[self._close_game_window(h, pid) for h, pid in targets])
        return [hwnd for (hwnd, _), closed in zip(targets, results) if not closed]

    @staticmethod
    async def _close_game_window(hwnd: int, pid: int | None) -> bool:
        """Close one game window, reporting rather than raising when it will not go."""
        try:
            return await force_close_client(hwnd, pid=pid)
        except Exception:
            logger.exception("Failed to close game window hwnd=%d", hwnd)
            return False

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
        self._sync_tiles()
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

                # Identify windows by title, over *every* live window rather than only the
                # hooked ones. The launcher titles a window right after login, so this is
                # what lets a client still at character select be matched to its account,
                # given a slot below, and put on screen — which is the whole point of
                # embedding before a character is in game. It is title reads and a window
                # rect, no memory, so an unhooked window costs nothing here.
                self._refresh_account_map(self._handler.clients)

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
                    self._clients = []
                    # Not a return to the launch screen: this branch is also the whole of a
                    # launch, from the first window appearing until the first character is
                    # in game, and those windows have tiles.
                    self._place_pending_accounts()
                    self._sync_tiles()
                    if self._machine:
                        self._machine.set_clients(self._clients)
                    self.clients_changed.emit([])
                    self.all_clients_changed.emit([], list(self._unhooked_clients))
                    await asyncio.sleep(CLIENT_DETECT_INTERVAL_IDLE)
                    continue

                # Place whatever the titles could not: a window BartleBot did not
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
                            self._place_account(username)

                    self._clients = self._build_ordered_clients(clients)
                    if self._machine:
                        self._machine.set_clients(self._clients)
                    self.clients_changed.emit(self._clients)
                    self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

                # Windows still logging in or choosing a character reach neither branch
                # above — they are not clients yet — so they are placed and drawn here.
                self._place_pending_accounts()
                self._sync_tiles()

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
                self._learn_character_roster(self._clients)

                await asyncio.sleep(CLIENT_DETECT_INTERVAL)

            except MemoryReadError:
                self._clients = []
                self._game_view.unembed_all()
                if self._machine:
                    self._machine.set_clients(self._clients)
                self.clients_changed.emit([])
                self.all_clients_changed.emit([], list(self._unhooked_clients))
                await asyncio.sleep(CLIENT_DETECT_INTERVAL)
            except Exception:
                self._clients = []
                self._game_view.unembed_all()
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

    def _learn_character_roster(self, clients: list) -> None:
        """Record which characters each identified account has, for the launch screen.

        The launch screen has to describe an account that is not running, and nothing
        else in the app remembers a character past the life of its window. This is the
        one moment both halves are in hand: a live client whose account the map has
        placed, whose name the cache holds, and whose character id says which character
        that name belongs to.

        Cheap by construction. Both reads are dict lookups into caches the loop has
        already filled — no memory access of its own — and a keyring write only happens
        when the roster actually changes, which for a stable set of characters is once
        each, ever.

        Never raises: this runs inside the client loop, whose MemoryReadError branch
        empties the client list, so an exception escaping here would look like every
        client vanishing at once. character_roster's own calls swallow their failures,
        so the remaining risk is a client object going away mid-pass.
        """
        from bartlebot import character_roster, wizard_names  # noqa: PLC0415

        for client in clients:
            try:
                username = self._hwnd_account_map.get(client.window_handle)
                if not username:
                    continue  # unidentified — an adopted window we have never launched
                name = wizard_names.cached_name(client)
                gid = wizard_names.cached_character_id(client)
                if not name or gid == wizard_names.NO_CHARACTER:
                    continue  # at character select, mid-login, or the name is not read yet
                roster = self._rosters.get(username)
                if roster is None:
                    roster = character_roster.load(username)
                    self._rosters[username] = roster
                updated = character_roster.upsert(roster, gid, name)
                if updated == roster:
                    continue
                self._rosters[username] = updated
                if character_roster.save(username, updated):
                    # No name and no id in the message: a log line naming a wizard is the
                    # thing the roster's keyring storage exists to avoid, and the count is
                    # what makes this line useful anyway.
                    logger.info(
                        "Learned a character for account '%s' (%d known)",
                        username,
                        len(updated),
                    )
            except Exception:
                logger.debug("Could not update the character roster for a client", exc_info=True)

    def characters_for(self, username: str) -> list[dict]:
        """The known characters for *username*, newest last. Never raises.

        Public because the launch screen asks about accounts that are not running, which
        is precisely the case no client object can answer.
        """
        from bartlebot import character_roster  # noqa: PLC0415

        roster = self._rosters.get(username)
        if roster is None:
            roster = character_roster.load(username)
            self._rosters[username] = roster
        return list(roster)

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
            # Was this window closed by relog_clients rather than lost? Consumed here, so a
            # client that comes back and later dies for real is reported as a disconnect.
            relogging = hwnd in self._relogging_hwnds
            self._relogging_hwnds.discard(hwnd)
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
                    # A relog is routed through this same path on purpose, and filing
                    # scheduled maintenance as a fault is the kind of double-meaning that
                    # makes a ten-hour log unreadable (#156). The wording is the only thing
                    # that differs — everything downstream of here is identical.
                    if relogging:
                        logger.info(
                            "Client at slot %d (account: %s) closed for a relog — bringing it back",
                            orig_pos,
                            username,
                        )
                    else:
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

    def begin_launch(self, usernames: list[str]) -> list[str]:
        """Claim slots and open a launch for *usernames*, returning the ones accepted.

        The single door both launch routes go through. They used to reserve by different
        paths -- a single account called ``reserve_slots`` from the window, a profile
        handed the reserver down into ``launch_profile`` -- and neither could see the
        other, so a click during a launch reserved nothing new and spawned a second batch
        anyway. Rejecting an account already in flight is the whole point, and it can only
        be done where both routes meet.

        An account is rejected for either of two reasons, and they are different states:
        a launch is already running for it, or it is already *here* -- a window open and
        recognised, whether hooked, embedded, or set aside unhooked. The second is the one
        a profile hits: a profile lists accounts, not windows, so starting a four-account
        profile with two of them already up would spawn a second window for each of those
        two, logged into the same account as the one on screen. The in-flight tracker
        cannot see that, because those launches finished -- successfully -- some time ago.

        The rejected ones are dropped rather than failing the batch: a profile of four with
        one account already launching should bring up the other three. Callers pair this
        with ``end_launch`` in a ``finally`` -- an attempt that is never ended leaves the
        account unlaunchable for the rest of the session.
        """
        active = set(self.active_accounts)
        wanted = [u for u in usernames if u not in active]
        accepted = self._launches.begin(wanted)
        if accepted:
            self.reserve_slots(accepted)
            self._sync_tiles()
        already_here = [u for u in usernames if u in active]
        if already_here:
            logger.info("Ignoring launch for account(s) already running: %s", ", ".join(already_here))
        rejected = [u for u in wanted if u not in accepted]
        if rejected:
            logger.info("Ignoring launch for account(s) already launching: %s", ", ".join(rejected))
        return accepted

    def end_launch(self, usernames: list[str]) -> None:
        """Close the launches for *usernames*, freeing the slot of any that never arrived.

        Called from the caller's ``finally``, so it runs on the failure paths too -- and
        those are the ones that matter. Nothing else takes a reservation back: a launch
        that dies at the login screen would otherwise hold its slot, and the grid would
        keep a place for a window that is not coming until the app restarts.

        A slot is only released if no window turned up for it. An account that reached the
        login screen keeps its slot whether or not the drive that followed succeeded --
        there is a real window sitting in it, and moving it would be the tile shuffle the
        reserved ordering exists to prevent.

        A launch that failed keeps its attempt open so its tile can say so, but only where
        there is a tile to say it on: an account whose window came up and whose *drive*
        then failed is closed here like any other, because its slot holds a real client
        tile and a failure state with nowhere to be drawn is a launch the user can never
        dismiss.
        """
        arrived = set(self._hwnd_account_map.values())
        for username in usernames:
            if username in arrived:
                self._launches.dismiss(username)
            else:
                self._launches.end([username])
        # Only the launches that actually closed give their slots back. One left open
        # because it failed is still holding a tile, and the tile has to stay where it is
        # for the user to read it and click it away.
        self._release_unarrived([u for u in usernames if u not in self._launches])
        self._sync_tiles()

    def dismiss_launch(self, username: str) -> None:
        """Clear a failed launch the user has acknowledged, giving its slot back.

        The other half of leaving a failed attempt open. Nothing else closes one -- that is
        the point of the terminal stage -- so without this a failure is exactly the
        permanent artifact the pending tile was built to avoid.
        """
        if not self._launches.dismiss(username):
            return
        self._release_unarrived([username])
        self._sync_tiles()

    def report_launch_failed(self, username: str) -> None:
        """Mark a launch as having given up, and redraw its tile to say so."""
        if self._launches.fail(username):
            self._sync_tiles()

    def report_launch_stage(self, username: str, stage: LaunchStage) -> None:
        """Record which part of its launch *username* has reached, and redraw its tile.

        Handed to the launcher as its progress callback. Syncing only on a real change
        keeps this off the tick path: the launcher reports a stage per account per phase,
        which is a handful of calls per launch, and a repeated report costs a dict lookup.
        """
        if self._launches.set_stage(username, stage):
            self._sync_tiles()

    @property
    def launching(self) -> list[str]:
        """Accounts with a launch running, for a caller deciding whether to offer another."""
        return list(self._launches.in_flight())

    @property
    def active_accounts(self) -> list[str]:
        """Accounts that already have a game window open under this app.

        Read off the account map rather than ``clients``, because the question is about
        windows and not about hooks. ``_refresh_account_map`` runs over every live window
        each tick -- hooked, unhooked, and still at character select -- so the map names
        an account from the moment the launcher titles its window until that window
        closes, which is exactly the span during which launching it again would produce a
        duplicate.

        An adopted window BartleBot has never launched is in here too, once
        ``_refresh_account_identity`` has fingerprinted it. One belonging to an account
        with no fingerprint on file stays unidentified and so cannot be guarded -- the
        same known limitation ``account_identity`` documents, and the same fix: launching
        that account from here once teaches it permanently.
        """
        return list(dict.fromkeys(self._hwnd_account_map.values()))

    def _release_unarrived(self, usernames: list[str]) -> None:
        """Give back the reserved slots of accounts that never produced a window.

        Trailing empties are trimmed as well, so a failed launch does not leave the
        ordering longer than the fleet it describes. That length is what ``_assign_slot``
        hands out from, and letting it grow is the slot drift its docstring describes.
        """
        arrived = set(self._hwnd_account_map.values())
        released = False
        for username in usernames:
            if username in arrived:
                continue
            for pos, held in enumerate(self._client_order):
                if held == username:
                    self._client_order[pos] = None
                    self._vacated_slots.pop(username, None)
                    released = True
        if not released:
            return
        while self._client_order and self._client_order[-1] is None:
            self._client_order.pop()
        logger.info("Released slot(s) for launch(es) that produced no window: %s", self._client_order)

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

    def _place_account(self, username: str) -> None:
        """Give *username* its slot, honouring a recovery that is holding one for it.

        A client coming back from a crash goes to the slot it left, not to the first free
        one — the ordering is the user's, and recovery is meant to be invisible in it.
        Everything else falls through to _assign_slot's preference order.
        """
        recovery_pos = next((pos for pos, u in self._pending_recovery.items() if u == username), None)
        if recovery_pos is not None:
            self._client_order[recovery_pos] = username
            self._vacated_slots.pop(username, None)
        else:
            self._assign_slot(username)

    def _place_pending_accounts(self) -> None:
        """Give every identified window a slot, whether or not its hooks are up.

        A window is identified by its *title*, which the launcher sets immediately after
        login — long before a character is in game. Claiming the slot then is what lets the
        tile appear at character select and then *stay where it is* when the client finally
        hooks: the slot it is drawn in is already the slot it will keep.

        A launch profile has reserved these itself (see reserve_slots), so this changes
        nothing there. It is what covers an account launched on its own from a card, which
        states no order and so has nothing to reserve.
        """
        for hwnd in sorted(self._embeddable_hwnds()):
            username = self._hwnd_account_map.get(hwnd)
            if username and username not in self._client_order:
                self._place_account(username)

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

    def _embeddable_hwnds(self) -> set[int]:
        """Every live window that may hold a tile, hooked or not.

        A window is embeddable as soon as it exists: ``ClientTile`` needs an hwnd and
        nothing else, so there is no reason to wait for a character to be in game. Two
        exclusions, both deliberate. A client the user unhooked by hand is meant to be left
        alone. A client whose hooks *failed* can never become a player and is never retried
        (see ``_start_activation``), so giving it a tile would promise something the app
        cannot deliver.
        """
        live = {c.window_handle for c in self._handler.clients}
        return live - self._unhooked_hwnds - self._failed_hook_hwnds

    def _account_label(self, username: str) -> str:
        """What to call a window that has an account but no player index yet."""
        meta = next((a for a in SettingsManager.instance().accounts if a.get("username") == username), {})
        return CredentialStore.display_name(username, meta.get("alias") or None)

    def tile_slots(self) -> list[TileSlot]:
        """Every window that should be on screen right now, in slot order.

        Public because it is the whole contract with the game view, and because a test
        that cannot ask this question has to reach into the ordering by hand.
        """
        return build_tile_slots(
            client_order=self._client_order,
            hwnd_accounts=self._hwnd_account_map,
            embeddable_hwnds=self._embeddable_hwnds(),
            client_hwnds=[c.window_handle for c in self._clients],
            account_label=self._account_label,
            launching=self._launches.in_flight(),
        )

    def _sync_tiles(self) -> None:
        """Hand the game view the current tile set.

        Called on every client-loop tick rather than only when the client list changes: a
        window reaching character select changes no client list at all — it is not hooked —
        and it is exactly the window this exists to put on screen. ``sync_tiles`` is a
        no-op when nothing moved, so the common tick costs one dict comparison.
        """
        try:
            self._game_view.sync_tiles(self.tile_slots())
        except Exception:
            logger.exception("Syncing the game view tiles failed")

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def _run_recovery_queue(self) -> None:
        """Bring every pending slot back, then resume the FSM once.

        Slots are drained in batches rather than one at a time: a scheduled relog closes
        the whole fleet, and relaunching four clients in turn costs four full launches --
        spawn, two loading screens, character select, load-in -- where one batch costs
        roughly one. See ``launch_accounts``.

        The FSM stays paused for the entire duration. Once every pending recovery has
        completed successfully, the FSM is jumped to recovery_state and resumed exactly
        once.
        """
        try:
            await self._settle_recovery_batch()
            while self._pending_recovery:
                batch = dict(self._pending_recovery)
                try:
                    await self._reconnect_clients(batch)
                except Exception:
                    logger.exception(
                        "Unhandled error recovering slot(s) %s — stopping bot",
                        ", ".join(str(pos) for pos in sorted(batch)),
                    )
                    self._pending_recovery.clear()
                    if self._machine:
                        self._machine.stop()
                    return
                for orig_pos in batch:
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

    async def _settle_recovery_batch(self) -> None:
        """Wait for a relog in flight to finish closing, so its clients recover together.

        Only a relog closes several clients on purpose, and it is the one case where the
        batch is worth waiting for: the closes are issued together but each window is only
        noticed missing on the next ``_client_loop`` poll, so starting on the first arrival
        would relaunch the fleet one client at a time. A crash queues whatever it queues
        and is not waited on — there is nothing more coming.

        Bounded, and never the difference between recovering and not: if the relog is still
        closing when the budget runs out, the queue proceeds with what it has and picks up
        the stragglers on its next pass.
        """
        if not self._relog_in_progress:
            return
        loop = asyncio.get_event_loop()
        deadline = loop.time() + RECOVERY_BATCH_SETTLE
        while self._relog_in_progress and loop.time() < deadline:
            await asyncio.sleep(CLIENT_DETECT_INTERVAL)
        # The last process to die is still one poll away from being noticed missing.
        await asyncio.sleep(CLIENT_DETECT_INTERVAL * 2)

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

    def _recovery_target(self, orig_pos: int, username: str) -> "_RecoveryTarget | None":
        """Everything needed to relaunch one slot, or None if it cannot be relaunched.

        None means the machine has already been stopped and the user told why: a bot
        mid-run cannot continue a player short, and no stored password is not something
        this can drive its way out of.
        """
        machine = self._machine
        password = CredentialStore.retrieve(username)
        if not password:
            logger.error("No stored password for '%s' — stopping bot", username)
            self.recovery_error.emit(
                f"No stored password for account '{username}'.\n\n"
                "Open Settings → Accounts and save the password, then restart the bot."
            )
            if machine:
                machine.stop()
            return None

        display_name = CredentialStore.display_name(username, self._alias_for(username))
        return _RecoveryTarget(
            orig_pos=orig_pos,
            username=username,
            password=password,
            display_name=display_name,
            title=f"[{display_name}] Wizard101",
            # The last known resolution for this client, captured while the window was
            # alive. None falls back to the global setting, for a client never seen before.
            resolution=self._client_resolutions.get(username),
        )

    async def _reconnect_clients(self, slots: dict[int, str]) -> None:
        """Relaunch these slots together and put each client back where it was.

        The screens between auto-login and in-game are driven by drive_client_in, the same
        path a normal launch takes — see launcher/auto_select.py for why that has to be
        done blind. Recovery differs only in what failure means: a bot mid-run cannot
        continue a player short, so a drive that gives up stops the machine.

        The clients go out as one batch, and their drives run concurrently. That is the
        launcher's ordinary shape rather than a liberty taken here — a launch profile has
        always advanced its whole team at once, and auto-select posts its keystrokes with
        ``PostMessageW``, so no drive needs focus or competes with another for it.

        One exception: the game reads its resolution from a single config.xml at startup,
        so clients that want different resolutions cannot share a batch. They are grouped
        by resolution and each group is launched in turn. A fleet normally shares one.
        """
        from bartlebot.launcher.launch_profile import launch_accounts  # noqa: PLC0415

        machine = self._machine
        if machine is None:
            return

        targets: list[_RecoveryTarget] = []
        for orig_pos, username in sorted(slots.items()):
            target = self._recovery_target(orig_pos, username)
            if target is None:
                # Nothing is launched when any slot is unrecoverable: the run is over
                # either way, and half a fleet relaunched is worse than none.
                return
            targets.append(target)
        if not targets:
            return

        auto_select_on = SettingsManager.instance().auto_select_character
        if not auto_select_on:
            # The user has opted out of automatic character selection, so recovery waits
            # for them rather than pressing anything — but it still has to wait, since the
            # run cannot resume a player short.
            self._warn_awaiting_manual_login(targets)

        reached: dict[str, bool] = {}

        async def _drive(title: str) -> bool:
            ok = await self.drive_client_in(title)
            reached[title] = ok
            return ok

        for resolution, group in _group_by_resolution(targets):
            logger.info(
                "Relaunching %d player(s) for recovery: %s",
                len(group),
                ", ".join(f"'{t.display_name}' at slot {t.orig_pos}" for t in group),
            )
            try:
                await launch_accounts(
                    [(t.username, t.password, t.display_name) for t in group],
                    resolution=resolution,
                    # Handed the drive either way: with auto-select off it presses nothing
                    # that picks a character, it just clears the loading screens so the
                    # user arrives at a character-select screen rather than a black one.
                    drive=_drive,
                )
            except Exception:
                logger.exception(
                    "Failed to launch account(s) %s — stopping bot",
                    ", ".join(f"'{t.display_name}'" for t in group),
                )
                machine.stop()
                return

        if not auto_select_on:
            # The drive stopped at character select, so nothing above answered whether the
            # client got in — wait every window out at once rather than in turn.
            results = await asyncio.gather(*[self.await_client_in(t.title) for t in targets])
            reached = {t.title: ok for t, ok in zip(targets, results)}

        missing = [t for t in targets if not reached.get(t.title)]
        if missing:
            logger.error(
                "Timed out waiting for reconnected client(s) %s — stopping bot",
                ", ".join(f"'{t.display_name}'" for t in missing),
            )
            machine.stop()
            return

        for target in targets:
            logger.info(
                "Client '%s' recovered and in-game, slot %d restored",
                target.display_name,
                target.orig_pos,
            )
            # Restore the slot so the next _client_loop rebuild puts the client back in
            # order. machine.clients is kept in sync by _client_loop automatically.
            self._client_order[target.orig_pos] = target.username

    def _warn_awaiting_manual_login(self, targets: list["_RecoveryTarget"]) -> None:
        """Say once, for the whole batch, that these windows are waiting to be logged in.

        One message rather than one per client: with auto-select off a fleet relog would
        otherwise raise a dialog per player, each saying the same thing.
        """
        names = ", ".join(f"'{t.display_name}'" for t in targets)
        logger.warning(
            "Auto-select is off — %s %s been relaunched and %s waiting at character select. Log in to resume the run.",
            names,
            "have" if len(targets) > 1 else "has",
            "are" if len(targets) > 1 else "is",
        )
        self.recovery_error.emit(
            f"{names} {'have' if len(targets) > 1 else 'has'} been relaunched and "
            f"{'are' if len(targets) > 1 else 'is'} waiting at character select.\n\n"
            "Log in to resume the run, or turn on Settings → Bot → "
            "Automatically select a character to have this done for you."
        )

    # ------------------------------------------------------------------
    # Relogging
    # ------------------------------------------------------------------

    def _start_relog_watch(self) -> None:
        """Begin watching for a due relog. Called at the start of every run."""
        self._stop_relog_watch()
        self._relog_watch_task = asyncio.create_task(self._relog_watch_loop())

    def _stop_relog_watch(self) -> None:
        """Cancel the relog watch. Called when the run ends, and before starting a new one."""
        if self._relog_watch_task is not None:
            self._relog_watch_task.cancel()
            self._relog_watch_task = None

    async def _relog_watch_loop(self) -> None:
        """Relog the fleet on a timer, and any one client running out of GDI objects.

        Both triggers live here rather than in loops of their own because they ask the same
        question — is now a safe moment to take clients away from the FSM — and because
        either kind of relog resets the timer. The policy itself is in ``bartlebot.relog``;
        this supplies only the clock and the readings.

        Never raises: it outlives every node, and an exception escaping here would take the
        timer with it for the rest of the run.
        """
        loop = asyncio.get_event_loop()
        last_relog_at = loop.time()
        while True:
            try:
                await asyncio.sleep(RELOG_WATCH_INTERVAL)
                machine = self._machine
                if machine is None or machine.is_stopped or self._relog_in_progress:
                    continue

                now = loop.time()
                interval = SettingsManager.instance().relog_interval_hours
                if relog.relog_due(now, last_relog_at, interval):
                    logger.info(
                        "Scheduled relog due — %.1f hours since the last one",
                        (now - last_relog_at) / relog.SECONDS_PER_HOUR,
                    )
                    if await self.relog_clients(list(self._clients), reason="scheduled"):
                        last_relog_at = loop.time()
                    continue

                starved = self._clients_near_gdi_quota()
                if starved and await self.relog_clients(starved, reason="GDI quota"):
                    last_relog_at = loop.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Relog watch loop error")

    def _clients_near_gdi_quota(self) -> list:
        """Live clients close enough to the 10,000 GDI object cap to need a relog (#164).

        One client has been seen reaching the cap exactly, after which every GDI allocation
        in that process fails. A failed read answers 0, which is never a trigger — a live
        client always holds thousands.
        """
        starved = []
        for client in list(self._clients):
            count = gdi_object_count(client.process_id)
            if relog.gdi_relog_due(count):
                logger.warning(
                    "Client hwnd=%d holds %d GDI objects of a %d quota — relogging it before allocations start failing",
                    client.window_handle,
                    count,
                    relog.GDI_QUOTA,
                )
                starved.append(client)
        return starved

    async def relog_clients(self, targets: list, *, reason: str) -> bool:
        """Close these clients on purpose so recovery brings them back fresh.

        The cycle *is* the crash-recovery path, deliberately: close the process and let
        ``remove_dead_clients()`` -> ``_on_clients_removed`` -> ``_run_recovery_queue`` do
        everything after that. Nothing here relaunches anything.

        Returns whether the clients were actually closed. False means the cycle was refused
        or abandoned, and the caller should leave its timer alone and try again later.

        The ordering is not negotiable:

        1. **Refuse unless every client could be recovered.** A crash happens whether or not
           recovery can cope with it; a scheduled relog is a healthy run being broken on
           purpose, so it must not start unless it is known to be reversible.
           ``_reconnect_clients`` stops the bot on a login failure — right for a crash, and
           unacceptable as a failure mode this feature introduces.
        2. **Wait for a real node boundary**, not merely for ``is_paused``. See
           ``BotMachine.wait_until_parked``.
        3. **Close, never unhook.** Killing the process sidesteps the fixed sleep in
           ``HookHandler._rewrite_autobot``, which is a hazard on exactly the lagging
           clients this exists for.
        """
        machine = self._machine
        if machine is None or machine.is_stopped or not targets:
            return False
        if self._relog_in_progress or self._recovery_in_progress or self._pending_recovery:
            logger.debug("Skipping %s relog — a recovery or relog is already in flight", reason)
            return False

        gaps = self.recovery_gaps()
        if gaps:
            for position, gap in gaps:
                logger.warning(
                    "Skipping %s relog — Player %d could not be brought back: %s",
                    reason,
                    position,
                    gap,
                )
            return False

        self._relog_in_progress = True
        try:
            machine.pause()
            if not await machine.wait_until_parked(RELOG_PARK_TIMEOUT):
                logger.warning(
                    "Skipping %s relog — the bot did not reach a node boundary within %.0fs",
                    reason,
                    RELOG_PARK_TIMEOUT,
                )
                machine.resume()
                return False
            if machine.is_ambient_combat_active:
                # Parked is not idle: the ambient watcher runs alongside the run loop and
                # can be mid-fight on every client. Try again next tick rather than closing
                # a client out of a duel.
                logger.info("Deferring %s relog — ambient combat is still being handled", reason)
                machine.resume()
                return False

            hwnds = [c.window_handle for c in targets]
            machine.suspend_health_checks_for(hwnds)
            self._relogging_hwnds.update(hwnds)
            logger.info("Relogging %d player(s) — reason: %s", len(targets), reason)

            # Closed together rather than in turn: force_close_client waits out the
            # process, so a fleet closed one at a time spends that wait per client and
            # spreads the removals across several _client_loop polls -- which is what
            # decides whether they come back as one batch or one at a time.
            results = await asyncio.gather(
                *[force_close_client(c.window_handle, pid=c.process_id) for c in targets],
                return_exceptions=True,
            )
            survivors = [c for c, ok in zip(targets, results) if ok is not True]
            for client, result in zip(targets, results):
                if isinstance(result, BaseException):
                    logger.error("Closing client hwnd=%d for a relog raised", client.window_handle, exc_info=result)
            if survivors:
                # Some of the fleet is already gone and these will not die. Recovery brings
                # back what it can; saying so is what stops the log reading as though the
                # cycle completed.
                logger.error(
                    "Relog incomplete — client(s) %s would not close, so the fleet is part-way "
                    "through a relog. Recovery will restore the players that did close.",
                    ", ".join(f"hwnd={c.window_handle}" for c in survivors),
                )
                return False
            return True
        finally:
            self._relog_in_progress = False
