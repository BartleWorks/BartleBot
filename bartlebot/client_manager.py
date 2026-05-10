import asyncio
import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal
from wizwalker.errors import MemoryReadError
from wizwalker.utils import get_window_title

from bartlebot.credentials import CredentialStore
from bartlebot.nodes.node_helpers import click_by_path, is_enabled_by_path, poll_until
from bartlebot.nodes.paths import BACKPACK_CLOSE_BUTTON_PATH, CROWNS_SHOP_PATH
from bartlebot.nodes.timing import TRIGGER_TIMEOUT
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


class ClientManager(QObject):
    """Owns client detection, slot ordering, game-view embedding, and recovery.

    Signals:
        clients_changed: Emitted whenever the live client list changes.
        recovery_error:  Emitted when recovery cannot proceed due to a user-fixable error.
    """

    clients_changed = pyqtSignal(list)
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
        # Maps window_handle → username for all clients seen this session.
        self._hwnd_account_map: dict[int, str] = {}
        # Desired slot ordering; None marks a slot currently being recovered.
        self._client_order: list[str | None] = []
        # Slots pending recovery: original_position → username
        self._pending_recovery: dict[int, str] = {}
        self._recovery_in_progress: bool = False
        # Last known window resolution per username; used by recovery to restore original size.
        self._client_resolutions: dict[str, tuple[int, int]] = {}
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

    # ------------------------------------------------------------------
    # Client loop
    # ------------------------------------------------------------------

    async def _client_loop(self) -> None:
        while True:
            try:
                self._handler.remove_dead_clients()
                new_clients = self._handler.get_new_clients()
                if new_clients:
                    try:
                        await asyncio.gather(*[c.activate_hooks() for c in new_clients])
                    except Exception:
                        for c in new_clients:
                            if c in self._handler.clients:
                                self._handler.clients.remove(c)
                            if c.window_handle in self._handler._managed_handles:
                                self._handler._managed_handles.remove(c.window_handle)
                        logger.exception("Client activation failed")
                        await asyncio.sleep(3.0)
                        continue

                clients = list(self._handler.clients)
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
                    await asyncio.sleep(3.0)
                    continue

                # Update the account map for any clients whose titles are now known.
                self._refresh_account_map(clients)

                # Compare by HWND set to detect additions and removals.
                new_hwnd_set = {c.window_handle for c in clients}
                cur_hwnd_set = {c.window_handle for c in self._clients}
                if new_hwnd_set != cur_hwnd_set:
                    # Detect removals before rebuilding the list.
                    removed_hwnds = cur_hwnd_set - new_hwnd_set
                    if removed_hwnds:
                        self._on_clients_removed(removed_hwnds)

                    # Register any newly added known clients in _client_order.
                    for c in clients:
                        if c.window_handle not in cur_hwnd_set:
                            username = self._hwnd_account_map.get(c.window_handle)
                            if username:
                                recovery_pos = next(
                                    (pos for pos, u in self._pending_recovery.items() if u == username),
                                    None,
                                )
                                if recovery_pos is not None:
                                    # Recovering client — restore to original slot rather than appending.
                                    self._client_order[recovery_pos] = username
                                elif username not in self._client_order:
                                    self._client_order.append(username)

                    ordered = self._build_ordered_clients(clients)
                    self._sync_embedded_clients(self._clients, ordered)
                    self._clients = ordered
                    if self._machine:
                        self._machine.set_clients(self._clients)
                    self.clients_changed.emit(self._clients)

                await asyncio.sleep(1.0)

            except MemoryReadError:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                if self._machine:
                    self._machine.set_clients(self._clients)
                self.clients_changed.emit([])
                await asyncio.sleep(1.0)
            except Exception:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                if self._machine:
                    self._machine.set_clients(self._clients)
                self.clients_changed.emit([])
                logger.exception("Client loop error")
                await asyncio.sleep(3.0)

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
        for hwnd in removed_hwnds:
            username = self._hwnd_account_map.get(hwnd)
            if not username:
                continue
            orig_pos = next((i for i, u in enumerate(self._client_order) if u == username), None)
            if orig_pos is not None:
                self._client_order[orig_pos] = None
                if machine is not None and not machine.is_stopped:
                    self._pending_recovery[orig_pos] = username
                    logger.warning(
                        "Client at slot %d (account: %s) disconnected — queuing recovery",
                        orig_pos,
                        username,
                    )
        if self._pending_recovery:
            # Pause immediately to prevent FAILURE results from accumulating before
            # recovery resumes the machine. Idempotent if already paused.
            if machine is not None and not machine.is_stopped:
                machine.pause()
            if not self._recovery_in_progress:
                self._recovery_in_progress = True
                self._recovery_task = asyncio.create_task(self._run_recovery_queue())

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
        prev_map = {c.window_handle: i for i, c in enumerate(prev_clients)}
        new_map = {c.window_handle: i for i, c in enumerate(new_clients)}
        # Unembed removed clients
        for hwnd, idx in prev_map.items():
            if hwnd not in new_map:
                try:
                    self._game_view.unembed_client(idx)
                except Exception:
                    pass
        # Move clients that changed slot index
        for hwnd, new_idx in new_map.items():
            if hwnd in prev_map and prev_map[hwnd] != new_idx:
                try:
                    self._game_view.unembed_client(prev_map[hwnd])
                    self._game_view.embed_client(hwnd, new_idx)
                except Exception:
                    pass
        # Embed newly added clients
        for hwnd, idx in new_map.items():
            if hwnd not in prev_map:
                try:
                    self._game_view.embed_client(hwnd, idx)
                except Exception:
                    pass

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

            # All pending clients are back in-game — jump to recovery state and resume.
            machine = self._machine
            if machine and not machine.is_stopped:
                recovery_state = self._get_recovery_state()
                if recovery_state:
                    machine.force_state(recovery_state)
                    machine.resume()
                    logger.info("All clients recovered — FSM resuming at %r", recovery_state)
                else:
                    logger.warning(
                        "Bot config has no recovery_state set — stopping. "
                        "Set recovery_state in your bot config to enable automatic recovery."
                    )
                    machine.stop()
        finally:
            self._recovery_in_progress = False

    async def _reconnect_client(self, orig_pos: int, username: str) -> None:
        """Relaunch a disconnected client, navigate to in-game, then resume the FSM.

        WizWalker hooks can only be activated once the character is in-game (not during
        loading screens or the character select menu). To advance through those screens
        without hooks, we spam the Enter key via Win32 PostMessageW until the client
        appears in self._clients (meaning _client_loop successfully activated hooks,
        implying the character is now in a game zone).
        """
        import ctypes  # noqa: PLC0415
        import ctypes.wintypes  # noqa: PLC0415

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

        # Spam Enter key into the game window to advance through login loading screens
        # and the character select menu. Hooks only activate once in-game, so we keep
        # pressing until the client appears in self._clients.
        _VK_RETURN = 0x0D
        _WM_KEYDOWN = 0x0100
        _WM_KEYUP = 0x0101
        _enter_stop = asyncio.Event()

        async def _enter_spam() -> None:
            while not _enter_stop.is_set():
                try:
                    hwnd = ctypes.windll.user32.FindWindowW(None, expected_title)
                    if hwnd:
                        ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYDOWN, _VK_RETURN, 0)
                        await asyncio.sleep(0.05)
                        ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYUP, _VK_RETURN, 0)
                except Exception:
                    logger.warning("Enter-spam: ctypes call failed, will retry", exc_info=True)
                await asyncio.sleep(5.0)

        spam_task = asyncio.create_task(_enter_spam())

        # Wait for _client_loop to hook the new instance (only succeeds once in-game)
        new_client = None
        deadline = asyncio.get_event_loop().time() + 120.0
        try:
            while asyncio.get_event_loop().time() < deadline:
                for c in self._clients:
                    try:
                        if get_window_title(c.window_handle) == expected_title:
                            new_client = c
                            break
                    except Exception:
                        pass
                if new_client:
                    break
                await asyncio.sleep(1.0)
        finally:
            _enter_stop.set()
            spam_task.cancel()
            try:
                await spam_task
            except (asyncio.CancelledError, Exception):
                pass

        if new_client is None:
            logger.error("Timed out waiting for reconnected client '%s' — stopping bot", display_name)
            machine.stop()
            return

        logger.info("Client '%s' recovered and in-game, slot %d restored", display_name, orig_pos)

        _VK_ESCAPE = 0x1B
        hwnd = new_client.window_handle

        async def _send_esc() -> None:
            ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYDOWN, _VK_ESCAPE, 0)
            await asyncio.sleep(0.05)
            ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYUP, _VK_ESCAPE, 0)

        # Close any window accidentally opened by Enter spam (e.g. chat box).
        # ESC always opens the settings window after closing chat, so: fire ESC until
        # settings appears, then fire ESC until settings disappears.
        logger.info("Cleaning up any window opened by Enter spam on client '%s'", display_name)
        for _ in range(5):
            if await is_enabled_by_path(new_client, BACKPACK_CLOSE_BUTTON_PATH):
                break
            await _send_esc()
            await asyncio.sleep(2.0)
        for _ in range(5):
            if not await is_enabled_by_path(new_client, BACKPACK_CLOSE_BUTTON_PATH):
                break
            await _send_esc()
            await asyncio.sleep(2.0)

        # Dismiss the crowns shop if it opened automatically on login.
        for _ in range(5):
            if not await is_enabled_by_path(new_client, CROWNS_SHOP_PATH):
                break
            await _send_esc()
            if not await poll_until(
                lambda: is_enabled_by_path(new_client, BACKPACK_CLOSE_BUTTON_PATH),
                timeout=TRIGGER_TIMEOUT,
            ):
                continue
            await click_by_path(new_client, BACKPACK_CLOSE_BUTTON_PATH)
            await poll_until(
                lambda: not is_enabled_by_path(new_client, BACKPACK_CLOSE_BUTTON_PATH),
                timeout=TRIGGER_TIMEOUT,
            )

        # Restore the slot so the next _client_loop rebuild puts the client back in order.
        # machine.clients is kept in sync by _client_loop automatically.
        self._client_order[orig_pos] = username
