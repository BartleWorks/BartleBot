from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from bartleby.qt.dialogs.first_run_dialog import FirstRunDialog, FirstRunPage
from bartleby.qt.global_hotkeys import ID_BASE_SPEED_AUTHORING, GlobalHotkeys
from bartleby.qt.logo import install_app_icon, refresh_taskbar_icon
from bartleby.runtime.clients.install_location import apply_install
from bartleby.runtime.clients.wizwalker_cache import apply_cache_dir
from bartleby.runtime.launcher.launch_profile import restore_game_prefs, restore_ui_size
from bartleby.shared.preferences import PreferenceStore
from bartleby.shared.vendor import VENDOR
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.clients.authoring_tools import SLOT_KEYS, AuthoringAction, AuthoringTools, slot_keys_armed, source_index
from bartlebot.clients.client_manager import ClientManager
from bartlebot.combat.flee_marks import FleeMarks
from bartlebot.content.store_roots import DEV_ENV_VAR, dev_mode
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.controller.decks import Decks
from bartlebot.controller.documents import Documents
from bartlebot.controller.run import Run
from bartlebot.controller.sharing import Sharing
from bartlebot.controller.template_roots import TemplateRoots
from bartlebot.controller.templates import Templates
from bartlebot.game import zone_names
from bartlebot.gui.canvas import recording
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.combat_strategies.deck_link import DeckClientLink
from bartlebot.gui.debug.game_hang_trace import GameHangTracer
from bartlebot.gui.debug.stall_trace import StallTracer
from bartlebot.gui.debug.window_show_trace import WindowShowTracer
from bartlebot.gui.dialogs.first_run_dialog import needs_asking, setup_pages
from bartlebot.gui.main_window import MainWindow
from bartlebot.gui.share_drop import ShareFileDrops
from bartlebot.gui.update_prompt import UpdatePrompt
from bartlebot.packaging.product import NAME as PRODUCT
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bartleby.qt.app.single_instance import SingleInstance

#: What a PlayerCard shows for a zone it could not read; never a real zone name.
_ZONE_PLACEHOLDER = "—"


def merge_zone_choices(client_zones: Sequence[str], recent_zones: Sequence[str]) -> tuple[list[str], list[str]]:
    """Build the zone dropdown's contents from live and remembered zones.

    Returns ``(live, choices)``: the de-duplicated zones clients are actually in,
    and those followed by the remembered ones that are not already listed.  Live
    zones lead because they are what a bot being edited right now is about to
    reference.  Blanks and the unread placeholder are dropped — offering "—" as a
    zone name would write it straight into a bot config.

    A duplicate of a zone -- a Challenge Mode copy, a gauntlet room in another level band --
    is offered as its canonical zone, which is what a bot stores and what matches every
    duplicate (zone_names.canonical_zone), so two of them never appear side by side.
    """
    live: list[str] = []
    for zone in client_zones:
        if not zone or zone == _ZONE_PLACEHOLDER:
            continue
        if (canonical := zone_names.canonical_zone(zone)) not in live:
            live.append(canonical)
    recent = dict.fromkeys(zone_names.canonical_zone(z) for z in recent_zones)
    return live, live + [z for z in recent if z not in live]


