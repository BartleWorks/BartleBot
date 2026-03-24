import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from wizwalker.errors import MemoryReadError
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, StateModel
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
        )
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
                    self._sync_embedded_clients(self._clients, [])
                    self._clients = []
                    self.clients_changed.emit([])
                    self._window.set_start_enabled(False)
                    await asyncio.sleep(3.0)
                    continue

                # Compare by HWND set, not order, so user-initiated reorders are
                # not overwritten by the loop seeing a different list order.
                new_hwnd_set = {c.window_handle for c in clients}
                cur_hwnd_set = {c.window_handle for c in self._clients}
                if new_hwnd_set != cur_hwnd_set:
                    # Preserve our ordering for retained clients; append new ones.
                    retained = [c for c in self._clients if c.window_handle in new_hwnd_set]
                    added = [c for c in clients if c.window_handle not in cur_hwnd_set]
                    new_clients = retained + added
                    self._sync_embedded_clients(self._clients, new_clients)
                    self._clients = new_clients
                    self.clients_changed.emit(self._clients)
                    self._window.set_start_enabled(self._start_enabled())

                await asyncio.sleep(1.0)

            except MemoryReadError:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                self.clients_changed.emit([])
                self._window.set_start_enabled(False)
                await asyncio.sleep(1.0)
            except Exception:
                self._sync_embedded_clients(self._clients, [])
                self._clients = []
                self.clients_changed.emit([])
                self._window.set_start_enabled(False)
                logger.exception("Client loop error")
                await asyncio.sleep(3.0)

    def _sync_embedded_clients(self, prev_clients: list, new_clients: list) -> None:
        prev_handles = {c.window_handle: i for i, c in enumerate(prev_clients)}
        new_handles = {c.window_handle: i for i, c in enumerate(new_clients)}
        for hwnd, idx in prev_handles.items():
            if hwnd not in new_handles:
                try:
                    self._window.game_view.unembed_client(idx)
                except Exception:
                    pass
        for hwnd, idx in new_handles.items():
            if hwnd not in prev_handles:
                try:
                    self._window.game_view.embed_client(hwnd, idx)
                except Exception:
                    pass
        # Game view panel is always visible; no need to show/hide on client changes.

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
        self._window.right_sidebar.drops.swap_clients(idx_a, idx_b)
        self.clients_changed.emit(self._clients)

    def _start_enabled(self) -> bool:
        return bool(self._clients) and self._loaded_config is not None
