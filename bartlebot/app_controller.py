from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from bartleby.qt.app.logging_setup import add_gui_callback, remove_gui_callback, session_log_dir
from bartleby.qt.dialogs.first_run_dialog import FirstRunDialog, FirstRunPage
from bartleby.qt.global_hotkeys import ID_BASE_SPEED_AUTHORING, GlobalHotkeys
from bartleby.qt.logo import install_app_icon, refresh_taskbar_icon
from bartleby.runtime import run_trace
from bartleby.runtime.clients import group_manager
from bartleby.runtime.clients.install_location import apply_install
from bartleby.runtime.clients.window_kill import force_close_client
from bartleby.runtime.clients.wizwalker_cache import apply_cache_dir
from bartleby.runtime.game import wizard_names
from bartleby.runtime.launcher.launch_profile import restore_game_prefs, restore_ui_size
from bartleby.runtime.timing import DECK_APPLY_TIMEOUT, DECK_IMPORT_TIMEOUT
from bartleby.shared.preferences import PreferenceStore
from bartleby.shared.vendor import VENDOR
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.bot import variables
from bartlebot.bot.machine import BotMachine
from bartlebot.clients import recovery_readiness, run_probe
from bartlebot.clients.authoring_tools import SLOT_KEYS, AuthoringAction, AuthoringTools, slot_keys_armed, source_index
from bartlebot.clients.client_manager import ClientManager
from bartlebot.combat.combat_resolution import CombatResolver, Level, describe_run
from bartlebot.combat.combat_warnings import CombatCheck, check_combat
from bartlebot.combat.deck_io import DeckApplyReport, apply_deck, read_deck
from bartlebot.combat.flee_marks import FleeMarks
from bartlebot.content.store_roots import DEV_ENV_VAR, dev_mode
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.controller.documents import Documents
from bartlebot.controller.sharing import Sharing
from bartlebot.controller.template_roots import TemplateRoots
from bartlebot.controller.templates import Templates
from bartlebot.game import restricted_zones, zone_names
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
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS
from bartlebot.options import option_values, player_realms
from bartlebot.packaging.product import NAME as PRODUCT
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from bartleby.qt.app.single_instance import SingleInstance
    from bartleby.shared.strategy_models import DeckData

#: What a PlayerCard shows for a zone it could not read; never a real zone name.
_ZONE_PLACEHOLDER = "—"


#: Player slots with a deck card on the Combat tab; a group is at most four wizards, so
#: Import all reads no client past the fourth.
_DECK_SLOTS = 4

#: Why a deck import or apply failed, when the log has the traceback and the user does not.
_DECK_JOB_FAILED = "failed — see the log for details"

#: Said under every deck failure: a job that dies part-way leaves the game's window as it was.
_DECK_WINDOW_NOTE = "The game's deck window may be left open or partially edited — check the client before retrying."


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


