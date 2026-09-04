from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from wakepy import ActivationError, keep

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from wizwalker.extensions.scripting.deck_builder import DeckBuilder
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot import group_manager, recovery_readiness, restricted_zones, wizard_names
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
from bartlebot.utils.window_kill import force_close_client

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bartlebot.bot.strategy_models import DeckData

#: What ClientWidget shows for a zone it could not read; never a real zone name.
_ZONE_PLACEHOLDER = "—"


def merge_zone_choices(client_zones: Sequence[str], recent_zones: Sequence[str]) -> tuple[list[str], list[str]]:
    """Build the zone dropdown's contents from live and remembered zones.

    Returns ``(live, choices)``: the de-duplicated zones clients are actually in,
    and those followed by the remembered ones that are not already listed.  Live
    zones lead because they are what a bot being edited right now is about to
    reference.  Blanks and the unread placeholder are dropped — offering "—" as a
    zone name would write it straight into a bot config.
    """
    live: list[str] = []
    for zone in client_zones:
        if zone and zone != _ZONE_PLACEHOLDER and zone not in live:
            live.append(zone)
    return live, live + [z for z in recent_zones if z not in live]


class AppController(QObject):
    """Owns all application-level state and wires the GUI to the bot engine."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        # Named before the first stylesheet is built, because QStandardPaths derives
        # the cache directory from it and that is where the themed combo box arrow is
        # written. Left unset, Qt falls back to the executable's name — "python".
        self._app.setApplicationName("BartleBot")
        self._app.setStyle("Fusion")
        self._apply_theme()
        self._handler = WizSprinter()
        self._current_machine: BotMachine | None = None
        self._loaded_config: BotConfig | None = None
        self._last_save_path: str | None = None

        # Long-lived asyncio task — held here to prevent GC (event loop keeps weak refs only).
        self._bot_task: asyncio.Task | None = None

        # Same reason: in-flight client close sequences, one per crashed client.
        self._close_tasks: set[asyncio.Task] = set()

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
        self._window.set_auto_select_driver(self._client_manager.drive_client_in)
        self._window.set_slot_reserver(self._client_manager.reserve_slots)
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
        w.left_sidebar.set_clients_getter(lambda: self._client_manager.clients)

        def _zone_getter() -> list[str]:
            settings = SettingsManager.instance()
            live, choices = merge_zone_choices(w.right_sidebar.clients.client_zones(), settings.recent_zones)
            for zone in live:
                settings.add_recent_zone(zone)
            return choices

        w.bot_canvas.set_zone_getter(_zone_getter)

        # Left sidebar config changes
        w.left_sidebar.config_changed.connect(self._on_config_changed)
        w.left_sidebar.metadata_changed.connect(self._on_metadata_changed)
        w.right_sidebar.config_changed.connect(self._on_config_changed)

        # Deck import / apply requests from Combat tab
        w.left_sidebar.combat_tab.import_deck_requested.connect(self._on_import_deck_requested)
        w.left_sidebar.combat_tab.apply_deck_requested.connect(self._on_apply_deck_requested)

        # Client list changes → start button state. The game view is not connected here:
        # a tile's position follows its slot rather than its player index, so ClientManager
        # syncs the view itself, on every detection tick, including ticks where the client
        # list did not change at all. See bartlebot.tile_slots.
        self._client_manager.clients_changed.connect(self._on_clients_changed)

        # Wizard names → game tile pills. The sidebar reads the cache directly in its own
        # refresh loop, so it needs no connection here.
        self._client_manager.wizard_names_changed.connect(w.game_view.set_wizard_names)

        # The launch screen describes accounts that are *not* running, so its character
        # lines cannot come from a client — they come from the persisted roster, which
        # ClientManager owns and fills. Injected rather than imported so the widget does
        # not reach for a manager it has no business constructing.
        w.game_view.set_roster_source(self._client_manager.characters_for)

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
        w.settings_applied.connect(w.right_sidebar.log_panel.apply_settings)
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
        self._app.aboutToQuit.connect(self._client_manager.stop)
        # The canvas is never closed: Qt delivers QCloseEvent to the window being
        # closed, not to its children, so its own closeEvent never fires in the app.
        self._app.aboutToQuit.connect(self._window.bot_canvas.prepare_for_shutdown)
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
            id=str(uuid4()),
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
        self._window.right_sidebar.set_config(config)
        self._window.bot_canvas.enter_edit_mode()
        self._window.set_save_enabled(True)
        self._window.set_close_enabled(True)
        self._window.set_start_enabled(self._start_enabled())

    def _open_bot(self, path: str | None = None) -> None:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self._window, "Open Bot File", "", "Bot Files (*.json)")
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
        self._window.left_sidebar.set_config(config)
        self._window.right_sidebar.set_config(config)
        self._window.set_save_enabled(True)
        self._window.set_close_enabled(True)
        self._window.set_start_enabled(self._start_enabled())
        self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        self._window.bot_canvas.refresh_placeholder()

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
        self._window.right_sidebar.set_config(None)
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
        # A fork gets its own identity. The copy would otherwise share the original's
        # id, and with it every value the user has stored against that bot -- and the
        # remembered combat strategy, which is a fork's to choose again.
        old_id = self._loaded_config.id
        self._loaded_config.id = str(uuid4())
        try:
            self._loaded_config.save(path)
        except Exception as e:
            # Nothing was written, so the file on disk is still the original and the
            # loaded bot is still that bot. Keeping the minted id here would orphan
            # every value the user has stored against it for the rest of the session.
            self._loaded_config.id = old_id
            QMessageBox.critical(self._window, "Failed to save bot", str(e))
            return
        self._last_save_path = path
        self._has_unsaved_changes = False
        self._delete_autosave(old_save_path)
        self._window.set_bot_name(self._loaded_config.name, Path(path).name)
        self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        self._window.bot_canvas.refresh_placeholder()
        # A new id resolves against a different set of stored values -- none, for a
        # fresh fork -- so every surface displaying one has to re-resolve. Without
        # this, Setup goes on showing the original's overrides against a bot that
        # does not have them, and only a reopen tells the truth.
        self._window.left_sidebar.refresh_options(self._loaded_config)
        # The combat half of the same re-resolve: the active strategy and the BCS
        # list are both keyed on the id, and a fork inherits neither (#179).
        self._window.left_sidebar.rebind_identity()

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
        deck and the wizard are the same one it was saved from.  A *successful*
        archmastery change counts as clean: opting in is what asked for it, and
        a dialog on every apply would train the user to dismiss these.  Failing
        to make one does not — it was asked for and silently did nothing.
        """
        if report.is_clean:
            return

        lines: list[str] = []
        if report.archmastery_failed:
            lines.append(
                f"Could not set the archmastery school to {report.archmastery_failed}. The school was left as it was."
            )
        if report.substitutions:
            if lines:
                lines.append("")
            lines.append("Used a lower tier than the deck asked for:")
            lines += [f"    {used}  (deck wanted {req})" for req, used in sorted(report.substitutions.items())]
        if report.short:
            if lines:
                lines.append("")
            lines.append("Fewer copies went in than the deck asked for:")
            lines += [f"    {name}  ({got} of {wanted})" for name, (got, wanted) in sorted(report.short.items())]
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

        self._show_deck_notice(
            f"Deck applied for P{slot}, with changes",
            "\n".join(lines),
            warn=bool(report.skipped or report.archmastery_failed),
        )

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
        if not self._check_restricted_zones():
            return
        from bartlebot.gui.safety_warning_dialog import SafetyWarningDialog, collect_safety_warnings  # noqa: PLC0415

        safety_warnings = collect_safety_warnings(self._loaded_config, self._client_manager.clients)
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

        if not self._confirm_recovery_gaps():
            return

        self._bot_task = asyncio.create_task(self._start_bot())

    def _check_restricted_zones(self) -> bool:
        """Refuse to start a bot that names a restricted zone. Returns whether to proceed.

        First check in the run sequence, and the only one with no way past it. The
        other pre-run dialogs ask; this one tells. Running the safety and combat
        warnings first would make the user answer questions about a run that was
        never going to start.

        Catching it here rather than letting the in-run watcher handle it means the
        client never travels: there is no arena to be seen standing in and no hooks
        to tear out.
        """
        if self._loaded_config is None:
            return True
        found = restricted_zones.check_bot_config(self._loaded_config)
        if not found:
            return True
        lines = "\n".join(f"• {label}: {zone} — {hit.reason}" for label, zone, hit in found)
        logger.critical("Refusing to run: bot names %d restricted zone(s)", len(found))
        QMessageBox.critical(
            self._window,
            "Restricted Zone",
            "This bot cannot be run. It sends a client to a zone BartleBot does not "
            "automate:\n\n"
            f"{lines}\n\n"
            "BartleBot is for PvE. PvP zones and Beastmoon are off limits, and this "
            "is not something that can be turned off.",
        )
        return False

    def _confirm_recovery_gaps(self) -> bool:
        """Warn about clients that could not be recovered, and return whether to proceed.

        Runs here in the Qt slot rather than inside _start_bot for the reason in
        CLAUDE.md: exec() from within an asyncio task wedges the tasks that wake while
        the dialog is open. No task exists yet at this point.

        Silent when every client is recoverable, which is the normal case — a dialog
        that appears every run stops being read.
        """
        gaps = self._client_manager.recovery_gaps()
        if not gaps:
            return True

        clients = self._client_manager.clients
        lines = [
            recovery_readiness.client_label(position, wizard_names.cached_name(clients[position - 1])) + f": {reason}"
            for position, reason in gaps
        ]
        for line in lines:
            logger.warning("Client cannot be recovered — %s", line)

        result = QMessageBox.warning(
            self._window,
            "Clients Cannot Be Recovered",
            recovery_readiness.warning_text(lines, len(clients)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return result == QMessageBox.StandardButton.Yes

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
            on_restricted_zone=self._on_machine_restricted_zone,
        )
        self._current_machine = machine
        self._client_manager.set_machine(machine)
        self._window.set_start_enabled(False)
        self._window.right_sidebar.drops.setup_clients(len(self._client_manager.clients))
        self._window.right_sidebar.drops.clear_drops()
        self._window.right_sidebar.log_panel.on_run_started()
        log_cb = self._window.right_sidebar.log_panel.append_log_formatted
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
        """Called by the health monitor when a client becomes unresponsive.

        Force-closes the game window so that remove_dead_clients() detects the exit
        and triggers the reconnect procedure via _on_clients_removed. That reaping is
        keyed on the *process* exiting, not the window, so the close has to be seen
        through: a crashing client parks on an error popup and never exits on WM_CLOSE
        alone, which used to leave recovery waiting forever (#79).
        """
        logger.warning("Client hwnd=%d flagged unresponsive — closing it", hwnd)
        task = asyncio.create_task(self._close_dead_client(hwnd))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    def _on_machine_restricted_zone(self, description: str) -> None:
        """Called by the health monitor when a client reaches a restricted zone.

        The machine has already stopped itself by the time this runs. What is left
        is to detach from every client, so nothing of this tool remains hooked into
        a process sitting in a PvP zone -- a stopped FSM on its own leaves the memory
        hooks in place, which is the state the guard exists to avoid.
        """
        logger.critical("Restricted zone reached (%s) — unhooking all clients", description)
        task = asyncio.create_task(self._detach_from_restricted_zone(description))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    async def _detach_from_restricted_zone(self, description: str) -> None:
        """Unhook every client, then tell the user why the run ended.

        The dialog is deferred past this task for the Python 3.13 re-entrancy trap in
        CLAUDE.md, and is scheduled in a finally: an unhook that throws still has to
        produce the explanation, or the bot simply stops with no reason given.
        """
        try:
            await self._client_manager.unhook_all()
        except Exception:
            logger.exception("Failed to unhook clients after restricted-zone stop")
        finally:
            QTimer.singleShot(
                0,
                lambda: QMessageBox.critical(
                    self._window,
                    "Restricted Zone",
                    f"The bot was stopped: a client entered {description}.\n\n"
                    "BartleBot is for PvE. PvP zones and Beastmoon are off limits.\n\n"
                    "All clients have been unhooked. Move them out of the zone and "
                    "reconnect to continue.",
                ),
            )

    async def _close_dead_client(self, hwnd: int) -> None:
        """Run the close sequence for a flagged client and report a failure to the user."""
        try:
            closed = await force_close_client(hwnd)
        except Exception:
            logger.exception("Failed to close client hwnd=%d", hwnd)
            return
        if closed:
            return
        # Deferred rather than emitted here: recovery_error opens a modal QMessageBox,
        # and exec() from inside an asyncio task is the re-entrancy trap in CLAUDE.md.
        # A zero-delay timer runs it from Qt's loop with no task frame on the stack.
        QTimer.singleShot(
            0,
            lambda: self._client_manager.recovery_error.emit(
                "A crashed client could not be closed — it is likely showing a Windows error "
                "popup that has to be dismissed by hand.\n\n"
                "Close it manually and the bot will recover from there."
            ),
        )

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_metadata_changed(self) -> None:
        """Mark the bot dirty for an edit that cannot have changed its option set.

        The description is the case this exists for.  ``_on_config_changed`` ends in
        ``refresh_options``, which recomposes both option surfaces and rebuilds every
        row when the composed list moved -- around 55ms on a 67-state bot, and it was
        being paid once per *character* typed into the description box, which is what
        made that field unusable.  Nothing composes an option from prose, so there is
        nothing here to rebuild: the guard in ``refresh_options`` (#184) would now
        skip it either way, and this still saves the recompose and the description's
        own round trip.
        """
        self._has_unsaved_changes = True
        self._window.set_save_enabled(True)

    def _on_config_changed(self) -> None:
        self._has_unsaved_changes = True
        self._window.set_save_enabled(True)
        canvas_config = self._window.bot_canvas.config
        if canvas_config is not None:
            self._loaded_config = canvas_config
            # Undo/redo replaces the canvas's config object; the sidebar has to
            # follow it or its edits go nowhere (#53).  No-op the rest of the time.
            self._window.left_sidebar.sync_config(canvas_config, self._window.bot_canvas.mode == BotMode.EDIT)
        if self._loaded_config is not None:
            self._window.bot_canvas.refresh_initial(self._loaded_config.initial)
            self._window.bot_canvas.refresh_recovery(self._loaded_config.recovery_state)
            # An edge is drawn dotted while it is conditional, and deleting an option
            # strips its conditions off every transition that guarded on it -- from a
            # surface with no way to reach the canvas.  Cheap: each edge is only
            # repainted when its own flag actually moved.
            self._window.bot_canvas.refresh_edge_flags()
            self._window.left_sidebar.refresh_options(self._loaded_config)
            filename = Path(self._last_save_path).name if self._last_save_path else "Untitled"
            self._window.set_bot_name(self._loaded_config.name, filename)

    def _on_mode_changed(self, mode: BotMode) -> None:
        self._window.left_sidebar.set_editable(mode == BotMode.EDIT)
        self._window.right_sidebar.set_editable(mode == BotMode.EDIT)
        if mode == BotMode.RUN:
            self._window.set_start_enabled(self._start_enabled())

    def _start_enabled(self) -> bool:
        return bool(self._client_manager.clients) and self._loaded_config is not None

    def _on_clients_changed(self, clients: list) -> None:
        self._window.set_start_enabled(self._start_enabled())
        self._window.set_client_summary(len(clients))
        self._window.left_sidebar.combat_tab.set_client_count(len(clients))
        # Player checkboxes grey out the players nobody is behind; both panels build
        # them once and would otherwise stay stale until the next node selection.
        self._window.bot_canvas.refresh_player_availability()
        self._window.left_sidebar.refresh_player_availability()

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
            if self._last_save_path:
                stem = Path(self._last_save_path).stem
                SettingsManager.instance().set_autosave_origin(stem, self._last_save_path)
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
        SettingsManager.instance().remove_autosave_origin(stem)

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
        """Resolve the original bot file for an auto-save stem.

        Prefers the origin recorded at auto-save time (set_autosave_origin), which
        stores the exact path the auto-save was derived from. Falls back to a
        stem match against recent_bot_files for auto-saves written before that
        mapping existed.
        """
        origin = SettingsManager.instance().get_autosave_origin(stem)
        if origin:
            return Path(origin)
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
            self._window.right_sidebar.set_config(config)
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
