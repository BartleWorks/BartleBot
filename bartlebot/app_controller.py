import asyncio
import logging
from datetime import datetime
from pathlib import Path

from wakepy import ActivationError, keep

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from wizwalker.extensions.scripting.deck_builder import DeckBuilder
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.client_manager import ClientManager
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.main_window import MainWindow
from bartlebot.logging_setup import add_gui_callback, remove_gui_callback
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS
from bartlebot.settings_manager import SettingsManager


class AppController(QObject):
    """Owns all application-level state and wires the GUI to the bot engine."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._app.setStyle("Fusion")
        self._apply_theme()
        self._handler = WizSprinter()
        self._current_machine: BotMachine | None = None
        self._loaded_config: BotConfig | None = None
        self._last_save_path: str | None = None

        # Long-lived asyncio task — held here to prevent GC (event loop keeps weak refs only).
        self._bot_task: asyncio.Task | None = None

        # Auto-save state
        self._has_unsaved_changes: bool = False
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(False)
        self._auto_save_timer.timeout.connect(self._do_autosave)

        self._window = MainWindow()
        self._client_manager = ClientManager(
            handler=self._handler,
            game_view=self._window.game_view,
            drops_widget=self._window.right_sidebar.drops,
            get_recovery_state=lambda: self._loaded_config.recovery_state if self._loaded_config else None,
        )
        self._wire()
        self._window.show()
        self._reconfigure_autosave_timer()
        self._check_crash_recovery()

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _wire(self) -> None:
        w = self._window

        # File operations — connect directly to exposed QAction signals
        w.action_new.triggered.connect(lambda: self._new_bot())
        w.action_open.triggered.connect(lambda: self._open_bot())
        w.action_close.triggered.connect(lambda: self._close_bot())
        w.action_save.triggered.connect(lambda: self._save_bot())
        w.action_save_as.triggered.connect(lambda: self._save_bot_as())
        w.open_recent_requested.connect(self._open_bot)

        # Canvas signals
        w.bot_canvas.config_changed.connect(self._on_config_changed)
        w.bot_canvas.mode_changed.connect(self._on_mode_changed)
        w.bot_canvas.run_requested.connect(self._on_run_requested)
        w.bot_canvas.pause_requested.connect(self._pause_bot)
        w.bot_canvas.resume_requested.connect(self._resume_bot)
        w.bot_canvas.stop_button.clicked.connect(self._stop_bot)
        w.bot_canvas.save_done.connect(self._save_bot)
        w.bot_canvas.set_clients_getter(lambda: self._client_manager.clients)

        def _zone_getter() -> list[str]:
            settings = SettingsManager.instance()
            current_zones: list[str] = []
            for widget in w.right_sidebar.clients._widgets:
                zone = getattr(widget, "_last_zone", "")
                if zone and zone != "—" and zone not in current_zones:
                    current_zones.append(zone)
                    settings.add_recent_zone(zone)
            recent = [z for z in settings.recent_zones if z not in current_zones]
            return current_zones + recent

        w.bot_canvas.set_zone_getter(_zone_getter)

        # Left sidebar config changes
        w.left_sidebar.config_changed.connect(self._on_config_changed)

        # Deck import requests from Combat tab
        w.left_sidebar.combat_tab.import_deck_requested.connect(self._on_import_deck_requested)

        # Client list changes → game view renumber + start button state
        self._client_manager.clients_changed.connect(w.game_view.renumber_tiles)
        self._client_manager.clients_changed.connect(self._on_clients_changed)

        # All clients (active + unhooked) → sidebar rebuild
        self._client_manager.all_clients_changed.connect(
            lambda active, unhooked: w.right_sidebar.clients.set_all_clients(active, unhooked)
        )

        # Reorder buttons in sidebar → swap client positions
        w.right_sidebar.clients.swap_requested.connect(self._client_manager.swap_clients)

        # Unhook/rehook buttons in sidebar → ClientManager
        w.right_sidebar.clients.unhook_requested.connect(
            lambda idx: asyncio.ensure_future(self._client_manager.unhook_client(idx))
        )
        w.right_sidebar.clients.rehook_requested.connect(
            lambda hwnd: asyncio.ensure_future(self._client_manager.rehook_client(hwnd))
        )

        self._client_manager.recovery_error.connect(
            lambda msg: QMessageBox.critical(self._window, "Client Recovery Failed", msg)
        )

        w.update_recent_menu(SettingsManager.instance().recent_bot_files)
        w.settings_applied.connect(self._reconfigure_autosave_timer)
        w.settings_applied.connect(w.left_sidebar.log_panel.apply_settings)
        w.settings_applied.connect(self._apply_theme)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._client_manager.start()
        quit_event = asyncio.Event()
        self._app.aboutToQuit.connect(quit_event.set)
        await quit_event.wait()

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _new_bot(self) -> None:
        self._delete_autosave()
        self._has_unsaved_changes = False
        config = BotConfig(
            name="New Bot",
            initial="state_1",
            recovery_state="state_1",
            states=[StateModel(id="state_1", node_type="teleport_to_world_hub")],
            transitions=[],
        )
        self._loaded_config = config
        self._last_save_path = None
        self._window.set_bot_name(config.name, "Untitled")
        self._window.bot_canvas.setup(config)
        self._window.left_sidebar.set_config(config)
        self._window.bot_canvas.enter_edit_mode()
        self._window.set_save_enabled(True)
        self._window.set_close_enabled(True)
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
        old_save_path = self._last_save_path
        self._delete_autosave(old_save_path)
        self._has_unsaved_changes = False
        self._loaded_config = config
        self._last_save_path = path
        self._window.set_bot_name(config.name, Path(path).name)
        self._window.bot_canvas.setup(config)
        self._apply_bot_defaults(config)
        self._window.left_sidebar.set_config(config)
        self._window.set_save_enabled(True)
        self._window.set_close_enabled(True)
        self._window.set_start_enabled(self._start_enabled())
        self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        self._window.bot_canvas.refresh_placeholder()

    def _apply_bot_defaults(self, config: BotConfig) -> None:
        """Apply user-configured defaults to a freshly loaded BotConfig.

        - Safety Delay (bot option): replace its value with the configured default.
        - Quicksell params: overwrite sell_* params on every quicksell state.
        """
        sm = SettingsManager.instance()

        delay = sm.default_safety_delay
        config.options = [
            opt.model_copy(update={"default": delay}) if opt.name == "Safety Delay" else opt for opt in config.options
        ]

        sell_pets = sm.get_general("default_quicksell_sell_pets", default=True)
        sell_socket_wrenches = sm.get_general("default_quicksell_sell_socket_wrenches", default=False)
        sell_skeleton_keys = sm.get_general("default_quicksell_sell_skeleton_keys", default=False)
        for state in config.states:
            if state.node_type == "quicksell":
                state.params["sell_pets"] = sell_pets
                state.params["sell_socket_wrenches"] = sell_socket_wrenches
                state.params["sell_skeleton_keys"] = sell_skeleton_keys

    def _close_bot(self) -> None:
        if self._loaded_config is None:
            return
        if self._has_unsaved_changes:
            reply = QMessageBox.question(
                self._window,
                "Close Bot",
                "You have unsaved changes. Close without saving?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Save:
                self._save_bot()
                if self._has_unsaved_changes:  # save was cancelled or failed
                    return
        self._delete_autosave()
        self._loaded_config = None
        self._last_save_path = None
        self._has_unsaved_changes = False
        self._window.bot_canvas.close_bot()
        self._window.left_sidebar.set_config(None)
        self._window.set_bot_name(None, None)
        self._window.set_save_enabled(False)
        self._window.set_close_enabled(False)
        self._window.set_start_enabled(False)

    def _save_bot(self) -> None:
        if self._loaded_config is None:
            return
        if self._last_save_path is None:
            self._save_bot_as()
            return
        try:
            self._loaded_config.save(self._last_save_path)
            self._has_unsaved_changes = False
            self._delete_autosave()
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
        old_save_path = self._last_save_path
        try:
            self._loaded_config.save(path)
            self._last_save_path = path
            self._has_unsaved_changes = False
            self._delete_autosave(old_save_path)
            self._window.set_bot_name(self._loaded_config.name, Path(path).name)
            self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
            self._window.bot_canvas.refresh_placeholder()
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to save bot", str(e))

    # ------------------------------------------------------------------
    # Bot control
    # ------------------------------------------------------------------

    def _on_import_deck_requested(self, slot: int) -> None:
        clients = self._client_manager.clients
        if not clients:
            QMessageBox.warning(self._window, "No clients", "No Wizard101 clients are connected.")
            return
        client_idx = slot - 1
        if client_idx >= len(clients):
            QMessageBox.warning(self._window, "No client", f"No client connected for slot {slot}.")
            return
        asyncio.create_task(self._import_deck_for_slot(slot))

    async def _import_deck_for_slot(self, slot: int) -> None:
        from bartlebot.bot.strategy_models import DeckData  # noqa: PLC0415
        from bartlebot.strategy_store import load_bot_strategy, save_bot_strategy  # noqa: PLC0415

        if self._loaded_config is None:
            return
        clients = self._client_manager.clients
        client_idx = slot - 1
        if client_idx >= len(clients):
            return
        active_bcs_id = SettingsManager.instance().get_active_strategy(self._loaded_config.id)
        if not active_bcs_id:
            logger.warning("No active Bot Combat Strategy — deck import skipped for slot %d", slot)
            return
        try:
            bcs = load_bot_strategy(active_bcs_id)
        except Exception:
            logger.exception("Failed to load BotCombatStrategy %s for deck import", active_bcs_id)
            return
        client = clients[client_idx]
        logger.info("Importing deck for slot %d", slot)
        try:
            async with DeckBuilder(client) as db:
                preset_dict = await db.get_deck_preset()
        except Exception:
            logger.exception("Failed to import deck for slot %d", slot)
            return
        bcs.player_decks[slot] = DeckData(**preset_dict)
        logger.info("Deck imported for slot %d: %d cards", slot, bcs.player_decks[slot].total_cards())
        try:
            save_bot_strategy(bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy after deck import")
        self._window.left_sidebar.combat_tab.refresh_deck_summaries()
        self._on_config_changed()

    def _on_run_requested(self) -> None:
        if not self._client_manager.clients or self._loaded_config is None:
            return
        from bartlebot.gui.safety_warning_dialog import SafetyWarningDialog, collect_safety_warnings  # noqa: PLC0415

        safety_warnings = collect_safety_warnings(self._loaded_config)
        if safety_warnings:
            dlg = SafetyWarningDialog(safety_warnings, parent=self._window)
            if not dlg.exec():
                return

        combat_warnings = self._collect_combat_warnings()
        if combat_warnings:
            msg = "\n".join(f"• {w}" for w in combat_warnings)
            result = QMessageBox.warning(
                self._window,
                "Combat Configuration Warnings",
                f"The following issues were found with the combat configuration:\n\n{msg}\n\n"
                "The bot will run using built-in defaults for any missing strategies. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if result != QMessageBox.StandardButton.Yes:
                return

        self._bot_task = asyncio.create_task(self._start_bot())

    def _collect_combat_warnings(self) -> list[str]:
        """Return a list of combat config warning strings. Empty list = no issues."""
        from bartlebot.strategy_store import (  # noqa: PLC0415
            load_bot_strategy,
            load_duel_strategy,
            load_player_strategy,
        )

        if self._loaded_config is None:
            return []
        warnings: list[str] = []
        bot_id = self._loaded_config.id
        sm = SettingsManager.instance()
        active_bcs_id = sm.get_active_strategy(bot_id)

        if not active_bcs_id:
            warnings.append("No Bot Combat Strategy is set — will use built-in combat defaults.")
            return warnings

        try:
            bcs = load_bot_strategy(active_bcs_id)
        except Exception:
            warnings.append(f"Active Bot Combat Strategy file is missing (id: {active_bcs_id[:8]}…).")
            return warnings

        num_clients = len(self._client_manager.clients)

        # Check default strategy
        if bcs.default_strategy_id:
            try:
                dcs = load_duel_strategy(bcs.default_strategy_id)
                if num_clients < dcs.min_players:
                    warnings.append(
                        f"Default strategy '{dcs.name}' requires ≥{dcs.min_players} players "
                        f"but only {num_clients} client(s) are connected."
                    )
                for assignment in dcs.player_assignments:
                    pcs_id = assignment.player_combat_strategy_id
                    try:
                        load_player_strategy(pcs_id)
                    except Exception:
                        warnings.append(
                            f"Player strategy file missing in default DCS '{dcs.name}' (id: {pcs_id[:8]}…)."
                        )
            except Exception:
                warnings.append(f"Default Duel Strategy file is missing (id: {bcs.default_strategy_id[:8]}…).")

        # Check state assignments
        for sa in bcs.state_assignments:
            try:
                dcs = load_duel_strategy(sa.duel_combat_strategy_id)
                if num_clients < dcs.min_players:
                    warnings.append(
                        f"State assignment DCS '{dcs.name}' requires ≥{dcs.min_players} players "
                        f"but only {num_clients} client(s) are connected."
                    )
                for assignment in dcs.player_assignments:
                    pcs_id = assignment.player_combat_strategy_id
                    try:
                        load_player_strategy(pcs_id)
                    except Exception:
                        warnings.append(f"Player strategy file missing in DCS '{dcs.name}' (id: {pcs_id[:8]}…).")
            except Exception:
                warnings.append(f"Assigned Duel Strategy file is missing (id: {sa.duel_combat_strategy_id[:8]}…).")

        return warnings

    async def _start_bot(self) -> None:
        if not self._client_manager.clients or self._loaded_config is None:
            return
        machine = BotMachine(
            clients=self._client_manager.clients,
            config=self._loaded_config.to_machine_config(node_specs=NODE_SPECS),
            node_registry=NODE_REGISTRY,
            bot_config=self._loaded_config,
            on_state_change=self._on_machine_state_change,
            on_transition=self._on_machine_transition,
            on_drop=self._on_machine_drop,
            on_client_disconnected=self._on_machine_client_disconnected,
        )
        self._current_machine = machine
        self._client_manager.set_machine(machine)
        await self._apply_pre_run_decks()
        self._window.set_start_enabled(False)
        self._window.right_sidebar.drops.setup_clients(len(self._client_manager.clients))
        self._window.right_sidebar.drops.clear_drops()
        self._window.left_sidebar.log_panel.on_run_started()
        log_cb = self._window.left_sidebar.log_panel.append_log_formatted
        add_gui_callback(log_cb)
        self._window.bot_canvas.set_running()
        self._window.right_sidebar.clients.set_bot_running(True)
        self._window.left_sidebar.set_bot_running(True)
        try:
            try:
                with keep.running(on_fail="error"):
                    await machine.run()
            except ActivationError:
                logger.warning(
                    "wakepy could not acquire a sleep-prevention wake lock; bot will run without sleep guard"
                )
                await machine.run()
        finally:
            self._window.bot_canvas.set_idle()
            self._window.right_sidebar.clients.set_bot_running(False)
            self._window.left_sidebar.set_bot_running(False)
            remove_gui_callback(log_cb)
            self._client_manager.set_machine(None)
            self._current_machine = None
            self._window.set_start_enabled(self._start_enabled())

    async def _apply_pre_run_decks(self) -> None:
        """Apply saved deck presets to each connected client before the FSM starts."""
        from bartlebot.strategy_store import load_bot_strategy  # noqa: PLC0415

        if self._loaded_config is None:
            return
        active_bcs_id = SettingsManager.instance().get_active_strategy(self._loaded_config.id)
        if not active_bcs_id:
            return
        try:
            bcs = load_bot_strategy(active_bcs_id)
        except Exception:
            logger.warning("Could not load BotCombatStrategy %s — skipping deck apply", active_bcs_id)
            return
        if not bcs.player_decks:
            return
        clients = self._client_manager.clients
        for slot, deck_data in bcs.player_decks.items():
            if deck_data.is_empty():
                continue
            client_idx = slot - 1  # slots are 1-based
            if client_idx >= len(clients):
                logger.warning("Deck saved for slot %d but only %d client(s) connected — skipping", slot, len(clients))
                continue
            client = clients[client_idx]
            preset_dict = deck_data.model_dump()
            logger.info("Applying saved deck for slot %d (%d cards)", slot, deck_data.total_cards())
            try:
                async with DeckBuilder(client) as db:
                    await db.set_deck_preset(preset_dict)
            except Exception:
                logger.warning(
                    "Failed to fully apply deck for slot %d — run will continue with whatever was set",
                    slot,
                    exc_info=True,
                )

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
        self._has_unsaved_changes = True
        self._window.set_save_enabled(True)
        canvas_config = self._window.bot_canvas.config
        if canvas_config is not None:
            self._loaded_config = canvas_config
        if self._loaded_config is not None:
            self._window.left_sidebar.bot_info.refresh_states(self._loaded_config.states)
            self._window.bot_canvas.refresh_initial(self._loaded_config.initial)
            self._window.bot_canvas.refresh_recovery(self._loaded_config.recovery_state)
            self._window.left_sidebar.options.set_config(self._loaded_config)
            filename = Path(self._last_save_path).name if self._last_save_path else "Untitled"
            self._window.set_bot_name(self._loaded_config.name, filename)

    def _on_mode_changed(self, mode: BotMode) -> None:
        self._window.left_sidebar.set_editable(mode == BotMode.EDIT)
        if mode == BotMode.RUN:
            self._window.set_start_enabled(self._start_enabled())

    def _start_enabled(self) -> bool:
        return bool(self._client_manager.clients) and self._loaded_config is not None

    def _on_clients_changed(self, _clients: list) -> None:
        self._window.set_start_enabled(self._start_enabled())

    # ------------------------------------------------------------------
    # Auto-save / crash recovery
    # ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        """Read the theme setting and apply palette + stylesheet to the app."""
        from bartlebot.gui.theme import THEME, build_custom_theme, set_theme, set_theme_instance  # noqa: PLC0415

        sm = SettingsManager.instance()
        theme_mode = sm.get_general("theme", default="system")
        if theme_mode == "custom":
            overrides = sm.get("custom_theme", default={})
            set_theme_instance(build_custom_theme(overrides))
        else:
            set_theme(theme_mode)
        self._app.setPalette(THEME.build_palette())
        self._app.setStyleSheet(THEME.global_stylesheet())

    def _reconfigure_autosave_timer(self) -> None:
        """Read current settings and start/stop/reconfigure the auto-save timer."""
        sm = SettingsManager.instance()
        enabled = sm.get_general("auto_save_enabled", default=True)
        interval_sec = sm.get_general("auto_save_interval_seconds", default=300)
        interval_ms = max(30, interval_sec) * 1000
        self._auto_save_timer.stop()
        if enabled:
            self._auto_save_timer.setInterval(interval_ms)
            self._auto_save_timer.start()

    def _autosave_path(self) -> Path:
        """Derive the recovery file path for the currently loaded config."""
        stem = Path(self._last_save_path).stem if self._last_save_path else "_unsaved"
        return SettingsManager.autosave_dir() / f"{stem}.autosave.json"

    def _do_autosave(self) -> None:
        """Timer callback — write a recovery file if there are unsaved changes."""
        if not self._has_unsaved_changes or self._loaded_config is None:
            return
        # The config is read-only while the bot is running, nothing new to save.
        if self._current_machine is not None:
            return
        try:
            self._loaded_config.save(self._autosave_path())
            logger.debug("Auto-saved recovery file: %s", self._autosave_path())
        except Exception:
            logger.exception("Auto-save failed")

    def _delete_autosave(self, save_path: str | None = None) -> None:
        """Remove the recovery file after a successful manual save."""
        stem = Path(save_path).stem if save_path else "_unsaved"
        p = SettingsManager.autosave_dir() / f"{stem}.autosave.json"
        try:
            if p.exists():
                p.unlink()
                logger.debug("Deleted auto-save file: %s", p)
        except OSError:
            logger.warning("Could not delete auto-save file %s", p)

    def _check_crash_recovery(self) -> None:
        """Scan the autosave directory at startup and offer to restore newer files."""
        candidates = []
        for autosave_path in sorted(SettingsManager.autosave_dir().glob("*.autosave.json")):
            stem = autosave_path.name.replace(".autosave.json", "")
            if stem == "_unsaved":
                original_path: Path | None = None
                is_candidate = True
            else:
                original_path = self._find_original_for_stem(stem)
                if original_path and original_path.exists():
                    is_candidate = autosave_path.stat().st_mtime > original_path.stat().st_mtime
                else:
                    is_candidate = True
            if is_candidate:
                candidates.append(
                    {
                        "autosave_path": autosave_path,
                        "original_path": original_path,
                        "modified": datetime.fromtimestamp(autosave_path.stat().st_mtime),
                    }
                )
        if candidates:
            self._offer_recovery(candidates)

    def _find_original_for_stem(self, stem: str) -> Path | None:
        """Search recent_bot_files for a path whose stem matches the autosave stem."""
        for recent_path in SettingsManager.instance().recent_bot_files:
            p = Path(recent_path)
            if p.stem == stem:
                return p
        return None

    def _offer_recovery(self, candidates: list[dict]) -> None:
        """Show the RecoveryDialog and act on the user's choices."""
        from bartlebot.gui.recovery_dialog import RecoveryDialog  # noqa: PLC0415

        dlg = RecoveryDialog(candidates, parent=self._window)
        if dlg.exec():
            chosen = dlg.selected_candidates()
        else:
            chosen = []

        chosen_paths = {c["autosave_path"] for c in chosen}

        # Restore chosen candidates (last one loaded becomes the active config).
        for c in chosen:
            autosave_path: Path = c["autosave_path"]
            original_path: Path | None = c.get("original_path")
            try:
                config = BotConfig.load(autosave_path)
            except Exception as e:
                QMessageBox.warning(
                    self._window,
                    "Recovery failed",
                    f"Could not load recovery file:\n{autosave_path}\n\n{e}",
                )
                chosen_paths.discard(autosave_path)
                continue
            self._loaded_config = config
            self._last_save_path = str(original_path) if original_path else None
            display_name = original_path.name if original_path else "Untitled (recovered)"
            self._window.set_bot_name(config.name, display_name)
            self._window.bot_canvas.setup(config)
            self._window.left_sidebar.set_config(config)
            self._window.bot_canvas.enter_edit_mode()
            self._window.set_save_enabled(True)
            self._window.set_close_enabled(True)
            self._window.set_start_enabled(self._start_enabled())
            # Mark dirty so the user is reminded to save and autosave continues.
            self._has_unsaved_changes = True

        # Delete autosave files that were not chosen (discarded by the user).
        for c in candidates:
            if c["autosave_path"] not in chosen_paths:
                try:
                    c["autosave_path"].unlink()
                except OSError:
                    pass
