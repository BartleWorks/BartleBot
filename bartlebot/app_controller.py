import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from wizwalker.errors import MemoryReadError
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.utils import get_window_title

from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.credentials import CredentialStore
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.main_window import MainWindow
from bartlebot.logging_setup import add_gui_callback, remove_gui_callback
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS
from bartlebot.settings_manager import SettingsManager


class AppController(QObject):
    """Owns all application-level state and wires the GUI to the bot engine."""

    # Emitted whenever the live client list changes (add, remove, or reorder).
    # Connected to GameView.renumber_tiles and ClientSection.set_clients.
    clients_changed = pyqtSignal(list)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        from bartlebot.gui.theme import THEME  # noqa: PLC0415

        self._app.setStyleSheet(THEME.global_stylesheet())
        self._handler = WizSprinter()
        self._clients: list = []
        self._current_machine: BotMachine | None = None
        self._loaded_config: BotConfig | None = None
        self._last_save_path: str | None = None

        # Client recovery state
        # Maps window_handle → username for all clients seen this session.
        # Persists after process exit so we can identify removed clients.
        self._hwnd_account_map: dict[int, str] = {}
        # Ordered list of usernames representing the desired client slot ordering.
        # None marks a slot currently being recovered.
        self._client_order: list[str | None] = []
        # Slots pending recovery: original_position → username
        self._pending_recovery: dict[int, str] = {}
        self._recovery_in_progress: bool = False

        self._window = MainWindow()
        self._wire()
        self._window.show()

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _wire(self) -> None:
        w = self._window

        # File operations — connect directly to exposed QAction signals
        w.action_new.triggered.connect(lambda: self._new_bot())
        w.action_open.triggered.connect(lambda: self._open_bot())
        w.action_save.triggered.connect(lambda: self._save_bot())
        w.action_save_as.triggered.connect(lambda: self._save_bot_as())
        w.open_recent_requested.connect(self._open_bot)

        # Canvas signals
        w.bot_canvas.config_changed.connect(self._on_config_changed)
        w.bot_canvas.mode_changed.connect(self._on_mode_changed)
        w.bot_canvas.run_requested.connect(lambda: asyncio.create_task(self._start_bot()))
        w.bot_canvas.pause_requested.connect(self._pause_bot)
        w.bot_canvas.resume_requested.connect(self._resume_bot)
        w.bot_canvas.stop_button.clicked.connect(self._stop_bot)
        w.bot_canvas.save_done.connect(self._save_bot)
        w.bot_canvas.set_clients_getter(lambda: self._clients)

        # Left sidebar config changes
        w.left_sidebar.config_changed.connect(self._on_config_changed)

        # Client list changes → game view renumber + sidebar rebuild
        self.clients_changed.connect(w.game_view.renumber_tiles)
        self.clients_changed.connect(w.right_sidebar.clients.set_clients)

        # Reorder buttons in sidebar → swap client positions
        w.right_sidebar.clients.swap_requested.connect(self.swap_clients)

        w.update_recent_menu(SettingsManager.instance().recent_bot_files)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        asyncio.create_task(self._client_loop())
        quit_event = asyncio.Event()
        self._app.aboutToQuit.connect(quit_event.set)
        await quit_event.wait()

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _new_bot(self) -> None:
        config = BotConfig(
            name="New Bot",
            initial="state_1",
            states=[StateModel(id="state_1", node_type="go_to_zone")],
            transitions=[],
        )
        self._loaded_config = config
        self._last_save_path = None
        self._window.set_bot_name(config.name, "Untitled")
        self._window.bot_canvas.setup(config)
        self._window.left_sidebar.set_config(config)
        self._window.bot_canvas.enter_edit_mode()
        self._window.set_save_enabled(True)
        self._window.set_start_enabled(self._start_enabled())

    def _open_bot(self, path: str | None = None) -> None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self._window, "Open Bot", "", "Bot Files (*.json)")
        if not path:
            return
        try:
            config = BotConfig.load(path)
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to load bot", str(e))
            return
        self._loaded_config = config
        self._last_save_path = path
        self._window.set_bot_name(config.name, Path(path).name)
        self._window.bot_canvas.setup(config)
        self._window.left_sidebar.set_config(config)
        self._window.set_save_enabled(True)
        self._window.set_start_enabled(self._start_enabled())
        self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))

    def _save_bot(self) -> None:
        if self._loaded_config is None:
            return
        if self._last_save_path is None:
            self._save_bot_as()
            return
        try:
            self._loaded_config.save(self._last_save_path)
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to save bot", str(e))

    def _save_bot_as(self) -> None:
        if self._loaded_config is None:
            return
        path, _ = QFileDialog.getSaveFileName(self._window, "Save Bot As", "", "Bot Files (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        try:
            self._loaded_config.save(path)
            self._last_save_path = path
            self._window.set_bot_name(self._loaded_config.name, Path(path).name)
            self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to save bot", str(e))

    # ------------------------------------------------------------------
    # Bot control
    # ------------------------------------------------------------------

    async def _start_bot(self) -> None:
        if not self._clients or self._loaded_config is None:
            return
        machine = BotMachine(
            clients=self._clients,
            config=self._loaded_config.to_machine_config(node_specs=NODE_SPECS),
            node_registry=NODE_REGISTRY,
            bot_config=self._loaded_config,
            on_state_change=self._on_machine_state_change,
            on_transition=self._on_machine_transition,
            on_drop=self._on_machine_drop,
            on_log=self._on_machine_log,
            on_client_disconnected=self._on_machine_client_disconnected,
        )
        # Reset recovery state for the new run
        self._pending_recovery.clear()
        self._recovery_in_progress = False
        self._current_machine = machine
        self._window.set_start_enabled(False)
        self._window.right_sidebar.drops.setup_clients(len(self._clients))
        self._window.right_sidebar.drops.clear_drops()
        add_gui_callback(self._window.bot_canvas.append_log_formatted)
        self._window.bot_canvas.set_running()
        try:
            await machine.run()
        finally:
            self._window.bot_canvas.set_idle()
            remove_gui_callback(self._window.bot_canvas.append_log_formatted)
            self._current_machine = None
            self._window.set_start_enabled(self._start_enabled())

    def _pause_bot(self) -> None:
        if self._current_machine:
            self._current_machine.pause()

    def _resume_bot(self) -> None:
        if self._current_machine:
            self._current_machine.resume()

    def _stop_bot(self) -> None:
        if self._current_machine:
            self._current_machine.stop()

    # ------------------------------------------------------------------
    # BotMachine callback receivers
    # ------------------------------------------------------------------

    def _on_machine_state_change(self, state_id: str) -> None:
        self._window.set_fsm_state(state_id)

    def _on_machine_transition(self, prev: str, result: str, next_: str) -> None:
        self._window.bot_canvas.on_transition(prev, result, next_)

    def _on_machine_drop(self, item: str, qty: int, client_idx: int) -> None:
        self._window.right_sidebar.drops.add_drop(item, qty, client_idx)

    def _on_machine_log(self, msg: str, level: int) -> None:
        self._window.bot_canvas.append_log(msg, level)

    def _on_machine_client_disconnected(self, hwnd: int) -> None:
        """Called by _client_status_watcher when a client becomes unresponsive.

        Force-closes the game window so that remove_dead_clients() detects the exit
        and triggers the reconnect procedure via _on_clients_removed.
        """
        import ctypes  # noqa: PLC0415

        logger.warning("Client hwnd=%d flagged unresponsive — sending WM_CLOSE", hwnd)
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_config_changed(self) -> None:
        self._window.set_save_enabled(True)
        if self._loaded_config is not None:
            self._window.left_sidebar.bot_info.refresh_states(self._loaded_config.states)
            self._window.bot_canvas.refresh_initial(self._loaded_config.initial)
            self._window.left_sidebar.options.set_config(self._loaded_config)

    def _on_mode_changed(self, mode: BotMode) -> None:
        self._window.left_sidebar.set_editable(mode == BotMode.EDIT)
        if mode == BotMode.RUN:
            self._window.set_start_enabled(self._start_enabled())

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
                    if self._current_machine:
                        self._current_machine.clients = self._clients
                    self.clients_changed.emit([])
                    self._window.set_start_enabled(False)
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
                    if self._current_machine:
                        self._current_machine.clients = self._clients
                    self.clients_changed.emit(self._clients)
                    self._window.set_start_enabled(self._start_enabled())

                await asyncio.sleep(1.0)

            except MemoryReadError:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                if self._current_machine:
                    self._current_machine.clients = self._clients
                self.clients_changed.emit([])
                self._window.set_start_enabled(False)
                await asyncio.sleep(1.0)
            except Exception:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                if self._current_machine:
                    self._current_machine.clients = self._clients
                self.clients_changed.emit([])
                self._window.set_start_enabled(False)
                logger.exception("Client loop error")
                await asyncio.sleep(3.0)

    def _refresh_account_map(self, live_clients: list) -> None:
        """For each live client with an unrecognised HWND, try to match window title to an account."""
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
                    break

    def _on_clients_removed(self, removed_hwnds: set) -> None:
        """Handle client removal: update _client_order and queue recovery if bot is running."""
        machine = self._current_machine
        for hwnd in removed_hwnds:
            username = self._hwnd_account_map.get(hwnd)
            if not username:
                continue
            orig_pos = next((i for i, u in enumerate(self._client_order) if u == username), None)
            if orig_pos is not None:
                self._client_order[orig_pos] = None
                if machine is not None and not machine._stop_flag.is_set():
                    self._pending_recovery[orig_pos] = username
                    logger.warning(
                        "Client at slot %d (account: %s) disconnected — queuing recovery",
                        orig_pos,
                        username,
                    )
        if self._pending_recovery:
            # Pause immediately to prevent FAILURE results from accumulating before
            # recovery resumes the machine. Idempotent if already paused.
            if machine is not None and not machine._stop_flag.is_set():
                machine.pause()
            if not self._recovery_in_progress:
                self._recovery_in_progress = True
                asyncio.create_task(self._run_recovery_queue())

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
                    self._window.game_view.unembed_client(idx)
                except Exception:
                    pass
        # Move clients that changed slot index
        for hwnd, new_idx in new_map.items():
            if hwnd in prev_map and prev_map[hwnd] != new_idx:
                try:
                    self._window.game_view.unembed_client(prev_map[hwnd])
                    self._window.game_view.embed_client(hwnd, new_idx)
                except Exception:
                    pass
        # Embed newly added clients
        for hwnd, idx in new_map.items():
            if hwnd not in prev_map:
                try:
                    self._window.game_view.embed_client(hwnd, idx)
                except Exception:
                    pass
        # Game view panel is always visible; no need to show/hide on client changes.

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
                    if self._current_machine:
                        self._current_machine.stop()
                self._pending_recovery.pop(orig_pos, None)

            # All pending clients are back in-game — jump to recovery state and resume.
            machine = self._current_machine
            if machine and not machine._stop_flag.is_set():
                recovery_state = self._loaded_config.recovery_state if self._loaded_config else None
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

        from bartlebot.launcher.launch_profile import launch_account  # noqa: PLC0415

        machine = self._current_machine
        if machine is None:
            return

        password = CredentialStore.retrieve(username)
        if not password:
            logger.error("No stored password for '%s' — stopping bot", username)
            machine.stop()
            return

        settings = SettingsManager.instance()
        accounts_meta = {a["username"]: a for a in settings.accounts}
        alias = accounts_meta.get(username, {}).get("alias") or None
        display_name = CredentialStore.display_name(username, alias)
        expected_title = f"[{display_name}] Wizard101"

        logger.info("Relaunching '%s' for recovery at slot %d", display_name, orig_pos)
        try:
            await launch_account(username, password, display_name)
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
                hwnd = ctypes.windll.user32.FindWindowW(None, expected_title)
                if hwnd:
                    ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYDOWN, _VK_RETURN, 0)
                    await asyncio.sleep(0.05)
                    ctypes.windll.user32.PostMessageW(hwnd, _WM_KEYUP, _VK_RETURN, 0)
                await asyncio.sleep(1.0)

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
            except asyncio.CancelledError:
                pass

        if new_client is None:
            logger.error("Timed out waiting for reconnected client '%s' — stopping bot", display_name)
            machine.stop()
            return

        logger.info("Client '%s' recovered and in-game, slot %d restored", display_name, orig_pos)

        # Restore the slot so the next _client_loop rebuild puts the client back in order.
        # machine.clients is kept in sync by _client_loop automatically.
        self._client_order[orig_pos] = username

    def swap_clients(self, idx_a: int, idx_b: int) -> None:
        """Swap the positions of two clients in the live list.

        This changes which client index bot logic routes to, updates game-view
        tile labels, and rebuilds the sidebar — without unhooking or re-hooking
        any WizWalker clients.
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
        self._window.right_sidebar.drops.swap_clients(idx_a, idx_b)
        self.clients_changed.emit(self._clients)

    def _start_enabled(self) -> bool:
        return bool(self._clients) and self._loaded_config is not None