def start_run_trace(slot_of) -> Path | None:
    """Begin the run's trace, in developer mode only, under ``logs/trace/``. Returns its file.

    Core's ``run_trace`` writes wherever it is told; whether a run is traced at all is
    BartleBot's call, and it is only while ``BARTLEBOT_DEV`` is set (``store_roots.dev_mode``).
    ``tools/run_trace_report.py`` summarizes the newest one.
    """
    if not dev_mode():
        return None
    return run_trace.start(slot_of, session_log_dir() / "trace")


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
        self._current_machine: BotMachine | None = None
        # Every bot's fights fled on a default, for as long as the app is open: the Combat
        # tab marks them (Stage 7 of epic #201). See _on_machine_fight_fled.
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

        # Long-lived asyncio task — held here to prevent GC (event loop keeps weak refs only).
        self._bot_task: asyncio.Task | None = None
        # The run-start read of who already has an elixir. See _on_run_requested.
        self._run_probe: asyncio.Task | None = None
        # The pre-run setup of the run in progress, so Stop can cut it short.
        self._pre_run: asyncio.Task | None = None

        # Same reason: in-flight client close sequences, one per crashed client.
        self._close_tasks: set[asyncio.Task] = set()

        # Same reason: the one deck import or apply allowed to run — see _start_deck_job.
        self._deck_task: asyncio.Task | None = None

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
            is_running=lambda: self._current_machine is not None,
            parent=self,
        )
        # File › Templates… and a template draft's own verbs.
        self._templates = Templates(self._window, self._documents, self._roots)
        # Share files: Export, and Import from a menu, a drop or a later launch.
        self._sharing = Sharing(
            self._window, self._documents, self._app, is_running=lambda: self._current_machine is not None
        )
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
        w.bot_canvas.run_requested.connect(self._on_run_requested)
        w.bot_canvas.pause_requested.connect(self._pause_bot)
        w.bot_canvas.resume_requested.connect(self._resume_bot)
        w.bot_canvas.stop_button.clicked.connect(self._stop_bot)
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
        w.left_sidebar.combat_tab.import_deck_requested.connect(self._on_import_deck_requested)
        w.left_sidebar.combat_tab.apply_deck_requested.connect(self._on_apply_deck_requested)
        w.left_sidebar.combat_tab.import_all_decks_requested.connect(self._on_import_all_decks_requested)
        w.left_sidebar.combat_tab.apply_all_decks_requested.connect(self._on_apply_all_decks_requested)
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
                import_deck=self._import_deck_for_dialog,
                apply_deck=self._apply_deck_for_dialog,
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
        self._stop_bot()
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
        if self._current_machine is not None:
            return True
        return any(task is not None and not task.done() for task in (self._bot_task, self._run_probe))

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
        dialog = FirstRunDialog(pages, rerun=rerun, parent=self._window)
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

    def _on_import_deck_requested(self, slot: int) -> None:
        if self._slot_has_client(slot):
            self._start_deck_job(self._import_deck_for_slot(slot))

    def _on_apply_deck_requested(self, slot: int) -> None:
        if self._slot_has_client(slot):
            self._start_deck_job(self._apply_deck_for_slot(slot))

    def _on_import_all_decks_requested(self) -> None:
        if self._slot_has_client(1):
            self._start_deck_job(self._import_all_decks())

    def _on_apply_all_decks_requested(self) -> None:
        if self._slot_has_client(1):
            self._start_deck_job(self._apply_all_decks())

    def _import_deck_for_dialog(self, slot: int, done: Callable[[DeckData | str], None]) -> bool:
        """Read a slot's deck for the combat dialog's Deck tab, which stores it in its own preset.

        Unlike Import on a deck card this touches no Bot Combat Strategy: the answer goes to
        *done* and the dialog's Save decides whether it lasts. False if nothing started.
        """
        if self._deck_task is not None or not self._slot_has_client(slot):
            return False

        async def job() -> None:
            result = await self._read_slot_deck(slot, self._client_manager.clients[slot - 1])
            if isinstance(result, str):
                self._warn_deck_failed("import", {slot: result})
            self._answer_dialog(done, result)

        self._start_deck_job(job())
        return True

    def _apply_deck_for_dialog(self, slot: int, deck: DeckData, done: Callable[[str | None], None]) -> bool:
        """Put *deck*, a library preset's, on a slot's client. False if nothing started."""
        if self._deck_task is not None or not self._slot_has_client(slot):
            return False

        async def job() -> None:
            result = await self._apply_slot_deck(slot, self._client_manager.clients[slot - 1], deck)
            if isinstance(result, str):
                self._warn_deck_failed("apply", {slot: result})
            else:
                self._report_deck_apply(slot, result)
            self._answer_dialog(done, result if isinstance(result, str) else None)

        self._start_deck_job(job())
        return True

    @staticmethod
    def _answer_dialog(done: Callable[[Any], None], result: object) -> None:
        """Hand a deck job's result to the dialog that asked, which may have closed meanwhile."""
        try:
            done(result)
        except RuntimeError:
            logger.info("The dialog that asked for a deck job closed before it finished")
        except Exception:
            logger.exception("A deck job's answer raised")

    def _slot_has_client(self, slot: int) -> bool:
        """Whether *slot* has a hooked client, telling the user when it does not.

        Slot 1 is what the whole-group verbs ask about: it is missing exactly when no
        client is hooked at all.
        """
        clients = self._client_manager.clients
        if not clients:
            QMessageBox.warning(self._window, "No clients", "No Wizard101 clients are connected.")
            return False
        if slot - 1 >= len(clients):
            QMessageBox.warning(self._window, "No client", f"No client connected for slot {slot}.")
            return False
        return True

    def _start_deck_job(self, job: Coroutine[object, object, None]) -> None:
        """Run a deck import or apply, holding every deck verb until it finishes.

        One job at a time. Two on one client drive its deck window at once, and two
        imports each load, set and save the strategy, so the later save drops the
        earlier one's deck. The tab disables its verbs while a job runs, but a card's
        right-click menu still reaches Import and Apply, so the refusal lives here too.
        """
        if self._deck_task is not None:
            job.close()
            logger.info("A deck import or apply is already running — ignoring the new request")
            return
        self._deck_task = asyncio.create_task(job)
        self._deck_task.add_done_callback(self._on_deck_job_done)
        self._window.left_sidebar.combat_tab.set_deck_busy(True)

    def _on_deck_job_done(self, task: asyncio.Task) -> None:
        self._deck_task = None
        if not task.cancelled() and task.exception() is not None:
            logger.error("Deck job raised", exc_info=task.exception())
        self._window.left_sidebar.combat_tab.set_deck_busy(False)

    def _active_deck_strategy_id(self, action: str) -> str | None:
        """The Bot Combat Strategy a deck import or apply works on, or None to skip it."""
        if self._documents.loaded_config is None:
            return None
        bcs_id = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
        if not bcs_id:
            logger.warning("No active Bot Combat Strategy — deck %s skipped", action)
            return None
        return bcs_id

    async def _import_deck_for_slot(self, slot: int) -> None:
        bcs_id = self._active_deck_strategy_id("import")
        clients = self._client_manager.clients
        if bcs_id is None or slot - 1 >= len(clients):
            return
        result = await self._read_slot_deck(slot, clients[slot - 1])
        if isinstance(result, str):
            self._warn_deck_failed("import", {slot: result})
            return
        self._store_imported_decks(bcs_id, {slot: result})

    async def _import_all_decks(self) -> None:
        """Read every hooked client's deck, then store them all with one save.

        The reads run concurrently, for the reason ``_apply_pre_run_decks`` gives: every
        input WizWalker sends is addressed to one client's own window. The save does not:
        see ``_store_imported_decks``. A slot that fails is reported, and the others are
        kept.
        """
        bcs_id = self._active_deck_strategy_id("import")
        if bcs_id is None:
            return
        clients = list(self._client_manager.clients)[:_DECK_SLOTS]
        slots = list(range(1, len(clients) + 1))
        results = await asyncio.gather(*(self._read_slot_deck(slot, client) for slot, client in zip(slots, clients)))
        decks = {slot: result for slot, result in zip(slots, results) if not isinstance(result, str)}
        failures = {slot: result for slot, result in zip(slots, results) if isinstance(result, str)}
        if decks:
            self._store_imported_decks(bcs_id, decks)
        if failures:
            self._warn_deck_failed("import", failures)

    async def _read_slot_deck(self, slot: int, client) -> DeckData | str:
        """*client*'s current deck, or the reason it could not be read. Never raises.

        The read half of an import, shared by one slot and by all of them, so Import all
        can read concurrently and still save once.
        """
        from bartleby.shared.strategy_models import DeckData  # noqa: PLC0415

        logger.info("Importing deck for slot %d", slot)
        try:
            preset_dict = await asyncio.wait_for(read_deck(client), timeout=DECK_IMPORT_TIMEOUT)
            deck_data = DeckData(**preset_dict)
        except TimeoutError:
            logger.error("Deck import for slot %d timed out after %.0fs", slot, DECK_IMPORT_TIMEOUT)
            return "timed out"
        except Exception:
            logger.exception("Failed to import deck for slot %d", slot)
            return _DECK_JOB_FAILED
        logger.info("Deck imported for slot %d: %d cards", slot, deck_data.total_cards())
        return deck_data

    def _store_imported_decks(self, bcs_id: str, decks: dict[int, DeckData]) -> None:
        """Write *decks* into the strategy with one load and one save, then redraw.

        Once for every slot, never once per slot: a load-set-save per slot, run
        concurrently, keeps only the slot saved last and silently drops the others.
        Loaded after the reads rather than before them, so the save lands on the file as
        it stands now.
        """
        from bartlebot.combat.strategy_store import load_bot_strategy, save_bot_strategy  # noqa: PLC0415

        try:
            bcs = load_bot_strategy(bcs_id)
        except Exception:
            logger.exception("Failed to load BotCombatStrategy %s for deck import", bcs_id)
            return
        bcs.player_decks.update(decks)
        try:
            save_bot_strategy(bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy after deck import")
        self._window.left_sidebar.combat_tab.update_deck_slots(decks)
        self._documents.on_config_changed()

    def _saved_decks(self) -> dict[int, DeckData] | None:
        """The active strategy's non-empty decks by slot, or None if there is no strategy."""
        from bartlebot.combat.strategy_store import load_bot_strategy  # noqa: PLC0415

        bcs_id = self._active_deck_strategy_id("apply")
        if bcs_id is None:
            return None
        try:
            bcs = load_bot_strategy(bcs_id)
        except Exception:
            logger.exception("Failed to load BotCombatStrategy %s for deck apply", bcs_id)
            return None
        return {slot: deck_data for slot, deck_data in bcs.player_decks.items() if not deck_data.is_empty()}

    async def _apply_deck_for_slot(self, slot: int) -> None:
        decks = self._saved_decks()
        clients = self._client_manager.clients
        if decks is None or slot - 1 >= len(clients):
            return
        deck_data = decks.get(slot)
        if deck_data is None:
            logger.warning("No deck saved for slot %d — nothing to apply", slot)
            return
        result = await self._apply_slot_deck(slot, clients[slot - 1], deck_data)
        if isinstance(result, str):
            self._warn_deck_failed("apply", {slot: result})
        else:
            self._report_deck_apply(slot, result)

    async def _apply_all_decks(self) -> None:
        """Apply each saved deck to its hooked client, concurrently, and report once."""
        decks = self._saved_decks()
        if decks is None:
            return
        clients = self._client_manager.clients
        pending = {slot: deck_data for slot, deck_data in sorted(decks.items()) if slot - 1 < len(clients)}
        if not pending:
            logger.warning("No saved deck has a connected client — nothing to apply")
            return
        results = await asyncio.gather(
            *(self._apply_slot_deck(slot, clients[slot - 1], deck_data) for slot, deck_data in pending.items())
        )
        self._report_apply_all(dict(zip(pending, results)))

    async def _apply_slot_deck(self, slot: int, client, deck_data: DeckData) -> DeckApplyReport | str:
        """Apply one slot's deck: the report, or the reason it failed. Never raises."""
        logger.info("Applying deck for slot %d (%d cards)", slot, deck_data.total_cards())
        try:
            report = await apply_deck(client, deck_data, slot=slot)
        except TimeoutError:
            logger.error("Deck apply for slot %d timed out after %.0fs", slot, DECK_APPLY_TIMEOUT)
            return "timed out"
        except Exception:
            logger.exception("Failed to apply deck for slot %d", slot)
            return _DECK_JOB_FAILED
        logger.info("Deck applied for slot %d", slot)
        return report

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

        lines = self._deck_apply_lines(report)
        if not report.applied:
            title = f"Deck for Player {slot} not applied"
            body = "None of the saved deck's cards are in this wizard's spellbook, so the current deck was left alone."
            self._show_deck_notice(title, f"{body}\n\n" + "\n".join(lines), warn=True)
            return

        self._show_deck_notice(
            f"Deck applied for Player {slot}, with changes",
            "\n".join(lines),
            warn=bool(report.skipped or report.archmastery_failed),
        )

    def _report_apply_all(self, outcomes: dict[int, DeckApplyReport | str]) -> None:
        """One notice for Apply all, however many slots need one.

        Each slot says what ``_report_deck_apply`` or ``_warn_deck_failed`` would have
        said for it alone; four dialogs in a row was the alternative. Silent when every
        deck went on clean, as a single apply is.
        """
        failures = {slot: outcome for slot, outcome in outcomes.items() if isinstance(outcome, str)}
        sections: list[str] = []
        if failures:
            sections.append(self._deck_failure_lines("apply", failures))
        warn = bool(failures)
        all_applied = not failures
        for slot, report in outcomes.items():
            if isinstance(report, str) or report.is_clean:
                continue
            if report.applied:
                heading = f"Player {slot}: applied, with changes"
                warn = warn or bool(report.skipped or report.archmastery_failed)
            else:
                heading = f"Player {slot}: not applied — none of the saved deck's cards are in this wizard's spellbook"
                warn = True
                all_applied = False
            body = [f"    {line}" if line else "" for line in self._deck_apply_lines(report)]
            sections.append("\n".join([heading, *body]))
        if not sections:
            return
        if failures:
            sections.append(_DECK_WINDOW_NOTE)
        title = "Decks applied, with changes" if all_applied else "Some decks were not applied"
        self._show_deck_notice(title, "\n\n".join(sections), warn=warn)

    def _deck_apply_lines(self, report: DeckApplyReport) -> list[str]:
        """What went on differently from the saved deck, in groups split by blank lines."""
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
        if report.over:
            if lines:
                lines.append("")
            lines.append("More copies went in than the deck asked for, and could not be taken back out:")
            lines += [
                f"    {name}  ({held}, asked for {wanted})" for name, (held, wanted) in sorted(report.over.items())
            ]
        if report.skipped:
            if lines:
                lines.append("")
            lines.append("Not in this wizard's spellbook, so left out:")
            lines += [f"    {self._describe_skipped(name, report)}" for name in report.skipped]
            lines.append("")
            lines.append("Swap in this wizard's own cards and re-import the deck to fix this.")
        return lines

    @staticmethod
    def _describe_skipped(name: str, report: DeckApplyReport) -> str:
        """One line for a skipped card: what it was, and what could stand in.

        The kind and the alternatives are only known for blades, traps, wards,
        charms and auras — the interchangeable ones.  A missing attack spell
        gets its name and nothing else, which is the honest answer: a Fire
        wizard has no stand-in for a Storm hitter.
        """
        from bartlebot.combat.card_kinds import describe_kind  # noqa: PLC0415

        kind = describe_kind(report.kinds.get(name))
        line = f"{name} — {kind}" if kind else name
        options = report.alternatives.get(name)
        if options:
            line += f"; you have {', '.join(options)}"
        return line

    def _warn_deck_failed(self, action: str, failures: dict[int, str]) -> None:
        """Surface deck import/apply failures, one line per slot, instead of leaving them log-only."""
        self._show_deck_notice(
            f"Deck {action} failed",
            f"{self._deck_failure_lines(action, failures)}\n\n{_DECK_WINDOW_NOTE}",
            warn=True,
        )

    @staticmethod
    def _deck_failure_lines(action: str, failures: dict[int, str]) -> str:
        return "\n".join(f"Deck {action} for Player {slot} {reason}." for slot, reason in sorted(failures.items()))

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
        if not self._client_manager.clients or self._documents.loaded_config is None:
            return
        if not self._documents.recompose_before_run():
            return
        if not self._check_restricted_zones():
            return
        if not self._check_player_realms():
            return
        if not self._check_variables():
            return
        from bartlebot.gui.dialogs.safety_warning_dialog import (  # noqa: PLC0415
            level_scaling_players,
            uses_friends_list,
        )

        clients = self._client_manager.clients
        players = level_scaling_players(self._documents.loaded_config, len(clients))
        friends_list = uses_friends_list(self._documents.loaded_config, len(clients))
        # Who is in a fight, and whether a level scaling player already has an elixir, are
        # memory reads, which are async, and what they feed is a blocking dialog, which must
        # not open inside a task (see "qasync + Python 3.13" in bartlebot/gui/CLAUDE.md). So
        # the reads run as a task and the rest of the sequence runs from its done callback,
        # which the loop calls outside any task.
        if self._run_probe is not None and not self._run_probe.done():
            return  # a second press while the first is still reading
        self._run_probe = asyncio.create_task(run_probe.probe_before_run(clients, players, friends_list))
        self._run_probe.add_done_callback(self._on_run_probe_done)

    def _on_run_probe_done(self, probe: asyncio.Task) -> None:
        """Carry on with the run request once the reads are in. A failed read stops nothing."""
        if probe.cancelled():
            return
        if probe.exception() is not None:
            logger.warning("Could not read the players before the run", exc_info=probe.exception())
            self._finish_run_request((), ())
            return
        result: run_probe.RunProbe = probe.result()
        if result.fighting:
            self._refuse_while_fighting(result.fighting)
            return
        if result.key_problems:
            self._refuse_unpressable_keys(result.key_problems)
            return
        self._finish_run_request(result.elixir_players, result.quick_teleport_players)

    def _refuse_while_fighting(self, fighting: list[int]) -> None:
        """Refuse to start while a player is in a fight (#203).

        Tells rather than asks. Everything before the first node needs the players out of
        battle: decks cannot be changed mid-fight, and forming the group opens the social
        window over the cards, which is how players come to sit out rounds until the game
        asks "You Are Idle". Ending the fight first costs the user seconds.
        """
        players = ", ".join(f"P{n}" for n in fighting)
        logger.warning("Refusing to run: %s in a fight", players)
        QMessageBox.warning(self._window, "Players in a Fight", run_probe.refusal_text(fighting))

    def _refuse_unpressable_keys(self, problems: tuple[str, ...]) -> None:
        """Refuse to start while a player has an action BartleBot cannot press.

        Tells rather than asks, as the fight refusal does: a node pressing that action would
        fail partway through the run, or type into the chat line, and only the player can
        change a binding, in the game (``bartleby-core/docs/KEY_BINDINGS.md``).
        """
        for problem in problems:
            logger.warning("Refusing to run: %s", problem)
        QMessageBox.warning(self._window, "Run Bot", run_probe.key_refusal_text(problems))

    def _finish_run_request(self, elixir_players, quick_teleport_players) -> None:
        """The rest of the run sequence: the dialogs that ask, then the run itself."""
        if not self._client_manager.clients or self._documents.loaded_config is None:
            return
        from bartlebot.gui.dialogs.safety_warning_dialog import (  # noqa: PLC0415
            SafetyWarningDialog,
            collect_safety_warnings,
        )

        safety_warnings = collect_safety_warnings(
            self._documents.loaded_config, self._client_manager.clients, elixir_players, quick_teleport_players
        )
        if safety_warnings:
            dlg = SafetyWarningDialog(safety_warnings, parent=self._window)
            if not dlg.exec():
                return

        if not self._confirm_combat():
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
        if self._documents.loaded_config is None:
            return True
        found = restricted_zones.check_bot_config(self._documents.loaded_config)
        if not found:
            return True
        logger.critical("Refusing to run: bot names %d restricted zone(s)", len(found))
        QMessageBox.critical(
            self._window,
            "Restricted Zone",
            "This bot cannot be run. It sends a client to a zone BartleBot does not "
            "automate:\n\n"
            f"{restricted_zones.describe_found(found)}\n\n"
            f"{restricted_zones.PVE_ONLY_TEXT}",
        )
        return False

    def _check_player_realms(self) -> bool:
        """Refuse to run a Separate bot holding a node that merges players. Returns whether to proceed.

        Like the restricted-zone check it tells rather than asks: the run would contradict
        itself, porting everyone onto one realm in a bot that says they keep to their own.
        The Options and Option Values rows already refuse the choice, so this is what a hand-edited
        file or an override from before the node was added reaches.
        """
        if self._documents.loaded_config is None:
            return True
        value = option_values.option_value(self._documents.loaded_config, player_realms.OPTION, player_realms.TOGETHER)
        reason = player_realms.run_refusal(self._documents.loaded_config, value)
        if reason is None:
            return True
        logger.warning("Refusing to run: %s is Separate in a bot with a merging node", player_realms.OPTION)
        QMessageBox.critical(self._window, player_realms.OPTION, reason)
        return False

    def _check_variables(self) -> bool:
        """Refuse to run a bot whose variables have errors. Returns whether to proceed.

        Tells rather than asks, as the two checks before it do: a condition on an undeclared
        variable, or a write its type refuses, is a graph that cannot run as drawn
        (``bartlebot.bot.variables``). Warnings are the author's and never stop a run.
        """
        if self._documents.loaded_config is None:
            return True
        reason = variables.run_refusal(self._documents.loaded_config)
        if reason is None:
            return True
        logger.warning("Refusing to run: the bot's variables have errors")
        QMessageBox.critical(self._window, "Variables", reason)
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

    def _confirm_combat(self) -> bool:
        """Check the combat setup, and return whether to proceed.

        Only a fault, a choice the user made that cannot work, opens a dialog, and its
        default is Go back. A notice is a fallback in use, and the run starts. Only the
        fights the pick's default covers, and each duel leaving a slot to chance, are
        logged here: the fallbacks are in the line ``_log_combat_sources`` writes before
        the first node, and are never logged twice
        (``docs/COMBAT_CONFIG.md``, "Before a run: faults and notices").
        """
        from bartlebot.gui.dialogs import combat_faults_dialog  # noqa: PLC0415

        check = self._check_combat()
        for notice in check.unassigned:
            logger.info("Combat: %s", notice)
        for notice in check.slot_order:
            logger.warning("Combat: %s", notice)
        if not check.faults:
            return True
        for fault in check.faults:
            logger.warning("Combat strategy problem: %s", fault)
        if combat_faults_dialog.confirm_run(self._window, check.faults):
            logger.info("Running anyway with %d combat strategy problem(s)", len(check.faults))
            return True
        return False

    def _check_combat(self) -> CombatCheck:
        """The combat setup's faults and notices for the bot about to run.

        Parsing every DSL the run could reach takes a noticeable moment on a large
        library, so this runs under a wait cursor.
        """
        if self._documents.loaded_config is None:
            return CombatCheck()

        active_bcs_id = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
        num_clients = len(self._client_manager.clients)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return check_combat(active_bcs_id, num_clients, self._documents.loaded_config)
        finally:
            QApplication.restoreOverrideCursor()

    def _log_combat_sources(self) -> None:
        """Log one line naming where the run's fights get their duel strategies, before the first node."""
        if self._documents.loaded_config is None:
            return
        try:
            pick = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
            resolver = CombatResolver(self._documents.loaded_config, len(self._client_manager.clients), pick)
            logger.info(describe_run(resolver))
        except Exception:
            logger.warning("Could not name the run's combat strategies", exc_info=True)

    async def _start_bot(self) -> None:
        """Build the machine, run the pre-run setup, then the run. Stop works throughout."""
        if not self._client_manager.clients or self._documents.loaded_config is None:
            return
        machine = BotMachine(
            clients=self._client_manager.clients,
            config=self._documents.loaded_config.to_machine_config(node_specs=NODE_SPECS),
            node_registry=NODE_REGISTRY,
            bot_config=self._documents.loaded_config,
            on_state_change=self._on_machine_state_change,
            on_transition=self._on_machine_transition,
            on_drop=self._on_machine_drop,
            on_client_disconnected=self._on_machine_client_disconnected,
            on_restricted_zone=self._on_machine_restricted_zone,
            on_explicit_stop=self._on_machine_explicit_stop,
            on_stopped=self._on_machine_stopped,
            recovery_gap=self._client_manager.recovery_gap_for,
            on_variable_change=self._on_machine_variable_change,
            on_fight_fled=partial(self._on_machine_fight_fled, self._documents.loaded_config.id),
        )
        self._current_machine = machine
        self._client_manager.set_machine(machine)
        self._window.set_start_enabled(False)
        self._window.action_import.setEnabled(False)
        self._window.right_sidebar.drops.setup_clients(len(self._client_manager.clients))
        self._window.right_sidebar.drops.clear_drops()
        log_panel = self._window.right_sidebar.log_panel
        if SettingsManager.instance().clear_log_on_run:
            log_panel.clear()
        log_cb = log_panel.append_entry
        add_gui_callback(log_cb)
        self._window.bot_canvas.set_running()
        self._window.right_sidebar.clients.set_bot_running(True)
        self._window.left_sidebar.set_bot_running(True)
        self._hotkeys.set_armed(False)
        self._slot_hotkeys.set_armed(False)
        start_run_trace(self._player_number)
        try:
            # A run walks at the game's own speed and fights through its own nodes.
            await self._authoring.suspend_for_run()
            # Inside the try so the finally still tears down if the user stops during
            # a slow apply, and after add_gui_callback so failures reach this run's
            # log panel rather than the previous run's. Its own task, so Stop can cancel it.
            self._pre_run = asyncio.ensure_future(run_trace.traced(self._run_pre_run_setup(), "pre_run"))
            try:
                await self._pre_run
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise  # this task was cancelled, not just the setup
                logger.info("Stopped before the run began; the rest of the pre-run setup was skipped")
                return
            finally:
                self._pre_run = None
            if machine.is_stopped:
                return  # a stop that landed just as the setup finished
            self._log_combat_sources()
            # Here rather than at the top: importing wakepy costs startup a tenth of a second (#154).
            from wakepy import ActivationError, keep  # noqa: PLC0415

            try:
                with keep.running(on_fail="error"):
                    await machine.run()
            except ActivationError:
                logger.warning(
                    "wakepy could not acquire a sleep-prevention wake lock; bot will run without sleep guard"
                )
                await machine.run()
        finally:
            run_trace.stop()
            self._window.bot_canvas.set_idle()
            self._window.right_sidebar.clients.set_bot_running(False)
            self._window.left_sidebar.set_bot_running(False)
            self._authoring.resume_after_run()
            self._on_game_focus_changed(self._window.game_view.focused_hwnd)
            remove_gui_callback(log_cb)
            self._client_manager.set_machine(None)
            self._current_machine = None
            self._window.set_start_enabled(self._documents.start_enabled())
            self._window.action_import.setEnabled(True)

    async def _run_pre_run_setup(self) -> None:
        """Prepare every connected client before the FSM starts.

        Each step must catch its own failures and log rather than raise: a setup problem
        should degrade the run, not cancel it. None of them may show a blocking dialog
        either — this runs inside the _start_bot asyncio task, where a modal .exec()
        deadlocks the qasync loop under Python 3.13 (see CLAUDE.md).
        """
        with run_trace.span("pre_run.freecam"):
            await self._exit_pre_run_freecam()
        with run_trace.span("pre_run.decks"):
            await self._apply_pre_run_decks()
        with run_trace.span("pre_run.group"):
            states = await self._form_pre_run_group()
        with run_trace.span("pre_run.port_survey"):
            await self._survey_pre_run_ports(states)

    def _player_number(self, client) -> int | None:
        """*client*'s player number, P1 first, or None for one not hooked. For the run trace."""
        clients = self._client_manager.clients
        return clients.index(client) + 1 if client in clients else None

    async def _exit_pre_run_freecam(self) -> None:
        """Put every player's camera back on them before anything drives a client.

        First, because every later step and every node assumes the camera follows the
        player, and because the run locks the Exit Freecam button -- a camera left detached
        now would stay detached until the run ended. Exit only, never TP to Camera: the
        player is where the user left them, and a bot may well depend on that. Never raises.
        """
        try:
            exited = await self._window.right_sidebar.clients.exit_freecam()
        except Exception:
            logger.warning("Could not take players out of freecam before the run", exc_info=True)
            return
        if exited:
            logger.info("Exited freecam for %d player(s) before the run", exited)

    async def _survey_pre_run_ports(self, states: list | None) -> None:
        """Read who among the clients can port to whom, for zone navigation. See bartlebot.clients.port_map.

        Runs whether or not auto-grouping is on: a friends list is one text read per client,
        and a route planned around a port that cannot happen costs far more. Never raises —
        a node that finds no map surveys for itself.
        """
        from bartlebot.clients import port_map  # noqa: PLC0415

        clients = self._client_manager.clients
        machine = self._current_machine
        if machine is None or len(clients) < 2:
            return
        try:
            machine.port_map = await port_map.survey(clients, states=states)
        except Exception:
            logger.warning("Port survey failed — zone navigation will survey when it needs one", exc_info=True)

    async def _form_pre_run_group(self) -> list | None:
        """Put all clients into one in-game group, if the user has that enabled.

        Group membership is lost whenever a client logs off or crashes, and it is what
        guarantees the first turn in combat, so it is re-established at the start of every
        run rather than assumed to have survived from the last one.

        Returns every client's group state as grouping left it, so the port survey can reuse
        them rather than opening every social window a second time: what the survey read when
        nothing needed changing, and GroupResult.states when form_group changed something.
        None when grouping is off or failed outright, and the survey then reads for itself.
        """
        if not SettingsManager.instance().auto_group_players:
            return None

        clients = self._client_manager.clients
        if len(clients) < 2:
            logger.debug("Skipping pre-run grouping — fewer than two clients connected")
            return None

        try:
            # Survey once and hand the result to form_group. Each survey opens and closes
            # the social window on every client, so letting form_group re-read would
            # double the slowest and most failure-prone part of this.
            with run_trace.span("group.read_states"):
                states = await group_manager.read_group_states(clients)
            if all(s.grouped for s in states) and group_manager.same_group(states) is not False:
                logger.debug("Skipping pre-run grouping — all clients are already in one group")
                return states
            with run_trace.span("group.resolve_names"):
                names = await wizard_names.resolve_names(clients)
            with run_trace.span("group.form"):
                result = await group_manager.form_group(
                    clients, names, states=states, safety_delay=SettingsManager.instance().default_safety_delay
                )
            if result.success:
                logger.info("Pre-run grouping: %s", result.summary())
            else:
                logger.warning(
                    "Pre-run grouping did not fully succeed (%s) — the run will continue ungrouped",
                    result.summary(),
                )
            return result.states
        except Exception:
            logger.warning("Pre-run grouping failed — the run will continue ungrouped", exc_info=True)
            return None

    async def _apply_pre_run_decks(self) -> None:
        """Apply saved deck presets to each connected client before the FSM starts."""
        from bartlebot.combat.strategy_store import load_bot_strategy  # noqa: PLC0415

        if self._documents.loaded_config is None:
            return
        active_bcs_id = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
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
        # on the loop thread. Measured at 1.88x for four clients, from before
        # wizwalker sent each window's messages from a thread of its own, when the
        # waits for the games' answers held the loop too.
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
            await apply_deck(client, deck_data, slot=slot)
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
        # Before the first node, Stop cuts the pre-run setup short rather than waiting out
        # every deck and the group: a user who stops this early has spotted something to fix.
        if self._pre_run is not None and not self._pre_run.done():
            self._pre_run.cancel()
        if self._current_machine:
            self._current_machine.stop()

    # ------------------------------------------------------------------
    # BotMachine callback receivers
    # ------------------------------------------------------------------

    def _on_machine_state_change(self, state_id: str) -> None:
        self._window.set_fsm_state(state_id)
        machine = self._current_machine
        if machine is not None:
            self._window.left_sidebar.variable_values.on_node_started(machine.node_type_of(state_id))

    def _on_machine_variable_change(self, name: str, old, new) -> None:
        self._window.left_sidebar.variable_values.on_variable_change(name, old, new)

    def _on_machine_transition(self, prev: str, result: str, next_: str) -> None:
        self._window.bot_canvas.on_transition(prev, result, next_)

    def _on_machine_drop(self, item: str, qty: int, client_idx: int) -> None:
        self._window.right_sidebar.drops.add_drop(item, qty, client_idx)

    def _on_machine_fight_fled(self, bot_id: str, state_id: str | None, level: Level) -> None:
        """Count a fight fled on a default, and have the Combat tab mark it (Stage 7 of epic #201).

        *bot_id* is the running bot's, bound when the machine is built, so a mark lands on
        the bot that fled even if another is open by the time the tab draws it.
        """
        if self._flee_marks.record(bot_id, state_id, level):
            self._window.left_sidebar.combat_tab.refresh_source()

    def _on_machine_client_disconnected(self, hwnd: int) -> None:
        """Called by the health monitor when a client becomes unresponsive.

        Force-closes the game window so that remove_dead_clients() detects the exit
        and triggers the reconnect procedure via _recover_removed. That reaping is
        keyed on the *process* exiting, not the window, so the close has to be seen
        through: a crashing client parks on an error popup and never exits on WM_CLOSE
        alone, which used to leave recovery waiting forever.
        """
        logger.warning("Client hwnd=%d flagged unresponsive — closing it", hwnd)
        task = asyncio.create_task(self._close_dead_client(hwnd))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    def _on_machine_explicit_stop(self, reason: str, state_label: str, result: str) -> None:
        """Called when a node returns a value its author marked as a deliberate stop.

        The machine has already stopped itself. What is left is telling the user why,
        which is the whole point of the feature -- the case it was built for is
        check_zone finding the wrong world, where a run that merely ends is
        indistinguishable from one that crashed.

        Deferred through the event loop for the Python 3.13 re-entrancy trap in
        CLAUDE.md, exactly as the restricted-zone explanation is.
        """
        body = f"The bot stopped at {state_label}, which returned {result}."
        if reason:
            body += "\n\n" + reason
        QTimer.singleShot(0, lambda: QMessageBox.information(self._window, "Bot Stopped", body))

    def _on_machine_stopped(self, reason: str) -> None:
        """Called when the machine stops itself for a reason the user should see (the flee limit).

        Deferred through the event loop for the same re-entrancy reason as
        _on_machine_explicit_stop.
        """
        QTimer.singleShot(0, lambda: QMessageBox.information(self._window, "Bot Stopped", reason))

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
