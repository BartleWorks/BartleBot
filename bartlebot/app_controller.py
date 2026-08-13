from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from wakepy import ActivationError, keep

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from wizwalker.extensions.scripting.deck_builder import DeckBuilder
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot import group_manager, wizard_names
from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.client_manager import ClientManager
from bartlebot.combat_warnings import collect_combat_warnings
from bartlebot.deck_io import DeckApplyReport, apply_deck, read_deck
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.main_window import MainWindow
from bartlebot.logging_setup import add_gui_callback, remove_gui_callback
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS
from bartlebot.nodes.timing import DECK_APPLY_TIMEOUT, DECK_IMPORT_TIMEOUT
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from bartlebot.bot.strategy_models import DeckData


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
        self._prune_dead_strategy_settings()
        self._check_crash_recovery()

    def _prune_dead_strategy_settings(self) -> None:
        """Drop remembered strategy selections whose BCS file is gone.

        Entries accumulate one per bot ever opened, and survive deleting the
        strategy they point at.  Best-effort: a failure here must not stop the
        app from starting.
        """
        from bartlebot.strategy_store import list_all_bot_strategies  # noqa: PLC0415

        try:
            known = {bcs.id for bcs in list_all_bot_strategies()}
        except Exception:
            logger.exception("Failed to enumerate bot strategies; skipping settings prune")
            return

        if not known:
            # An empty result is indistinguishable from "the store can't see
            # bot_strategies/ at all" (wrong project_root, directory deleted,
            # a dev checkout with no strategies yet) — and pruning on it would
            # wipe every remembered selection the user has.  Refuse rather than
            # guess.
            logger.debug("No bot strategies visible — skipping active-strategy prune")
            return

        removed = SettingsManager.instance().prune_active_strategies(known)
        if removed:
            logger.info("Pruned %d dead active-strategy setting(s)", removed)

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

        # Deck import / apply requests from Combat tab
        w.left_sidebar.combat_tab.import_deck_requested.connect(self._on_import_deck_requested)
        w.left_sidebar.combat_tab.apply_deck_requested.connect(self._on_apply_deck_requested)

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
        w.right_sidebar.clients.unhook_all_requested.connect(
            lambda: asyncio.ensure_future(self._client_manager.unhook_all())
        )
        w.right_sidebar.clients.rehook_all_requested.connect(
            lambda: asyncio.ensure_future(self._client_manager.rehook_all())
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
        # Connected before quit_event so debounced edits land while the widgets
        # are still alive.
        self._app.aboutToQuit.connect(self._flush_pending_edits)
        self._app.aboutToQuit.connect(quit_event.set)
        await quit_event.wait()

    def _flush_pending_edits(self) -> None:
        """Commit debounced combat edits that the 500 ms timer has not fired for yet."""
        try:
            self._window.left_sidebar.combat_tab.flush_pending_save()
        except Exception:
            logger.exception("Failed to flush pending combat edits on quit")

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
                preset_dict = await asyncio.wait_for(read_deck(db), timeout=DECK_IMPORT_TIMEOUT)
        except TimeoutError:
            logger.error("Deck import for slot %d timed out after %.0fs", slot, DECK_IMPORT_TIMEOUT)
            self._warn_deck_failed("import", slot, "timed out")
            return
        except Exception:
            logger.exception("Failed to import deck for slot %d", slot)
            self._warn_deck_failed("import", slot, "failed — see the log for details")
            return
        deck_data = DeckData(**preset_dict)
        bcs.player_decks[slot] = deck_data
        logger.info("Deck imported for slot %d: %d cards", slot, deck_data.total_cards())
        try:
            save_bot_strategy(bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy after deck import")
        self._window.left_sidebar.combat_tab.update_deck_slot(slot, deck_data)
        self._on_config_changed()

    def _on_apply_deck_requested(self, slot: int) -> None:
        clients = self._client_manager.clients
        if not clients:
            QMessageBox.warning(self._window, "No clients", "No Wizard101 clients are connected.")
            return
        client_idx = slot - 1
        if client_idx >= len(clients):
            QMessageBox.warning(self._window, "No client", f"No client connected for slot {slot}.")
            return
        asyncio.create_task(self._apply_deck_for_slot(slot))

    async def _apply_deck_for_slot(self, slot: int) -> None:
        from bartlebot.strategy_store import load_bot_strategy  # noqa: PLC0415

        if self._loaded_config is None:
            return
        clients = self._client_manager.clients
        client_idx = slot - 1
        if client_idx >= len(clients):
            return
        active_bcs_id = SettingsManager.instance().get_active_strategy(self._loaded_config.id)
        if not active_bcs_id:
            logger.warning("No active Bot Combat Strategy — deck apply skipped for slot %d", slot)
            return
        try:
            bcs = load_bot_strategy(active_bcs_id)
        except Exception:
            logger.exception("Failed to load BotCombatStrategy %s for deck apply", active_bcs_id)
            return
        deck_data = bcs.player_decks.get(slot)
        if deck_data is None or deck_data.is_empty():
            logger.warning("No deck saved for slot %d — nothing to apply", slot)
            return
        client = clients[client_idx]
        logger.info("Applying deck for slot %d (%d cards)", slot, deck_data.total_cards())
        try:
            report = await apply_deck(client, deck_data)
            logger.info("Deck applied for slot %d", slot)
        except TimeoutError:
            logger.error("Deck apply for slot %d timed out after %.0fs", slot, DECK_APPLY_TIMEOUT)
            self._warn_deck_failed("apply", slot, "timed out")
        except Exception:
            logger.exception("Failed to apply deck for slot %d", slot)
            self._warn_deck_failed("apply", slot, "failed — see the log for details")
        else:
            self._report_deck_apply(slot, report)

    def _report_deck_apply(self, slot: int, report: DeckApplyReport) -> None:
        """Tell the user when the deck that went on is not the deck that was saved.

        Saved decks name specific spells, so a deck built on another wizard
        routinely asks for cards this one does not have — a different school's
        hitter cannot be substituted at all.  Staying silent about that produced
        a half-built deck that looked like a success.

        Nothing is shown for a clean apply, which is the common case when the
        deck and the wizard are the same one it was saved from.
        """
        if report.is_clean:
            return

        lines: list[str] = []
        if report.substitutions:
            lines.append("Used a lower tier than the deck asked for:")
            lines += [f"    {used}  (deck wanted {req})" for req, used in sorted(report.substitutions.items())]
        if report.skipped:
            if lines:
                lines.append("")
            lines.append("Not in this wizard's spellbook, so left out:")
            lines += [f"    {self._describe_skipped(name, report)}" for name in report.skipped]
            lines.append("")
            lines.append("Swap in this wizard's own cards and re-import the deck to fix this.")

        if not report.applied:
            title = f"Deck for P{slot} not applied"
            body = "None of the saved deck's cards are in this wizard's spellbook, so the current deck was left alone."
            self._show_deck_notice(title, f"{body}\n\n" + "\n".join(lines), warn=True)
            return

        self._show_deck_notice(f"Deck applied for P{slot}, with changes", "\n".join(lines), warn=bool(report.skipped))

    @staticmethod
    def _describe_skipped(name: str, report: DeckApplyReport) -> str:
        """One line for a skipped card: what it was, and what could stand in.

        The kind and the alternatives are only known for blades, traps, wards,
        charms and auras — the interchangeable ones.  A missing attack spell
        gets its name and nothing else, which is the honest answer: a Fire
        wizard has no stand-in for a Storm hitter.
        """
        from bartlebot.card_kinds import describe_kind  # noqa: PLC0415

        kind = describe_kind(report.kinds.get(name))
        line = f"{name} — {kind}" if kind else name
        options = report.alternatives.get(name)
        if options:
            line += f"; you have {', '.join(options)}"
        return line

    def _warn_deck_failed(self, action: str, slot: int, reason: str) -> None:
        """Surface a deck import/apply failure instead of leaving it log-only."""
        self._show_deck_notice(
            f"Deck {action} failed",
            f"Deck {action} for P{slot} {reason}.\n\n"
            "The game's deck window may be left open or partially edited — "
            "check the client before retrying.",
            warn=True,
        )

    def _show_deck_notice(self, title: str, body: str, *, warn: bool) -> None:
        """Show a deck dialog *after* the calling task has finished.

        Both callers run inside an ``asyncio.Task``, and a blocking QMessageBox
        there is the Python 3.13 re-entrancy trap described in CLAUDE.md: the
        dialog re-enters Qt's event loop while the task's frame is still on the
        Python stack, and any task woken meanwhile is left permanently stuck.
        That is what froze the client sidebar for the duration of a bot run.

        Deferring through the Qt event loop means the dialog opens once the task
        has returned and no task is mid-execution, so blocking is safe again.
        """
        box = QMessageBox.warning if warn else QMessageBox.information
        QTimer.singleShot(0, lambda: box(self._window, title, body))

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
        """Return a list of combat config warning strings. Empty list = no issues.

        Parsing every distinct DSL takes a noticeable moment on a large library,
        so this runs under a wait cursor.
        """
        if self._loaded_config is None:
            return []

        active_bcs_id = SettingsManager.instance().get_active_strategy(self._loaded_config.id)
        num_clients = len(self._client_manager.clients)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return collect_combat_warnings(active_bcs_id, num_clients, self._loaded_config)
        finally:
            QApplication.restoreOverrideCursor()

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
            # Inside the try so the finally still tears down if the user stops during
            # a slow apply, and after add_gui_callback so failures reach this run's
            # log panel rather than the previous run's.
            await self._run_pre_run_setup()
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

    async def _run_pre_run_setup(self) -> None:
        """Prepare every connected client before the FSM starts.

        Each step must catch its own failures and log rather than raise: a setup problem
        should degrade the run, not cancel it. None of them may show a blocking dialog
        either — this runs inside the _start_bot asyncio task, where a modal .exec()
        deadlocks the qasync loop under Python 3.13 (see CLAUDE.md).
        """
        await self._apply_pre_run_decks()
        await self._form_pre_run_group()

    async def _form_pre_run_group(self) -> None:
        """Put all clients into one in-game group, if the user has that enabled.

        Group membership is lost whenever a client logs off or crashes, and it is what
        guarantees the first turn in combat, so it is re-established at the start of every
        run rather than assumed to have survived from the last one.
        """
        if not SettingsManager.instance().auto_group_players:
            return

        clients = self._client_manager.clients
        if len(clients) < 2:
            logger.debug("Skipping pre-run grouping — fewer than two clients connected")
            return

        try:
            # Survey once and hand the result to form_group. Each survey opens and closes
            # the social window on every client, so letting form_group re-read would
            # double the slowest and most failure-prone part of this.
            states = await group_manager.read_group_states(clients)
            if all(s.grouped for s in states) and group_manager.same_group(states) is not False:
                logger.debug("Skipping pre-run grouping — all clients are already in one group")
                return
            names = await wizard_names.resolve_names(clients)
            result = await group_manager.form_group(clients, names, states=states)
            if result.success:
                logger.info("Pre-run grouping: %s", result.summary())
            else:
                logger.warning(
                    "Pre-run grouping did not fully succeed (%s) — the run will continue ungrouped",
                    result.summary(),
                )
        except Exception:
            logger.warning("Pre-run grouping failed — the run will continue ungrouped", exc_info=True)

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
        pending = []
        for slot, deck_data in bcs.player_decks.items():
            if deck_data.is_empty():
                continue
            client_idx = slot - 1  # slots are 1-based
            if client_idx >= len(clients):
                logger.warning("Deck saved for slot %d but only %d client(s) connected — skipping", slot, len(clients))
                continue
            logger.info("Applying saved deck for slot %d (%d cards)", slot, deck_data.total_cards())
            pending.append(self._apply_one_pre_run_deck(clients[client_idx], slot, deck_data))
        if not pending:
            return
        # Concurrently, because applying four decks in sequence is four times the
        # wait for no reason: every input WizWalker sends is addressed to a single
        # window handle (send_key -> timed_send_key(self.window_handle, ...),
        # MouseHandler -> SendMessageW(self.client.window_handle, ...)), the
        # mouseless hook writes into each client's own memory, and nothing anywhere
        # touches the foreground window. So the clients cannot interfere.
        #
        # The win is smaller than N: memory reads are synchronous ReadProcessMemory
        # on the loop thread and SendMessageW blocks until the target game's message
        # pump answers, so only the sleeps actually overlap. Measured at 1.88x for
        # four clients (tools/probe_deck_apply_timing.py).
        #
        # Each task swallows its own failures, so no return_exceptions is needed and
        # one bad client cannot cancel the others' applies half-finished.
        await asyncio.gather(*pending)

    async def _apply_one_pre_run_deck(self, client, slot: int, deck_data: DeckData) -> None:
        """Apply one slot's deck, absorbing any failure.

        Never raises: these run concurrently under a single gather, and a deck that
        will not apply is a degraded run, not a cancelled one.
        """
        try:
            await apply_deck(client, deck_data)
        except TimeoutError:
            # Log only. This runs inside the _start_bot task, and a blocking
            # QMessageBox from an asyncio task deadlocks the qasync loop under
            # Python 3.13 (see CLAUDE.md).
            logger.error(
                "Deck apply for slot %d timed out after %.0fs — run will continue with whatever was set",
                slot,
                DECK_APPLY_TIMEOUT,
            )
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

    def _on_clients_changed(self, clients: list) -> None:
        self._window.set_start_enabled(self._start_enabled())
        self._window.left_sidebar.combat_tab.set_client_count(len(clients))

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