class AppController(QObject):
    """Owns all application-level state and wires the GUI to the bot engine."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        # Named before the first stylesheet is built, because QStandardPaths derives
        # the cache directory from it and that is where the themed combo box arrow is
        # written. Left unset, Qt falls back to the executable's name — "python".
        #
        # Organization *and* application, so Qt's own directory lands under the vendor
        # beside everything else this publisher writes — see shared.vendor.
        self._app.setOrganizationName(VENDOR)
        self._app.setApplicationName(PRODUCT)
        install_app_icon(self._app)
        self._app.setStyle("Fusion")
        self._apply_theme()
        # Before anything asks wizwalker's get_wiz_install(): a Steam install, or a folder
        # chosen in setup, is somewhere wizwalker never looks (epic #215, Stage 2).
        apply_install()
        # Before anything reads wizwalker's cache, or its default under StarrFox is made (#234).
        apply_cache_dir()
        self._handler = WizSprinter()
        # Every bot's fights fled on a default, for as long as the app is open: the Combat
        # tab marks them (Stage 7 of epic #201). See Run._on_machine_fight_fled.
        self._flee_marks = FleeMarks()
        # Where the template store is; tests point it at a tmp_path.
        self._roots = TemplateRoots()
        if dev_mode():
            logger.warning(
                "%s is set: bots, templates, subgraphs and combat strategies are read from and saved to %s",
                DEV_ENV_VAR,
                project_root(),
            )
        # Stray windows flashing on screen (#120): logged with the call that showed them.
        self._window_tracer = WindowShowTracer(app) if dev_mode() else None

        # Quit gate state. The close event is refused while clients are being released,
        # and accepted once they are — see _confirm_shutdown.
        self._shutdown_task: asyncio.Task | None = None
        self._clients_released: bool = False

        self._window = MainWindow()
        self._client_manager = ClientManager(
            handler=self._handler,
            game_view=self._window.game_view,
            drops_widget=self._window.right_sidebar.drops,
            get_recovery_state=self._recovery_state,
        )
        # The open document, the ones set aside under it, and every flow on them.
        clients = self._client_manager
        self._documents = Documents(
            self._window,
            SubgraphLibrary(),
            self._roots,
            clients=lambda: clients.clients,
            is_running=lambda: self._run.is_running,
            parent=self,
        )
        # File › Templates… and a template draft's own verbs.
        self._templates = Templates(self._window, self._documents, self._roots)
        # Share files: Export, and Import from a menu, a drop or a later launch.
        self._sharing = Sharing(self._window, self._documents, self._app, is_running=lambda: self._run.is_running)
        # Deck import and apply, from the Combat tab and the dialog, and before a run.
        self._decks = Decks(self._window, self._client_manager, self._documents)
        self._window.set_auto_select_driver(self._client_manager.drive_client_in)

        # The authoring utilities (#81): Bar A's buttons, and the hotkeys that fire them
        # from inside a game window. See bartlebot.clients.authoring_tools and gui.global_hotkeys.
        self._authoring = AuthoringTools(
            clients=lambda: self._client_manager.clients,
            bot_config=lambda: self._documents.loaded_config,
            speed_multiplier=lambda: SettingsManager.instance().speed_multiplier,
            on_change=self._refresh_authoring_bar,
        )
        self._hotkeys = GlobalHotkeys(self._on_authoring_hotkey)
        self._hotkeys.set_bindings(SettingsManager.instance().authoring_hotkeys)
        self._app.installNativeEventFilter(self._hotkeys)
        # Speed-authoring's keys 1-0 (#85). A set of their own, armed apart from the rest,
        # since while registered the digits are taken from the game's chat as well.
        self._slot_hotkeys = GlobalHotkeys(self._on_slot_hotkey, id_base=ID_BASE_SPEED_AUTHORING)
        self._app.installNativeEventFilter(self._slot_hotkeys)
        self._window.set_launch_registrar(
            self._client_manager.begin_launch,
            self._client_manager.end_launch,
            self._client_manager.report_launch_stage,
            self._client_manager.dismiss_launch,
        )
        # The bot run: the checks before it, the pre-run setup, and the machine's callbacks.
        self._run = Run(
            self._window,
            self._client_manager,
            self._documents,
            self._decks,
            self._authoring,
            self._flee_marks,
            on_run_starting=self._disarm_hotkeys,
            on_run_ended=lambda: self._on_game_focus_changed(self._window.game_view.focused_hwnd),
        )
        self._wire()
        self._window.show()
        # The taskbar asked for the icon while start-up held the thread; see refresh_taskbar_icon.
        QTimer.singleShot(0, lambda: refresh_taskbar_icon(self._window))
        self._documents.reconfigure_autosave_timer()
        self._prune_dead_strategy_settings()
        self._restore_game_display_settings()
        self._documents.check_crash_recovery()
        # A share file dropped anywhere on the window imports it (#171); one named at
        # launch, as a double-clicked file is (#27), once the window is up.
        self._share_drops = ShareFileDrops(self)
        self._share_drops.files_dropped.connect(self._sharing.import_dropped)
        self._app.installEventFilter(self._share_drops)
        self._wire_updates()
        QTimer.singleShot(0, self._after_window_shown)
        # A frozen window: every thread's stack, taken while the loop is held. Last, so
        # start-up's own work is not taken for a freeze, but before the loop first runs,
        # so the window's first maximized frames are watched.
        self._stall_tracer = StallTracer() if dev_mode() else None
        if self._stall_tracer is not None:
            self._stall_tracer.start()
        # A game window that stops answering, and what its thread was waiting on.
        self._hang_tracer = GameHangTracer(self._hooked_windows) if dev_mode() else None
        if self._hang_tracer is not None:
            self._hang_tracer.start()

    def _recovery_state(self) -> str | None:
        """The open bot's recovery state, where a client that reconnects resumes."""
        config = self._documents.loaded_config
        return config.recovery_state if config is not None else None

    @property
    def subgraph_library(self) -> SubgraphLibrary:
        """The session's view of the two subgraph roots. Tests swap in a ``tmp_path`` one."""
        return self._documents.subgraph_library

    @subgraph_library.setter
    def subgraph_library(self, library: SubgraphLibrary) -> None:
        self._documents.subgraph_library = library

    def listen_for_launches(self, instance: SingleInstance) -> None:
        """Take share files from later launches of the app, handed over by ``instance`` (``Sharing``)."""
        self._sharing.listen_for_launches(instance)

    @property
    def document_depth(self) -> int:
        """How many documents sit under the open one."""
        return self._documents.document_depth

    def _hooked_windows(self) -> list[tuple[str, int]]:
        """Each hooked client's window, by player number, for the game hang tracer."""
        return [(f"P{i + 1}", client.window_handle) for i, client in enumerate(self._client_manager.clients)]

    def _restore_game_display_settings(self) -> None:
        """Give the game back the display settings a previous session overwrote.

        A launch below 800x600 has to shrink the game's own HUD to match (see
        bartleby/runtime/game/game_sizing.py), and every launch retiles the resolution and forces
        windowed mode. The user plays Wizard101 outside BartleBot, so all three are put
        back on quit. This is the other half: a session that died without reaching the
        quit path left them in place, and nothing else would ever undo them. No-op when
        the last session restored them cleanly.

        Each restore is guarded on its own so one that cannot be written -- a locked
        file, an install that moved -- does not take the other down with it.
        """
        try:
            restore_ui_size()
        except Exception:
            logger.exception("Could not restore the game's UI scale from a previous session")
        try:
            restore_game_prefs()
        except Exception:
            logger.exception("Could not restore the game's resolution from a previous session")

    def _prune_dead_strategy_settings(self) -> None:
        """Drop remembered strategy selections whose BCS file is gone.

        Entries accumulate one per bot ever opened, and survive deleting the
        strategy they point at.  Best-effort: a failure here must not stop the
        app from starting.
        """
        from bartlebot.combat.strategy_store import list_all_bot_strategies  # noqa: PLC0415

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
        w.action_new.triggered.connect(lambda: self._documents.new_bot())
        w.action_open.triggered.connect(lambda: self._documents.open_bot())
        w.action_close.triggered.connect(lambda: self._documents.close_bot())
        w.action_save.triggered.connect(lambda: self._documents.save_bot())
        w.action_save_as.triggered.connect(lambda: self._documents.save_bot_as())
        w.action_templates.triggered.connect(lambda: self._templates.open_templates_dialog())
        w.action_export.triggered.connect(lambda: self._sharing.export_open_document())
        w.action_import.triggered.connect(lambda: self._sharing.import_share_file())
        w.action_setup.triggered.connect(lambda: self._rerun_setup())
        w.open_recent_requested.connect(self._documents.open_bot)

        # Canvas signals
        w.bot_canvas.config_changed.connect(self._documents.on_config_changed)
        # A finished template review, or an undo across one: the base moved.
        w.bot_canvas.template_ref_changed.connect(self._templates.refresh_template_context)
        self._documents.template_context_changed.connect(self._templates.refresh_template_context)
        w.bot_canvas.mode_changed.connect(self._on_mode_changed)
        w.bot_canvas.run_requested.connect(self._run.on_run_requested)
        w.bot_canvas.pause_requested.connect(self._run.pause_bot)
        w.bot_canvas.resume_requested.connect(self._run.resume_bot)
        w.bot_canvas.stop_button.clicked.connect(self._run.stop_bot)
        w.bot_canvas.save_requested.connect(lambda: self._documents.save_bot())
        w.bot_canvas.save_done.connect(self._documents.save_and_done)
        w.bot_canvas.open_definition_requested.connect(self._documents.open_definition)
        w.bot_canvas.definition_back_requested.connect(self._documents.definition_back)
        w.bot_canvas.definition_save_requested.connect(self._documents.on_definition_save_requested)
        w.bot_canvas.definition_save_as_requested.connect(lambda: self._documents.save_definition_as())
        w.bot_canvas.definition_revert_requested.connect(lambda: self._documents.revert_definition())
        w.bot_canvas.delete_subgraph_requested.connect(lambda sid: self._documents.delete_subgraph(sid))
        w.bot_canvas.rename_subgraph_requested.connect(lambda sid: self._documents.rename_subgraph(sid))
        w.bot_canvas.export_bot_requested.connect(lambda path: self._sharing.export_bot(path))
        w.bot_canvas.delete_bot_requested.connect(lambda path: self._documents.delete_bot(path))
        w.bot_canvas.export_subgraph_requested.connect(lambda sid: self._sharing.export_subgraph(sid))
        w.bot_canvas.document_back_requested.connect(self._documents.back_to_document)
        w.bot_canvas.template_save_requested.connect(lambda: self._documents.save_bot())
        w.bot_canvas.template_release_requested.connect(lambda: self._templates.release_template())
        w.bot_canvas.template_close_requested.connect(lambda: self._documents.close_bot())
        w.bot_canvas.copy_requested.connect(lambda: self._documents.make_own_copy())
        w.bot_canvas.set_clients_getter(lambda: self._client_manager.clients)
        # Ctrl+R records from the game window last in focus, as Bar A's buttons act on it.
        w.bot_canvas.set_preferred_player_getter(
            lambda: source_index(self._client_manager.clients, w.game_view.last_focused_hwnd)
        )
        w.bot_canvas.set_subgraph_library(self._documents.subgraph_library)
        w.left_sidebar.set_clients_getter(lambda: self._client_manager.clients)

        def _zone_getter() -> list[str]:
            settings = SettingsManager.instance()
            live, choices = merge_zone_choices(w.right_sidebar.clients.client_zones(), settings.recent_zones)
            for zone in live:
                settings.add_recent_zone(zone)
            return choices

        w.bot_canvas.set_zone_getter(_zone_getter)

        # Left sidebar config changes
        w.left_sidebar.config_changed.connect(self._documents.on_config_changed)
        w.left_sidebar.metadata_changed.connect(self._documents.on_metadata_changed)
        w.left_sidebar.edit_starting.connect(w.bot_canvas.note_edit_starting)
        w.left_sidebar.declarations_changed.connect(w.bot_canvas.reshow_properties)
        # A declaration edit -- an option's or a variable's -- changes what validation finds
        # without touching the graph, so nothing on the canvas would recheck it. Only the
        # sidebar's: the canvas rechecks its own edits already.
        w.left_sidebar.config_changed.connect(w.bot_canvas.revalidate)
        w.right_sidebar.config_changed.connect(self._documents.on_config_changed)

        # Deck import / apply requests from Combat tab
        w.left_sidebar.combat_tab.import_deck_requested.connect(self._decks.on_import_deck_requested)
        w.left_sidebar.combat_tab.apply_deck_requested.connect(self._decks.on_apply_deck_requested)
        w.left_sidebar.combat_tab.import_all_decks_requested.connect(self._decks.on_import_all_decks_requested)
        w.left_sidebar.combat_tab.apply_all_decks_requested.connect(self._decks.on_apply_all_decks_requested)
        w.left_sidebar.combat_tab.export_bot_strategy_requested.connect(
            lambda bcs: self._sharing.export_bot_strategy(bcs)
        )
        w.left_sidebar.combat_tab.set_flee_marks(self._flee_marks)

        # Which account is behind each player slot, for the combat dialog's slot pills.
        # A callable rather than a pushed value: an account identity is learned some time
        # after its client is hooked and nothing announces it arriving, so the answer is
        # taken at the moment the dialog opens. See CombatTab.set_player_account_source.
        w.left_sidebar.combat_tab.set_player_account_source(
            lambda: [self._client_manager.account_for(c) for c in self._client_manager.clients]
        )

        # What the combat dialog's Deck tab may ask of the clients. The deck job stays ours.
        w.left_sidebar.combat_tab.set_deck_client_link(
            DeckClientLink(
                client_count=lambda: len(self._client_manager.clients),
                import_deck=self._decks.import_deck_for_dialog,
                apply_deck=self._decks.apply_deck_for_dialog,
            )
        )

        # Client list changes → start button state. The game view is not connected here:
        # a tile's position follows its slot rather than its player index, so ClientManager
        # syncs the view itself, on every detection tick, including ticks where the client
        # list did not change at all. See bartleby.runtime.clients.tile_slots.
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

        # Dragging a player card in the sidebar → reorder the whole client list at once.
        # A permutation, not a swap: decomposing one drag into transpositions would rebuild
        # the sidebar once per pair and put intermediate orders on screen -- see
        # ClientManager.reorder_clients.
        w.right_sidebar.clients.reordered.connect(self._client_manager.reorder_clients)

        # Which account a client belongs to, for the color stripe down its card. Injected
        # rather than signalled: identities are learned inside the client loop and nothing
        # announces one arriving, so the section re-asks on each refresh tick.
        w.right_sidebar.clients.set_account_source(self._client_manager.account_for)

        # Unhook/rehook buttons in sidebar → ClientManager
        w.right_sidebar.clients.unhook_requested.connect(lambda idx: asyncio.ensure_future(self._unhook_client(idx)))
        w.right_sidebar.clients.rehook_requested.connect(
            lambda hwnd: asyncio.ensure_future(self._client_manager.rehook_client(hwnd))
        )
        w.right_sidebar.clients.unhook_all_requested.connect(lambda: asyncio.ensure_future(self._unhook_all()))
        w.right_sidebar.clients.rehook_all_requested.connect(
            lambda: asyncio.ensure_future(self._client_manager.rehook_all())
        )

        # Queued: recovery emits from inside its own task, and a modal opened there wedges
        # every task that wakes while it is up -- _client_loop among them, which recovery is
        # waiting on (bartlebot/gui/CLAUDE.md, #154).
        self._client_manager.recovery_error.connect(
            lambda msg: QMessageBox.critical(self._window, "Client Recovery Failed", msg),
            type=Qt.ConnectionType.QueuedConnection,
        )

        # Authoring utilities: the buttons, the focus that arms their hotkeys, and the
        # settings that bind them.
        w.authoring_bar.action_requested.connect(self._on_authoring_action)
        w.game_view.game_focus_changed.connect(self._on_game_focus_changed)
        w.bot_canvas.mode_changed.connect(lambda _mode: self._refresh_slot_keys())
        w.settings_applied.connect(self._apply_authoring_settings)
        self._refresh_authoring_bar()

        # Quit gate: the window asks before it closes, because closing it destroys every
        # embedded game window.
        w.set_shutdown_handler(self._confirm_shutdown)

        w.update_recent_menu(SettingsManager.instance().recent_bot_files)
        w.settings_applied.connect(self._documents.reconfigure_autosave_timer)
        w.settings_applied.connect(w.right_sidebar.log_panel.apply_settings)
        w.settings_applied.connect(self._apply_theme)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._client_manager.start()
        quit_event = asyncio.Event()
        # Nothing to flush here any more: combat edits used to sit in a 500 ms
        # debounce that a quit could beat, and now live behind the combat strategy
        # dialog's own Save button. See bartlebot.combat.strategy_workset.
        self._app.aboutToQuit.connect(self._client_manager.stop)
        # Give the system-wide hotkeys back while the thread that registered them can.
        self._app.aboutToQuit.connect(self._hotkeys.close)
        self._app.aboutToQuit.connect(self._slot_hotkeys.close)
        # The canvas is never closed: Qt delivers QCloseEvent to the window being
        # closed, not to its children, so its own closeEvent never fires in the app.
        self._app.aboutToQuit.connect(self._window.bot_canvas.prepare_for_shutdown)
        # Last line of defense for a quit that never reached closeEvent (a session logout,
        # an app.quit() from elsewhere): releasing the windows is synchronous, so it still
        # works here, where there is no loop left to await an unhook on.
        self._app.aboutToQuit.connect(self._window.game_view.unembed_all)
        # The game's UI scale, resolution and window mode are the user's, not ours: a
        # launch that retiled them gives them back here. Synchronous, so they still run
        # at this point.
        self._app.aboutToQuit.connect(self._restore_game_display_settings)
        if self._stall_tracer is not None:
            self._app.aboutToQuit.connect(self._stall_tracer.stop)
        if self._hang_tracer is not None:
            self._app.aboutToQuit.connect(self._hang_tracer.stop)
        self._app.aboutToQuit.connect(quit_event.set)
        await quit_event.wait()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _confirm_shutdown(self) -> bool:
        """The window's quit gate. True lets this close through, False refuses it.

        Quitting with clients attached is destructive twice over: the embedded game
        windows are Win32 children of the Qt window and die with it, and the WizWalker
        hooks are never released, so a surviving client cannot be hooked again until it
        is relaunched. Both are fixed by unhooking first — which is async, and
        cannot run from ``aboutToQuit`` — so the close is refused here, the release runs
        as a task, and the task closes the window again when it lands.
        """
        if self._clients_released:
            return True
        if self._shutdown_task is not None:
            return False  # already releasing; the task will close the window
        if not self._documents.close_definitions() or not self._documents.confirm_unsaved("Quit BartleBot"):
            return False
        hooked = len(self._client_manager.clients)
        unhooked = len(self._client_manager.unhooked_clients)
        if not hooked and not unhooked:
            return True
        close_windows = self._ask_shutdown_choice(hooked + unhooked)
        if close_windows is None:
            return False
        # Safe from the Python 3.13 re-entrancy trap: the dialog above ran in this Qt
        # slot, before any task existed.
        self._shutdown_task = asyncio.ensure_future(self._release_clients(close_windows=close_windows))
        return False

    def _ask_shutdown_choice(self, total: int) -> bool | None:
        """Ask what to do with the open game windows. True closes them, None cancels."""
        subject = "One Wizard101 window is" if total == 1 else f"{total} Wizard101 windows are"
        it = "it" if total == 1 else "them"
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Quit BartleBot")
        box.setText(f"{subject} still open — close {it} too, or leave {it} running?")
        keep_btn = box.addButton("Close BartleBot only", QMessageBox.ButtonRole.AcceptRole)
        close_btn = box.addButton("Close BartleBot and Wizard101", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is close_btn:
            return True
        if clicked is keep_btn:
            return False
        return None

    async def _release_clients(self, *, close_windows: bool) -> None:
        """Unhook (and optionally close) every client, then let the window close.

        A running bot is stopped rather than waited for: its nodes are about to lose the
        hooks they read through either way, and a node mid-timeout would hold the quit
        for a minute.
        """
        logger.info("Quitting — releasing clients (close_windows=%s)", close_windows)
        self._run.stop_bot()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Before the unhook, which is the last moment a boosted speed can be written back.
            await self._authoring.shutdown()
            survivors = await self._client_manager.shutdown(close_windows=close_windows)
            if survivors:
                logger.warning(
                    "%d game window(s) survived the close sequence: %s — they are unhooked "
                    "and have to be closed by hand",
                    len(survivors),
                    ", ".join(str(h) for h in survivors),
                )
        except Exception:
            logger.exception("Error while releasing clients on quit")
        finally:
            QApplication.restoreOverrideCursor()
            self._shutdown_task = None
            self._clients_released = True
            # Deferred so the close runs from Qt's loop rather than from inside this
            # task, per the re-entrancy rule in bartlebot/gui/CLAUDE.md.
            QTimer.singleShot(0, self._window.close)

    # ------------------------------------------------------------------
    # Authoring utilities
    # ------------------------------------------------------------------

    def _on_authoring_action(self, action: str) -> None:
        """A Bar A button: its source is the game window that had focus before the click."""
        source = self._window.game_view.last_focused_hwnd
        asyncio.ensure_future(self._authoring.perform(AuthoringAction(action), source))

    def _on_authoring_hotkey(self, action: str) -> None:
        """A hotkey, pressed inside a game window: that window is the source.

        Called from Qt's native event dispatch, so it only starts the task.
        """
        game_view = self._window.game_view
        source = game_view.focused_hwnd or game_view.last_focused_hwnd
        asyncio.ensure_future(self._authoring.perform(AuthoringAction(action), source))

    def _disarm_hotkeys(self) -> None:
        """A run is starting: no hotkey reaches the game until it ends, when focus decides again."""
        self._hotkeys.set_armed(False)
        self._slot_hotkeys.set_armed(False)

    def _on_game_focus_changed(self, hwnd: int | None) -> None:
        self._hotkeys.set_armed(hwnd is not None and not self._authoring.run_active)
        self._refresh_slot_keys()

    def _refresh_slot_keys(self) -> None:
        """Arm or disarm speed-authoring's keys 1-0, by ``authoring_tools.slot_keys_armed``."""
        canvas = self._window.bot_canvas
        armed = slot_keys_armed(
            speed_authoring=self._authoring.speed_authoring_on,
            game_focused=self._window.game_view.focused_hwnd is not None,
            editing=canvas.mode == BotMode.EDIT and canvas.config is not None,
            run_active=self._authoring.run_active,
        )
        if armed and not self._slot_hotkeys.armed:
            # Read on every arming, and only the assigned slots, so an empty one leaves its
            # digit to the game. Slots are assigned on the favorites bar, which takes the
            # keyboard back from the game -- so nothing can change while the keys are armed.
            slots = SettingsManager.instance().favorited_hotkey_nodes
            self._slot_hotkeys.set_bindings({f"slot_{i}": SLOT_KEYS[i] for i, node in enumerate(slots) if node})
        self._slot_hotkeys.set_armed(armed)

    def _on_slot_hotkey(self, action: str) -> None:
        """A key 1-0, pressed in a game window while speed-authoring is on (#85).

        The node goes in after the selected one, and records from the player whose window
        the key was pressed in. Called from Qt's native event dispatch, so nothing here may
        block: a refusal's popup is deferred through the event loop, like every dialog
        opened from a callback. The popup takes the keyboard from the game, which disarms
        the keys until the author clicks back in -- they need the canvas to act on it anyway.
        """
        slot = int(action.removeprefix("slot_"))
        if recording.is_recording():
            logger.warning(
                "Speed-authoring: key %s ignored, the last node is still recording. "
                "Finish that step, or stop it with its Record button, first.",
                SLOT_KEYS[slot],
            )
            return
        canvas = self._window.bot_canvas
        new_id, refusal = canvas.speed_insert(slot)
        if new_id is None:
            QTimer.singleShot(0, lambda: QMessageBox.information(self._window, "Speed-Authoring", refusal))
            return
        node_type = SettingsManager.instance().favorited_hotkey_nodes[slot]
        clients = self._client_manager.clients
        game_view = self._window.game_view
        index = source_index(clients, game_view.focused_hwnd or game_view.last_focused_hwnd)
        if clients and canvas.param_panel.start_record(clients[index]):
            logger.info("Speed-authoring: added %s, recording from P%d", node_type, index + 1)
        else:
            logger.info("Speed-authoring: added %s", node_type)

    def _apply_authoring_settings(self) -> None:
        self._hotkeys.set_bindings(SettingsManager.instance().authoring_hotkeys)
        self._refresh_authoring_bar()

    def _refresh_authoring_bar(self) -> None:
        tools = self._authoring
        self._window.authoring_bar.show_state(
            speed_on=tools.speed_on,
            combat_on=tools.combat_on,
            busy=tools.busy,
            run_active=tools.run_active,
            clients=len(self._client_manager.clients),
            hotkeys=SettingsManager.instance().authoring_hotkeys,
            speed_authoring_on=tools.speed_authoring_on,
            dialogue_on=tools.dialogue_on,
        )
        self._refresh_slot_keys()

    async def _unhook_client(self, idx: int) -> None:
        """Unhook one client, after giving it back its own speed and stopping its fight."""
        clients = self._client_manager.clients
        if 0 <= idx < len(clients):
            await self._authoring.release(clients[idx])
        await self._client_manager.unhook_client(idx)

    async def _unhook_all(self) -> None:
        await self._authoring.shutdown()
        await self._client_manager.unhook_all()

    # ------------------------------------------------------------------
    # Subgraphs
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Export (#170)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Import (#171)
    # ------------------------------------------------------------------

    def _after_window_shown(self) -> None:
        """What waits for the window: the update check, the first-run questions, then a file named at launch.

        One callback rather than two timers, because each opens a modal dialog and a timer
        still pending fires inside the first one's ``exec()``, opening the second on top of it.
        A timer's callback is a synchronous slot, so ``exec()`` is safe here
        (``bartlebot/gui/CLAUDE.md``, qasync).

        The update check goes first because it opens nothing itself: it runs in the
        background while setup is answered, and its popup waits for every dialog here to
        close (``gui/update_prompt.py``).
        """
        self._updates.check_at_startup()
        self._offer_first_run()
        self._sharing.import_from_arguments()

    def _wire_updates(self) -> None:
        """The update check, its popup, the title bar's button and Help › Check for Updates… (#27)."""
        self._updates = UpdatePrompt(
            self._window,
            busy=lambda: QApplication.activeModalWidget() is not None,
            running=self._bot_busy,
            # The window's own close, so the quit path unhooks every client first.
            restart=self._window.close,
            parent=self,
        )
        self._updates.button_changed.connect(self._window.title_bar.set_update_text)
        self._window.title_bar.update_requested.connect(self._updates.open_offer)
        action = self._window.action_check_updates
        action.setEnabled(self._updates.can_check)
        if not self._updates.can_check:
            action.setToolTip("Only an installed BartleBot updates itself.")
        action.triggered.connect(lambda: self._updates.check_now())

    @property
    def updates(self) -> UpdatePrompt:
        return self._updates

    def _bot_busy(self) -> bool:
        """Whether a bot is starting or running: the update popup waits for it to stop."""
        return self._run.busy

    def _offer_first_run(self) -> None:
        """Open setup when any page is unanswered, here or in BartleBuddy (epic #215).

        Each page records its answer on Next, so closing keeps what was answered, and the
        next launch opens on the pages still unanswered. ``docs/FIRST_RUN.md`` says when
        each page counts as answered.
        """
        prefs = PreferenceStore()
        # Each page's answer is read first: building the pages costs a registry and Steam
        # search, the account cards and screen measuring, at a launch with nothing to ask.
        if not needs_asking(prefs):
            return
        self._open_setup(setup_pages(prefs), rerun=False)

    def _rerun_setup(self) -> None:
        """File › Set up BartleBot…: every page that applies, on the current values."""
        self._open_setup(setup_pages(PreferenceStore(), rerun=True), rerun=True)

    def _open_setup(self, pages: list[FirstRunPage], *, rerun: bool) -> None:
        from bartlebot.gui.context_help import set_help_topic  # noqa: PLC0415 - beside its one use

        dialog = FirstRunDialog(pages, rerun=rerun, parent=self._window)
        set_help_topic(dialog, "setup")
        dialog.exec()
        # Whatever was answered is on disk, finished or not: the combat page may have changed
        # the Team default, and the Accounts and Launch profile pages the accounts and
        # profiles the launch screen and the Launch menu draw. The layout is fitted first,
        # since the resolution and the panes are what the launch screen judges them against.
        self._window.left_sidebar.combat_tab.refresh_source()
        self._window.fit_layout_to_launches()
        self._window.catch_up_with_accounts()

    # ------------------------------------------------------------------
    # Template drafts
    # ------------------------------------------------------------------

    @property
    def templates_root(self) -> Path | None:
        """The root the template store is under: None for the project root. Tests point it at a ``tmp_path``."""
        return self._roots.root

    @templates_root.setter
    def templates_root(self, root: Path | None) -> None:
        self._roots.root = root

    @property
    def templates_user_root(self) -> Path | None:
        """The user root the template store writes: None for the default. Tests point it at a ``tmp_path``."""
        return self._roots.user_root

    @templates_user_root.setter
    def templates_user_root(self, root: Path | None) -> None:
        self._roots.user_root = root

    # ------------------------------------------------------------------
    # Definition documents
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Bot control
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # BotMachine callback receivers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: BotMode) -> None:
        self._window.left_sidebar.set_editable(mode == BotMode.EDIT)
        self._window.right_sidebar.set_editable(mode == BotMode.EDIT)
        if mode == BotMode.RUN:
            self._window.set_start_enabled(self._documents.start_enabled())

    def _on_clients_changed(self, clients: list) -> None:
        self._window.set_start_enabled(self._documents.start_enabled())
        self._window.set_client_summary(len(clients))
        self._window.left_sidebar.combat_tab.set_client_count(len(clients))
        self._refresh_authoring_bar()
        # Player checkboxes gray out the players nobody is behind; both panels build
        # them once and would otherwise stay stale until the next node selection.
        self._window.bot_canvas.refresh_player_availability()
        self._window.left_sidebar.refresh_player_availability()

    # ------------------------------------------------------------------
    # Auto-save / crash recovery
    # ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        """Read the theme setting and apply palette + stylesheet to the app (core's ``apply_app_theme``)."""
        from bartleby.qt.theming.app_theme import apply_app_theme  # noqa: PLC0415

        sm = SettingsManager.instance()
        apply_app_theme(self._app, sm.theme, sm.custom_theme)
