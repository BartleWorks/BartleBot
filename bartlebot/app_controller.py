from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.bot import variables
from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig
from bartlebot.clients import group_manager, recovery_readiness, run_probe
from bartlebot.clients.authoring_tools import SLOT_KEYS, AuthoringAction, AuthoringTools, slot_keys_armed, source_index
from bartlebot.clients.client_manager import ClientManager
from bartlebot.clients.install_location import apply_install
from bartlebot.clients.window_kill import force_close_client
from bartlebot.clients.wizwalker_cache import apply_cache_dir
from bartlebot.combat import strategy_store
from bartlebot.combat.combat_resolution import CombatResolver, Level, describe_run
from bartlebot.combat.combat_warnings import CombatCheck, check_combat
from bartlebot.combat.deck_io import DeckApplyReport, apply_deck, read_deck
from bartlebot.combat.flee_marks import FleeMarks
from bartlebot.content import bot_delete, bot_fork, bot_roots, new_bot, share_export, share_import
from bartlebot.content.bot_tags import normalize_tags, suggested_tags, wants_rewards_prompt
from bartlebot.content.store_roots import DEV_ENV_VAR, Origin, dev_mode, pill_text
from bartlebot.content.subgraphs.subgraph_signature import signature_changes
from bartlebot.content.subgraphs.subgraph_signature import warning_text as signature_warning_text
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary, bots_linking, count_subgraph_uses
from bartlebot.content.subgraphs.subgraphs import (
    ComposeReport,
    compose_bot,
    config_to_definition,
    definition_change,
    definition_to_config,
)
from bartlebot.content.templates import setup_steps, template_diff, template_gallery, template_store
from bartlebot.game import restricted_zones, wizard_names, zone_names
from bartlebot.gui.canvas import recording
from bartlebot.gui.canvas.bot_canvas import BotMode, CanvasDocument
from bartlebot.gui.combat_strategies.deck_link import DeckClientLink
from bartlebot.gui.combat_strategies.dialog import DialogMode
from bartlebot.gui.debug.window_show_trace import WindowShowTracer
from bartlebot.gui.dialogs.export_dialog import run_export
from bartlebot.gui.dialogs.first_run_dialog import FirstRunDialog, FirstRunPage, needs_asking, setup_pages
from bartlebot.gui.dialogs.import_dialog import TITLE as IMPORT_TITLE
from bartlebot.gui.dialogs.import_dialog import ask_for_file, confirm_import
from bartlebot.gui.dialogs.make_copy_dialog import TITLE as MAKE_COPY_TITLE
from bartlebot.gui.dialogs.make_copy_dialog import MakeCopyDialog
from bartlebot.gui.dialogs.new_bot_dialog import NewBotDialog
from bartlebot.gui.dialogs.rewards_tag_dialog import RewardsChoice, RewardsTagDialog
from bartlebot.gui.dialogs.templates_dialog import TemplatesDialog, TemplatesVerb
from bartlebot.gui.global_hotkeys import ID_BASE_SPEED_AUTHORING, GlobalHotkeys
from bartlebot.gui.logo import install_app_icon, refresh_taskbar_icon
from bartlebot.gui.main_window import MainWindow
from bartlebot.gui.share_drop import ShareFileDrops, is_share_file
from bartlebot.gui.single_instance import LaunchQueue, SingleInstance, raise_window
from bartlebot.gui.update_prompt import UpdatePrompt
from bartlebot.launcher.launch_profile import restore_game_prefs, restore_ui_size
from bartlebot.logging_setup import add_gui_callback, remove_gui_callback
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS
from bartlebot.nodes.timing import DECK_APPLY_TIMEOUT, DECK_IMPORT_TIMEOUT
from bartlebot.options import option_values, player_realms
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.preferences import PreferenceStore
from bartlebot.shared.vendor import PRODUCT, VENDOR

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from bartlebot.shared.strategy_models import DeckData

#: What a PlayerCard shows for a zone it could not read; never a real zone name.
_ZONE_PLACEHOLDER = "—"

#: An autosave of a definition document is named for the definition, not a file.
_DEFINITION_AUTOSAVE_PREFIX = "subgraph_"

#: Nor is a template draft's: every draft is a ``draft.json``, so its stem says nothing.
_TEMPLATE_AUTOSAVE_PREFIX = "template_"

#: What the header and the window title call a template draft's file.
_DRAFT_FILE_LABEL = "template draft"

#: Player slots with a deck card on the Combat tab; a group is at most four wizards, so
#: Import all reads no client past the fourth.
_DECK_SLOTS = 4

#: Why a deck import or apply failed, when the log has the traceback and the user does not.
_DECK_JOB_FAILED = "failed — see the log for details"

#: Said under every deck failure: a job that dies part-way leaves the game's window as it was.
_DECK_WINDOW_NOTE = "The game's deck window may be left open or partially edited — check the client before retrying."


@dataclass
class _DocumentFrame:
    """A document set aside while a definition is open on top of it.

    Everything the controller holds for the open document -- the canvas's own state,
    the file it came from, whether it has unsaved changes -- so going back puts it
    exactly as it was.
    """

    doc: CanvasDocument
    path: str | None
    unsaved: bool
    clean: dict | None = None


def _file_snapshot(config: BotConfig | None) -> dict | None:
    """What ``config`` would write to disk: the baseline an autosave is compared against.

    Taken wherever a document is opened or saved. The dirty flag alone cannot say
    whether anything changed -- leaving Edit mode, or an edit typed and reverted,
    raises it too (#122) -- so an autosave that would only restate this is skipped.
    """
    return config.file_dump() if config is not None else None


#: Every export's title, and what it says on a template, which is shared after 1.0.0 (#165).
EXPORT_TITLE = "Export"
TEMPLATE_EXPORT_TEXT = "Templates can't be shared yet. Export a bot made from one instead."


def _matches_clean(config: BotConfig | None, snapshot: dict | None) -> bool:
    """Whether ``config`` is still what was last opened or saved. False when there is no snapshot."""
    return snapshot is not None and _file_snapshot(config) == snapshot


def _same_bot_content(autosave_path: Path, original_path: Path) -> bool:
    """Whether a recovery file holds the same bot as its original. False when either will not load.

    Both go through ``BotConfig`` rather than being compared as text, so a file an
    older version wrote -- different formatting, a default spelled out -- still matches.
    """
    try:
        return BotConfig.load(autosave_path).file_dump() == BotConfig.load(original_path).file_dump()
    except Exception:
        logger.debug("Could not compare %s with %s", autosave_path, original_path, exc_info=True)
        return False


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


def _share_files(args: list[str]) -> list[str]:
    """The arguments that name a share file that is there, in order."""
    return [arg for arg in args if is_share_file(arg) and Path(arg).is_file()]


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
        self._loaded_config: BotConfig | None = None
        self._last_save_path: str | None = None
        # Whether the open bot is an Official one, opened from the install directory: it
        # is never edited or saved in place, and Edit mode offers Make my own copy (#149).
        self._official_open: bool = False
        # One view of the subgraph library for the session, reloaded on every open and
        # before every run so a definition edited on disk reaches the bot that links it.
        self._subgraph_library = SubgraphLibrary()
        # Where the template store is: the project root, or a test's tmp_path.
        self._templates_root: Path | None = None
        # The user root the template store writes, when not the default; a test's tmp_path.
        self._templates_user_root: Path | None = None
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

        # Documents under the open one, bottom first: a bot, then each definition
        # opened from it. Empty while a bot, or a definition opened on its own, is on top.
        self._doc_stack: list[_DocumentFrame] = []

        # Auto-save state
        self._has_unsaved_changes: bool = False
        # The open document as last opened or saved; None when there is no such state
        # to return to (a recovered file). See _file_snapshot.
        self._clean_snapshot: dict | None = None
        # The id of the open bot whose author chose Save Without at the Rewards prompt:
        # asked once per open, and remembered nowhere else. See rewards_tag_dialog.
        self._rewards_declined_for: str | None = None
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

        # The authoring utilities (#81): Bar A's buttons, and the hotkeys that fire them
        # from inside a game window. See bartlebot.clients.authoring_tools and gui.global_hotkeys.
        self._authoring = AuthoringTools(
            clients=lambda: self._client_manager.clients,
            bot_config=lambda: self._loaded_config,
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
        self._reconfigure_autosave_timer()
        self._prune_dead_strategy_settings()
        self._restore_game_display_settings()
        self._check_crash_recovery()
        # A share file dropped anywhere on the window imports it (#171); one named at
        # launch, as a double-clicked file is (#27), once the window is up.
        self._share_drops = ShareFileDrops(self)
        self._share_drops.files_dropped.connect(self._import_dropped)
        self._app.installEventFilter(self._share_drops)
        self._wire_updates()
        QTimer.singleShot(0, self._after_window_shown)

    def _restore_game_display_settings(self) -> None:
        """Give the game back the display settings a previous session overwrote.

        A launch below 800x600 has to shrink the game's own HUD to match (see
        bartlebot/game/game_sizing.py), and every launch retiles the resolution and forces
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
        w.action_new.triggered.connect(lambda: self._new_bot())
        w.action_open.triggered.connect(lambda: self._open_bot())
        w.action_close.triggered.connect(lambda: self._close_bot())
        w.action_save.triggered.connect(lambda: self._save_bot())
        w.action_save_as.triggered.connect(lambda: self._save_bot_as())
        w.action_templates.triggered.connect(lambda: self._open_templates_dialog())
        w.action_export.triggered.connect(lambda: self._export_open_document())
        w.action_import.triggered.connect(lambda: self._import_share_file())
        w.action_setup.triggered.connect(lambda: self._rerun_setup())
        w.open_recent_requested.connect(self._open_bot)

        # Canvas signals
        w.bot_canvas.config_changed.connect(self._on_config_changed)
        # A finished template review, or an undo across one: the base moved.
        w.bot_canvas.template_ref_changed.connect(self._refresh_template_context)
        w.bot_canvas.mode_changed.connect(self._on_mode_changed)
        w.bot_canvas.run_requested.connect(self._on_run_requested)
        w.bot_canvas.pause_requested.connect(self._pause_bot)
        w.bot_canvas.resume_requested.connect(self._resume_bot)
        w.bot_canvas.stop_button.clicked.connect(self._stop_bot)
        w.bot_canvas.save_done.connect(self._save_and_done)
        w.bot_canvas.open_definition_requested.connect(self._open_definition)
        w.bot_canvas.definition_back_requested.connect(self._definition_back)
        w.bot_canvas.definition_save_requested.connect(self._on_definition_save_requested)
        w.bot_canvas.definition_save_as_requested.connect(lambda: self._save_definition_as())
        w.bot_canvas.definition_revert_requested.connect(lambda: self._revert_definition())
        w.bot_canvas.delete_subgraph_requested.connect(lambda sid: self._delete_subgraph(sid))
        w.bot_canvas.rename_subgraph_requested.connect(lambda sid: self._rename_subgraph(sid))
        w.bot_canvas.export_bot_requested.connect(lambda path: self._export_bot(path))
        w.bot_canvas.delete_bot_requested.connect(lambda path: self._delete_bot(path))
        w.bot_canvas.export_subgraph_requested.connect(lambda sid: self._export_subgraph(sid))
        w.bot_canvas.document_back_requested.connect(self._back_to_document)
        w.bot_canvas.template_save_requested.connect(lambda: self._save_bot())
        w.bot_canvas.template_release_requested.connect(lambda: self._release_template())
        w.bot_canvas.template_close_requested.connect(lambda: self._close_bot())
        w.bot_canvas.copy_requested.connect(lambda: self._make_own_copy())
        w.bot_canvas.set_clients_getter(lambda: self._client_manager.clients)
        # Ctrl+R records from the game window last in focus, as Bar A's buttons act on it.
        w.bot_canvas.set_preferred_player_getter(
            lambda: source_index(self._client_manager.clients, w.game_view.last_focused_hwnd)
        )
        w.bot_canvas.set_subgraph_library(self._subgraph_library)
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
        w.left_sidebar.edit_starting.connect(w.bot_canvas.note_edit_starting)
        w.left_sidebar.declarations_changed.connect(w.bot_canvas.reshow_properties)
        # A declaration edit -- an option's or a variable's -- changes what validation finds
        # without touching the graph, so nothing on the canvas would recheck it. Only the
        # sidebar's: the canvas rechecks its own edits already.
        w.left_sidebar.config_changed.connect(w.bot_canvas.revalidate)
        w.right_sidebar.config_changed.connect(self._on_config_changed)

        # Deck import / apply requests from Combat tab
        w.left_sidebar.combat_tab.import_deck_requested.connect(self._on_import_deck_requested)
        w.left_sidebar.combat_tab.apply_deck_requested.connect(self._on_apply_deck_requested)
        w.left_sidebar.combat_tab.import_all_decks_requested.connect(self._on_import_all_decks_requested)
        w.left_sidebar.combat_tab.apply_all_decks_requested.connect(self._on_apply_all_decks_requested)
        w.left_sidebar.combat_tab.export_bot_strategy_requested.connect(lambda bcs: self._export_bot_strategy(bcs))
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
        # list did not change at all. See bartlebot.clients.tile_slots.
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
        w.settings_applied.connect(self._reconfigure_autosave_timer)
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
        if not self._close_definitions() or not self._confirm_unsaved("Quit BartleBot"):
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
        self._window.authoring_bar.set_state(
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

    @property
    def subgraph_library(self) -> SubgraphLibrary:
        """The session's view of the two subgraph roots. Tests swap in a ``tmp_path`` one."""
        return self._subgraph_library

    @subgraph_library.setter
    def subgraph_library(self, library: SubgraphLibrary) -> None:
        self._subgraph_library = library

    def _compose(self, config: BotConfig) -> tuple[BotConfig, ComposeReport]:
        """Recompose every linked region of ``config`` from the library, reloaded first.

        The result is what the app holds in memory; the file the config came from is
        never written here (see docs/SUBGRAPHS.md). ``compose_bot`` raising is a bug
        in composition, not in the library, and a bug there must not make a bot
        unopenable: it is logged and the snapshot in the file is used as-is.
        """
        self._subgraph_library.reload()
        try:
            return compose_bot(config, self._subgraph_library)
        except Exception:
            logger.exception("Composing subgraphs failed; using the bot file's snapshot")
            return config, ComposeReport()

    def _recompose_before_run(self) -> bool:
        """Refresh linked regions from the library before a run. Returns whether to proceed.

        Silent by design: one log line per region that changed and the banner entry
        the canvas draws from the report, never a dialog -- see docs/SUBGRAPHS.md.
        The one refusal is a cycle, which has no snapshot worth running. A changed
        region swaps the in-memory config, exactly as an undo does, and marks the bot
        unsaved so the refreshed snapshot reaches disk on the next save.
        """
        if self._loaded_config is None:
            return True
        before = self._loaded_config
        composed, report = self._compose(before)
        canvas = self._window.bot_canvas
        if composed is not before and composed.model_dump() != before.model_dump():
            for region_id, label in _changed_linked_regions(before, composed):
                status = report.regions.get(region_id)
                version = status.library_version if status is not None else None
                logger.info("Subgraph region '%s' updated to library version %s", label, version)
            canvas.replace_config(composed, "Update subgraphs")
            self._loaded_config = composed
            # An Official bot runs the refreshed regions without anything to save (#149).
            self._mark_unsaved()
        canvas.set_compose_report(report)
        labels = {r.id: (r.label or r.id) for r in composed.regions}
        cycles = [(labels.get(rid, rid), s.reasons) for rid, s in report.regions.items() if s.kind == "cycle"]
        if not cycles:
            return True
        lines = "\n".join(f"\u2022 {label}: {'; '.join(reasons)}" for label, reasons in cycles)
        logger.critical("Refusing to run: %d subgraph region(s) contain a cycle", len(cycles))
        QMessageBox.critical(
            self._window,
            "Subgraph Cycle",
            "This bot cannot be run. A linked subgraph contains itself:\n\n"
            f"{lines}\n\n"
            "Fix the definition in the subgraph library, or detach the region.",
        )
        return False

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _new_bot(self) -> bool:
        """New Bot: ask for a starting point and the bot's details, then save the bot and open it for editing.

        Saved before it is opened, so the file exists from the start: a plain Save can
        never land on a template, and a bot abandoned half set up stays in the library.
        False when nothing was created.
        """
        if not self._close_definitions():
            return False
        # Written to the user's own folder, offering every world folder either root has.
        bots_dir = bot_roots.user_bots_dir()
        dialog = NewBotDialog(
            new_bot.starting_points(self._templates_root, self._templates_user_root),
            bots_dir,
            self._window,
            initial_folder=self._new_bot_folder(),
            folder_sources=(bot_roots.shipped_bots_dir(),),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if not self._confirm_unsaved("New Bot"):
            return False
        # Composed before it is written, so the file holds what opening it would make
        # of it and the first Save after that changes nothing.
        config, _report = self._compose(new_bot.create_bot(dialog.start, dialog.details()))
        path = dialog.target_path()
        try:
            config.save(path)
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to create bot", str(e))
            return False
        self._open_bot(str(path), mode=BotMode.EDIT)
        return True

    def _new_bot_folder(self) -> str | None:
        """The library folder New Bot opens on: the one being looked at, else the open bot's."""
        looking_at = self._window.bot_canvas.library_folder()
        if looking_at is not None:
            return looking_at
        return _world_folder(self._last_save_path)

    def _open_bot(self, path: str | None = None, *, mode: BotMode = BotMode.RUN) -> None:
        if not self._close_definitions():
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self._window, "Open Bot File", "", "Bot Files (*.json)")
        if not path:
            return
        if not self._confirm_unsaved("Open Bot"):
            return
        try:
            config = BotConfig.load(path)
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to load bot", str(e))
            return
        if config.is_template and not self._may_edit_template(config, path):
            return
        config, report = self._compose(config)
        old_save_path = self._last_save_path
        self._delete_autosave(old_save_path)
        self._has_unsaved_changes = False
        self._loaded_config = config
        self._last_save_path = path
        self._rewards_declined_for = None
        # An Official bot is read-only and only ever opens in Run mode (#149).
        self._official_open = not config.is_template and bot_roots.is_shipped(path)
        if self._official_open:
            mode = BotMode.RUN
        self._window.set_bot_name(config.name, self._file_label(config, path))
        self._window.bot_canvas.set_read_only(self._official_open)
        self._window.bot_canvas.setup(config, mode=mode)
        # After setup and compose, which the open does not count as edits either.
        self._clean_snapshot = _file_snapshot(config)
        self._window.bot_canvas.set_compose_report(report)
        self._refresh_template_context()
        self._window.left_sidebar.set_config(config)
        self._window.right_sidebar.set_config(config)
        self._enable_save_actions()
        self._window.set_close_enabled(True)
        self._window.set_start_enabled(self._start_enabled())
        if not config.is_template:
            # A draft is reached through File › Templates…, never as a recent bot.
            self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        self._window.bot_canvas.refresh_placeholder()

    def _close_bot(self) -> None:
        if self._loaded_config is None:
            return
        if self._loaded_config.is_definition:
            # Close is the document on top: a definition goes back to what it was opened from.
            self._definition_back()
            return
        if not self._confirm_unsaved("Close Bot"):
            return
        self._delete_autosave(stem=self._autosave_stem())
        self._loaded_config = None
        self._last_save_path = None
        self._official_open = False
        self._has_unsaved_changes = False
        self._clean_snapshot = None
        self._rewards_declined_for = None
        self._window.bot_canvas.close_bot()
        self._window.left_sidebar.set_config(None)
        self._window.right_sidebar.set_config(None)
        self._window.set_bot_name(None, None)
        self._window.set_save_enabled(False)
        self._window.set_close_enabled(False)
        self._window.set_start_enabled(False)

    def _confirm_unsaved(self, title: str) -> bool:
        """Ask about the open bot's unsaved edits before something replaces it. False to stay put.

        Save saves, and stays put if the save is cancelled or fails. Discard drops the edits
        and their recovery file, so the next launch does not offer them back. Open, New Bot
        and Quit used to replace an edited bot without asking, deleting its recovery file on
        the way (#154). The flag is raised by edits that change nothing, too: ask only when
        one did. A definition on top is closed first, by ``_close_definitions``.
        """
        config = self._loaded_config
        if config is None or not self._has_unsaved_changes or _matches_clean(config, self._clean_snapshot):
            return True
        reply = QMessageBox.question(
            self._window,
            title,
            f"Save your changes to '{config.name}' first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            return self._save_bot() and not self._has_unsaved_changes
        if reply != QMessageBox.StandardButton.Discard:
            return False
        self._delete_autosave(stem=self._autosave_stem())
        self._has_unsaved_changes = False
        return True

    def _save_and_done(self) -> None:
        """Save & Done: back to Run mode, but only once the save has gone through.

        A save the author cancels -- at the Rewards prompt, or in the file dialog --
        leaves them where they were, editing.
        """
        if self._save_bot():
            self._window.bot_canvas.exit_edit_mode(saved=True)

    def _confirm_rewards_tags(self) -> bool:
        """Ask for a Rewards tag if the bot has none. False when the author cancels the save.

        Asked once per open: Save Without is remembered against the bot's id until it is
        closed or reopened. A template draft is never asked.
        """
        config = self._loaded_config
        if (
            config is None
            or config.id == self._rewards_declined_for
            or not wants_rewards_prompt(config.tags, config.is_template)
        ):
            return True
        dialog = RewardsTagDialog(suggested_tags(config.states), self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if dialog.choice is RewardsChoice.TAGGED:
            config.tags = normalize_tags([*config.tags, *dialog.selected_tags()])
            self._window.bot_canvas.refresh_tags()
        else:
            self._rewards_declined_for = config.id
        return True

    def _save_bot(self) -> bool:
        """Save the open document. False when nothing was written: cancelled, refused or failed."""
        if self._loaded_config is None:
            return False
        if self._loaded_config.is_definition:
            return self._save_definition()
        if self._read_only_open():
            # An Official bot is never changed, so there is nothing to write: Save As
            # makes a plain copy, and Edit mode offers Make my own copy (#149).
            return False
        if self._last_save_path is None:
            return self._save_bot_as()
        if not self._loaded_config.is_template and not bot_roots.may_write(self._last_save_path):
            # An Official bot, or one recovered from a crash in it: the install directory is
            # replaced by every update, so the save goes to a copy in the user's own folder.
            return self._save_bot_as()
        if self._loaded_config.is_template:
            refused = template_store.draft_name_refused(self._loaded_config, **self._template_roots())
            if refused:
                QMessageBox.information(self._window, "Save Template", refused)
                return False
        if not self._confirm_rewards_tags():
            return False
        try:
            if not self._loaded_config.is_template:
                # A template keeps no history: a released version is hashed by its bytes.
                bot_roots.record_save(self._loaded_config, self._last_save_path)
            self._loaded_config.save(self._last_save_path)
            self._has_unsaved_changes = False
            self._clean_snapshot = _file_snapshot(self._loaded_config)
            if self._loaded_config.is_template:
                self._delete_autosave(stem=self._autosave_stem())
            else:
                self._delete_autosave()
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to save bot", str(e))
            return False
        return True

    def _save_bot_as(self) -> bool:
        """Save the open bot to a file the user names. False when nothing was written."""
        if self._loaded_config is None:
            return False
        if self._loaded_config.is_definition:
            # A definition lives in the library under its id, not in a file the user names.
            return self._save_definition()
        if self._loaded_config.is_template:
            # A copy would be a second draft of one template, or a bot carrying
            # template_meta; a bot is started from a template with New Bot instead.
            QMessageBox.information(
                self._window,
                "Save Bot As",
                "A template draft is only saved in place. Release it to make a version New Bot offers.",
            )
            return False
        path, _ = QFileDialog.getSaveFileName(
            self._window, "Save Bot As", str(_save_as_start(self._last_save_path)), "Bot Files (*.json)"
        )
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".json"
        if not bot_roots.may_write(path):
            QMessageBox.information(
                self._window,
                "Save Bot As",
                "That folder belongs to BartleBot's installation, which every update replaces, "
                "so a bot saved there would be lost. Choose a folder of your own.",
            )
            return False
        # After the file dialog, not before: cancelling the dialog should ask nothing.
        if not self._confirm_rewards_tags():
            return False
        old_save_path = self._last_save_path
        # A fork gets its own identity. The copy would otherwise share the original's
        # id, and with it every value the user has stored against that bot -- and the
        # remembered combat strategy, which is a fork's to choose again.
        # Its history goes too: what the original was copied from is not the copy's own past.
        old_id = self._loaded_config.id
        old_history = self._loaded_config.history
        old_sent = self._loaded_config.sent_sha256
        self._loaded_config.id = str(uuid4())
        self._loaded_config.history = []
        self._loaded_config.sent_sha256 = None
        try:
            self._loaded_config.save(path)
        except Exception as e:
            # Nothing was written, so the file on disk is still the original and the
            # loaded bot is still that bot. Keeping the minted id here would orphan
            # every value the user has stored against it for the rest of the session.
            self._loaded_config.id = old_id
            self._loaded_config.history = old_history
            self._loaded_config.sent_sha256 = old_sent
            QMessageBox.critical(self._window, "Failed to save bot", str(e))
            return False
        if self._rewards_declined_for == old_id:
            # Still the same open document, under its new identity.
            self._rewards_declined_for = self._loaded_config.id
        self._last_save_path = path
        self._has_unsaved_changes = False
        self._clean_snapshot = _file_snapshot(self._loaded_config)
        self._delete_autosave(old_save_path)
        # The copy is the user's own: editable, and saved in place from now on.
        self._official_open = False
        self._window.bot_canvas.set_read_only(False)
        self._window.set_save_enabled(True)
        self._window.set_bot_name(self._loaded_config.name, Path(path).name)
        self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        self._window.bot_canvas.refresh_placeholder()
        # A new id resolves against a different set of stored values -- none, for a
        # fresh fork -- so every surface displaying one has to re-resolve. Without
        # this, Option Values goes on showing the original's overrides against a bot that
        # does not have them, and only a reopen tells the truth.
        self._window.left_sidebar.refresh_options(self._loaded_config)
        # The combat half of the same re-resolve: the active strategy and the BCS
        # list are both keyed on the id, and a fork inherits neither.
        self._window.left_sidebar.rebind_identity()
        # And the template half: the canvas holds the base version's setup steps and the
        # update waiting against the bot's id, so under the new one it had neither, and
        # a copy of a half set up bot lost its Setup errors until it was reopened.
        self._refresh_template_context()
        return True

    def _make_own_copy(self) -> bool:
        """Make my own copy of the open Official bot, and open the copy in Edit mode (#149).

        Asked when Edit mode is asked for on an Official bot. The copy is a fork
        (:mod:`bartlebot.content.bot_fork`): a new id recording the Official bot's, in the user's
        own folder, written at once as New Bot writes a new bot. Unlike Save As, it takes
        every option value, Bot Combat Strategy and the active pick with it, so it runs
        exactly as the Official bot did. False when cancelled or nothing was written.
        """
        config, path = self._loaded_config, self._last_save_path
        if config is None or path is None or not self._read_only_open():
            return False
        dialog = MakeCopyDialog(config.name, self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            # The file as it ships, not the composed copy in memory: its hash is the record.
            official = BotConfig.load(path)
            fork, _report = self._compose(bot_fork.make_fork(official, dialog.name))
            target = bot_fork.fork_path(path, bot_roots.user_bots_dir(), _world_folder(path))
            target.parent.mkdir(parents=True, exist_ok=True)
            fork.save(target)
        except Exception as e:
            logger.exception("Could not make a copy of %s", path)
            QMessageBox.critical(self._window, MAKE_COPY_TITLE, f"Could not make your copy:\n\n{e}")
            return False
        logger.info("Made a copy of the Official bot '%s' at %s", official.name, target)
        try:
            bot_fork.carry_over(official.id, fork.id, SettingsManager.instance())
        except Exception:
            # The copy exists and opens; what did not come with it is the user's to set again.
            logger.exception("Could not carry the option values and combat setup over to the copy")
            QMessageBox.warning(
                self._window,
                MAKE_COPY_TITLE,
                "Your copy was made, but not all of its option values and Bot Combat Strategies "
                "could be copied from the Official bot. See the log for details.",
            )
        self._open_bot(str(target), mode=BotMode.EDIT)
        return True

    # ------------------------------------------------------------------
    # Export (#170)
    # ------------------------------------------------------------------

    def _export_sources(self) -> share_export.Sources:
        """Where an export reads from: the session's subgraph library, reloaded, and the real stores."""
        self._subgraph_library.reload()
        return share_export.Sources(subgraph_library=self._subgraph_library)

    def _export_open_document(self) -> Path | None:
        """File › Export…: the open bot, or the open subgraph definition, as saved. The path written, or None."""
        config = self._loaded_config
        if config is None:
            return None
        if config.is_template:
            QMessageBox.information(self._window, EXPORT_TITLE, TEMPLATE_EXPORT_TEXT)
            return None
        if not self._saved_for_export():
            return None
        config = self._loaded_config
        if config.is_definition:
            return self._export_subgraph(config.definition.id)
        if self._last_save_path is None:
            return None
        return self._export_bot(self._last_save_path)

    def _saved_for_export(self) -> bool:
        """Whether the open document is saved, offering to save it first. A share file carries what is saved."""
        if not self._has_unsaved_changes or _matches_clean(self._loaded_config, self._clean_snapshot):
            return True
        reply = QMessageBox.question(
            self._window,
            EXPORT_TITLE,
            "Export sends what is saved. Save your changes first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Save and self._save_bot()

    def _active_bot_strategy(self, bot_id: str):
        """The Bot Combat Strategy a bot's export offers: the user's active pick for it, or None.

        The pick chooses what is offered and never travels itself.
        """
        active = SettingsManager.instance().get_active_strategy(bot_id)
        if active is None:
            return None
        try:
            bcs = strategy_store.load_bot_strategy(active)
        except Exception:
            logger.warning("Could not read the active Bot Combat Strategy %s to export it", active, exc_info=True)
            return None
        return bcs if bcs.bot_id == bot_id else None

    def _export_bot(self, path: str) -> Path | None:
        """Export… on a bot: the file at ``path``, its subgraphs, and on request its Bot Combat Strategy."""
        try:
            bot = BotConfig.load(path)
        except Exception as e:
            QMessageBox.critical(self._window, EXPORT_TITLE, f"The bot could not be read:\n\n{e}")
            return None
        if bot.is_template:
            QMessageBox.information(self._window, EXPORT_TITLE, TEMPLATE_EXPORT_TEXT)
            return None
        sources = self._export_sources()
        official = bot_roots.is_shipped(path)
        combat = self._active_bot_strategy(bot.id)

        folder = _world_folder(path)

        def build(choices):
            return share_export.export_bot(
                bot,
                sources,
                official=official,
                combat=combat if choices.include_combat else None,
                include_decks=choices.include_decks,
                folder=folder,
            )

        return run_export(
            self._window, build, combat_name=combat.name if combat else None, offers_decks=combat is not None
        )

    def _export_subgraph(self, subgraph_id: str) -> Path | None:
        """Export… on a subgraph: the definition as saved, and the subgraphs it nests."""
        sources = self._export_sources()
        defn = self._subgraph_library.get(subgraph_id)
        if defn is None:
            QMessageBox.information(self._window, EXPORT_TITLE, "That subgraph could not be read from the library.")
            return None
        return run_export(self._window, lambda _choices: share_export.export_subgraph(defn, sources))

    def _export_bot_strategy(self, bcs) -> Path | None:
        """Export… on a Bot Combat Strategy: as saved, with the strategies it links, its bot, and on request its decks."""
        try:
            # The Combat tab writes each change as it is made, so the file is current.
            bcs = strategy_store.load_bot_strategy(bcs.id)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Could not reread the Bot Combat Strategy %s to export it", bcs.id, exc_info=True)
        sources = self._export_sources()
        return run_export(
            self._window,
            lambda choices: share_export.export_bot_strategy(bcs, sources, include_decks=choices.include_decks),
            offers_decks=True,
        )

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
        self._import_from_arguments()

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

    def _import_from_arguments(self) -> None:
        """Import each share file named on the command line: how a double-clicked file arrives (#27)."""
        for path in _share_files(self._app.arguments()[1:]):
            self._import_share_file(path)

    def listen_for_launches(self, instance: SingleInstance) -> None:
        """Take what later launches hand over: a double-click while the app runs arrives here (#27).

        The window comes forward at once. The files wait for any modal dialog to close, so
        an import's summary never opens over setup or over another import's.
        """
        self._launches = LaunchQueue(lambda: QApplication.activeModalWidget() is not None)
        self._launches.ready.connect(self._import_handed_over)
        instance.arguments_received.connect(self._on_later_launch)

    def _on_later_launch(self, args: list[str]) -> None:
        raise_window(self._window)
        self._launches.put(args)

    def _import_handed_over(self, args: list[str]) -> None:
        """Each share file a later launch named, imported here as one named at this launch would be."""
        paths = _share_files(args)
        if not paths or self._import_blocked_by_run():
            return
        for path in paths:
            self._import_share_file(path)

    def _import_dropped(self, paths: list[str]) -> None:
        """Files dropped on the window, imported one after another."""
        if self._import_blocked_by_run():
            return
        for path in paths:
            self._import_share_file(path)

    def _import_blocked_by_run(self) -> bool:
        """Say to stop the bot first, and True, while one runs: an import would write what the run is using (#181)."""
        if self._current_machine is None:
            return False
        logger.warning("Not importing: a bot is running")
        QMessageBox.warning(self._window, IMPORT_TITLE, "Stop the running bot before importing. Nothing was imported.")
        return True

    def _import_share_file(self, path: str | None = None) -> share_import.ImportResult | None:
        """File › Import…, a dropped file, or one named at launch: the summary, then the write. What was written.

        Refused files say why and write nothing. Changing a choice in the summary
        re-matches the file, so what Import writes is what the summary showed last.
        """
        if self._import_blocked_by_run():
            return None
        if path is None:
            path = ask_for_file(self._window)
            if path is None:
                return None
        sources = self._export_sources()
        try:
            share = share_import.read(path)
            incoming = share_import.check(share, sources)
        except share_import.Refused as e:
            logger.warning("Not importing %s: %s", path, e)
            QMessageBox.warning(self._window, IMPORT_TITLE, str(e))
            return None
        try:
            plan = confirm_import(
                self._window, lambda choices: share_import.plan_import(share, sources, choices, incoming=incoming)
            )
        except Exception as e:
            logger.exception("Could not match %s against the library", path)
            QMessageBox.critical(self._window, IMPORT_TITLE, f"Could not read what this file would change:\n\n{e}")
            return None
        if plan is None:
            return None
        reopen = self._clear_for_import(plan)
        if reopen is False:
            return None
        result: share_import.ImportResult | None = None
        try:
            result = share_import.apply_import(plan, sources)
        except Exception as e:
            logger.exception("The import of %s stopped part way", path)
            QMessageBox.critical(
                self._window,
                IMPORT_TITLE,
                f"The import stopped part way, and nothing after this was written:\n\n{e}",
            )
        self._after_import(plan, result, reopen if isinstance(reopen, str) else None)
        return result

    def _open_document_ids(self) -> tuple[str | None, set[str]]:
        """``(the open bot's file, the ids of every open definition)``, the bot under any definitions."""
        configs = [frame.doc.config for frame in self._doc_stack]
        if self._loaded_config is not None:
            configs.append(self._loaded_config)
        definitions = {c.definition.id for c in configs if c.is_definition}
        bot_path = self._doc_stack[0].path if self._doc_stack else self._last_save_path
        if self._loaded_config is not None and not self._doc_stack and self._loaded_config.is_definition:
            bot_path = None
        return bot_path, definitions

    def _clear_for_import(self, plan: share_import.ImportPlan) -> str | bool:
        """Make way for an import that writes over an open document. False to stop; the bot file to reopen; or True.

        A definition being written over is closed first, asking about unsaved changes as
        going back always does. The open bot is reopened afterward, unless it has unsaved
        changes, which the import would leave out of step with its file: then nothing is written.
        """
        over = [r for r in plan.rows if r.action in (share_import.Action.UPDATE, share_import.Action.REPLACE)]
        if not over:
            return True
        bot_path, definitions = self._open_document_ids()
        if any(r.kind == share_export.SUBGRAPH and r.id in definitions for r in over) and not self._close_definitions():
            return False
        bot_row = next(
            (
                r
                for r in over
                if r.kind == share_export.BOT
                and bot_path is not None
                and r.holding is not None
                and r.holding.path is not None
                and Path(r.holding.path).resolve() == Path(bot_path).resolve()
            ),
            None,
        )
        if bot_row is None:
            return True
        if not self._close_definitions():
            return False
        if self._has_unsaved_changes and not _matches_clean(self._loaded_config, self._clean_snapshot):
            QMessageBox.information(
                self._window,
                IMPORT_TITLE,
                f"'{bot_row.name}' is open with unsaved changes, and this import would replace it. "
                "Save or close it, then import again.",
            )
            return False
        return str(bot_path)

    def _after_import(
        self, plan: share_import.ImportPlan, result: share_import.ImportResult | None, reopen: str | None
    ) -> None:
        """Bring every surface up to date with what was written, then show the main item where it went."""
        self._subgraph_library.reload()
        self._window.bot_canvas.refresh_subgraph_listing()
        if result is None:
            return
        share_import.assign_active_picks(result, SettingsManager.instance())
        if reopen is not None:
            self._open_bot(reopen)
        elif self._loaded_config is not None and not self._loaded_config.is_definition:
            # A Bot Combat Strategy for the open bot: its list, and maybe its pick, changed.
            if any(bot_id == self._loaded_config.id for _bcs, bot_id in result.strategies_added):
                self._window.left_sidebar.rebind_identity()
        self._select_imported(plan, result)

    def _select_imported(self, plan: share_import.ImportPlan, result: share_import.ImportResult) -> None:
        """Afterward the library selects the main item: a bot or subgraph in the bot library, a strategy in its own."""
        main = plan.main
        kind = plan.share.main.kind
        target = main.target_id if main is not None else plan.share.main.id
        if kind == share_export.BOT_COMBAT_STRATEGY:
            # A Bot Combat Strategy is shown with its bot, unless that bot is the one open.
            if main is None or (self._loaded_config is not None and self._loaded_config.id == main.item.bot_id):
                return
            kind, target = share_export.BOT, main.item.bot_id
        if kind == share_export.BOT:
            path = result.bot_paths.get(target)
            if path is None:
                found = self._export_sources().bot_file(target)
                path = found.path if found is not None else None
            open_bot = self._last_save_path if not self._doc_stack else self._doc_stack[0].path
            if path is not None and not (open_bot and Path(open_bot).resolve() == Path(path).resolve()):
                # The open bot is already in front of the user: reopened, never revealed.
                self._window.bot_canvas.reveal_in_library(path=path)
        elif kind == share_export.SUBGRAPH:
            self._window.bot_canvas.reveal_in_library(subgraph_id=target)
        else:
            strategy_kind = {
                share_export.DUEL_STRATEGY: strategy_store.StrategyKind.DUEL,
                share_export.PLAYER_STRATEGY: strategy_store.StrategyKind.PLAYER,
                share_export.DECK: strategy_store.StrategyKind.DECK,
            }[kind]
            self._window.left_sidebar.combat_tab.open_combat_dialog(
                strategy_kind, DialogMode.LIBRARY, current_id=target
            )

    # ------------------------------------------------------------------
    # Template drafts
    # ------------------------------------------------------------------

    @property
    def templates_root(self) -> Path | None:
        """The root the template store is under: None for the project root. Tests point it at a ``tmp_path``."""
        return self._templates_root

    @templates_root.setter
    def templates_root(self, root: Path | None) -> None:
        self._templates_root = root

    def _file_label(self, config: BotConfig, path: str | Path | None) -> str:
        """What the header calls the open document's file: a draft's name says nothing, every one is draft.json."""
        if config.is_template:
            return _DRAFT_FILE_LABEL
        return Path(path).name if path else "Untitled"

    @property
    def templates_user_root(self) -> Path | None:
        """The user root the template store writes: None for the default. Tests point it at a ``tmp_path``."""
        return self._templates_user_root

    @templates_user_root.setter
    def templates_user_root(self, root: Path | None) -> None:
        self._templates_user_root = root

    def _may_edit_template(self, config: BotConfig, path: str) -> bool:
        """Whether a template file may be opened for editing. Says why not.

        Only a draft, never a released version. The user's own template's draft always;
        an Official one's only in dev mode, where its maintainer edits it (#164).
        """
        if Path(path).name != template_store.DRAFT_FILE:
            why = f"This is a released version of the template '{config.name}', which is never edited."
            hint = "To start a bot from a template, use New Bot."
        elif dev_mode():
            return True
        else:
            template = template_store.find_template(config.id, self._templates_root, self._templates_user_root)
            if (
                template is not None
                and template.origin is Origin.USER
                and template.draft_path.resolve() == Path(path).resolve()
            ):
                return True
            why = f"This is the draft of the Official template '{config.name}', which changes only with an app update."
            hint = "To make one of your own from it, use Duplicate in File › Templates…."
        QMessageBox.information(self._window, "Open Bot", f"{why}\n\n{hint}")
        return False

    def _template_roots(self) -> dict[str, Path | None]:
        """The template store's two roots, as every store function takes them."""
        return {"root": self._templates_root, "user_root": self._templates_user_root}

    def _user_bots_dirs(self) -> tuple[Path, ...]:
        """Where the user's own bots are: what the Templates dialog counts, and Delete names."""
        return (bot_roots.user_bots_dir(),)

    def _template_gallery(self) -> list[template_gallery.GalleryEntry]:
        return template_gallery.gallery(self._user_bots_dirs(), dev=dev_mode(), **self._template_roots())

    def _open_templates_dialog(self) -> bool:
        """File › Templates…: every template, and the verbs that make templates of your own (#164).

        Edit, New and Duplicate end the dialog and open a draft for editing; Rename and
        Delete happen while it stays open. True when a draft was opened.
        """
        if not self._close_definitions():
            return False
        dialog = TemplatesDialog(
            self._template_gallery, self._window, rename=self._rename_template, delete=self._delete_template
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.request is None:
            return False
        request = dialog.request
        if request.verb is TemplatesVerb.NEW:
            return self._new_template()
        if request.verb is TemplatesVerb.DUPLICATE:
            return self._duplicate_template(request.entry.template)
        return self._edit_template(request.entry.template)

    def _edit_template(self, template: template_store.Template) -> bool:
        """Open ``template``'s draft as the top-level document, in Edit mode. False when it did not open."""
        open_now = self._open_template()
        if open_now is None or open_now.id != template.id:
            self._open_bot(str(template.draft_path), mode=BotMode.EDIT)
        return self._loaded_config is not None and self._loaded_config.id == template.id

    def _ask_template_name(self, title: str, label: str, text: str = "", template_id: str | None = None) -> str | None:
        """Ask for a template's name until it is one no other template has. None when cancelled or blank.

        A taken name asks again, with what was typed and a line saying whose name it is.
        ``template_id`` is the template being renamed, whose own name is never taken.
        """
        prompt = label
        while True:
            name, ok = QInputDialog.getText(self._window, title, prompt, text=text)
            name = name.strip()
            if not ok or not name:
                return None
            taken = template_store.name_clash(name, template_id, **self._template_roots())
            if taken is None:
                return name
            prompt = f"There is already a template called '{taken}'.\n\n{label}"
            text = name

    def _new_template(self) -> bool:
        """New: ask for a name, start a blank template of the user's own, and open its draft."""
        name = self._ask_template_name("New Template", "What is the template called?")
        if name is None:
            return False
        try:
            template = template_store.new_template(name, **self._template_roots())
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self._window, "New Template", f"Could not create the template:\n\n{exc}")
            return False
        return self._edit_template(template)

    def _duplicate_template(self, source: template_store.Template) -> bool:
        """Duplicate: fork ``source`` into a template of the user's own, and open its draft."""
        taken = [t.name for t in template_store.list_templates(**self._template_roots())]
        name = self._ask_template_name(
            "Duplicate Template",
            f"What is your copy of {source.name} called?",
            text=template_gallery.copy_name(source.name, taken),
        )
        if name is None:
            return False
        try:
            fork = template_store.duplicate_template(source, name, **self._template_roots())
        except (OSError, LookupError, ValueError) as exc:
            QMessageBox.critical(self._window, "Duplicate Template", f"Could not copy {source.name}:\n\n{exc}")
            return False
        return self._edit_template(fork)

    def _rename_template(self, entry: template_gallery.GalleryEntry) -> bool:
        """Rename the user's own template. True when it was renamed.

        An open draft of it takes the name too, so its next save keeps it rather than
        writing the old one back.
        """
        template = entry.template
        current = template_store.renamed_in_draft(template) or template.name
        name = self._ask_template_name("Rename Template", "Template name:", text=current, template_id=template.id)
        if name is None or name == current:
            return False
        try:
            template_store.rename_template(template.id, name, **self._template_roots())
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.warning(self._window, "Rename Template", f"Could not rename {template.name}:\n\n{exc}")
            return False
        open_draft = self._open_template()
        if open_draft is not None and open_draft.id == template.id:
            config = self._loaded_config
            config.name = name
            if not self._has_unsaved_changes:
                self._clean_snapshot = _file_snapshot(config)
            self._window.set_bot_name(config.name, self._file_label(config, self._last_save_path))
            self._window.bot_canvas.refresh_name()
        if template_store.rename_waits_for_release(template, **self._template_roots()):
            QMessageBox.information(
                self._window,
                "Rename Template",
                f"The draft is now called {name}. {template.name} has been released with the app, so its "
                "card keeps that name until you release the draft.",
            )
        return True

    def _delete_template(self, entry: template_gallery.GalleryEntry) -> bool:
        """Delete the user's own template, after naming the bots made from it. True when it was deleted.

        The bots keep running, without setup steps or update reviews. An open draft of it
        is closed first, unsaved changes and all: they belong to what is being deleted.
        """
        template = entry.template
        refused = template_store.deletion_refused(template, **self._template_roots())
        if refused:
            QMessageBox.information(self._window, "Delete Template", refused)
            return False
        bots = template_store.bots_based_on(template.id, *self._user_bots_dirs())
        if bots:
            listed = "\n".join(f"• {b}" for b in bots[:12])
            more = f"\n…and {len(bots) - 12} more" if len(bots) > 12 else ""
            made = (
                f"These bots were made from it:\n\n{listed}{more}\n\n"
                "They keep running, but without its setup steps or update reviews."
            )
        else:
            made = "None of your bots were made from it."
        reply = QMessageBox.warning(
            self._window,
            "Delete Template",
            f"Delete {template.name}, its draft and every version released from it?\n\n{made}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        open_draft = self._open_template()
        if open_draft is not None and open_draft.id == template.id:
            self._has_unsaved_changes = False
            self._close_bot()
            if self._loaded_config is not None:
                return False
        try:
            template_store.delete_template(template.id, **self._template_roots())
        except (OSError, PermissionError) as exc:
            QMessageBox.critical(self._window, "Delete Template", f"Could not delete {template.name}:\n\n{exc}")
            return False
        # A bot open now may have been made from it: its steps and review go with it.
        self._refresh_template_context()
        return True

    def _open_template(self) -> template_store.Template | None:
        """The template whose draft is the open document, or None."""
        config = self._loaded_config
        if config is None or not config.is_template or self._last_save_path is None:
            return None
        template = template_store.find_template(config.id, self._templates_root, self._templates_user_root)
        if template is None or Path(self._last_save_path).resolve() != template.draft_path.resolve():
            return None
        return template

    def _refresh_template_context(self) -> None:
        """Tell the canvas what a template is behind the open document.

        For a draft, what its crumb says and which ids it must not mint. For a bot made
        from a template, the setup steps of its base version, which it does not carry,
        and the update waiting for it when the template has released since.
        """
        config = self._loaded_config
        if config is None:
            return
        if not config.is_template:
            if not config.is_definition:
                canvas = self._window.bot_canvas
                canvas.set_base_setup_steps(
                    setup_steps.steps_for(config, self._templates_root, self._templates_user_root)
                )
                origin = (
                    template_store.find_template(config.template.id, self._templates_root, self._templates_user_root)
                    if config.template is not None
                    else None
                )
                canvas.set_template_origin(
                    origin.name if origin is not None else None,
                    config.template.version if origin else None,
                    origin.latest_version if origin else None,
                )
                # An Official bot gets its updates from the app, never through the review
                # (#149). Developer mode never marks one Official, so its maintainer still can.
                update = (
                    None
                    if self._official_open
                    else template_diff.load_update(config, self._templates_root, self._templates_user_root)
                )
                canvas.set_template_update(update)
            return
        template = self._open_template()
        if template is None:
            self._window.bot_canvas.set_template_context(None)
            return
        reserved = template_store.used_ids(template.id, self._templates_root, self._templates_user_root)
        self._window.bot_canvas.set_template_context(template.latest_version, reserved)

    def _release_template(self) -> bool:
        """Release the open draft as its template's next version. False when refused or cancelled.

        Release takes the draft as it is on disk, so unsaved changes are saved first or
        the release does not happen. The template store refuses anything else wrong,
        having written nothing.
        """
        config = self._loaded_config
        template = self._open_template()
        if config is None or template is None:
            QMessageBox.warning(
                self._window, "Release", "Only a template's draft, opened from File › Templates…, can be released."
            )
            return False
        if self._has_unsaved_changes and not _matches_clean(config, self._clean_snapshot):
            reply = QMessageBox.question(
                self._window,
                "Release",
                "Release takes the draft as saved. Save your changes first?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply != QMessageBox.StandardButton.Save or not self._save_bot():
                return False
        errors = self._window.bot_canvas.validation_errors()
        draft = BotConfig.load(template.draft_path)
        problems = template_store.release_problems(
            draft,
            template,
            errors,
            self._subgraph_library,
            root=self._templates_root,
            user_root=self._templates_user_root,
        )
        if problems:
            self._show_release_refused(template.name, problems)
            return False
        replaced = template_store.replaced_nodes(draft, template)
        if replaced and not self._confirm_replaced(template.name, template.latest_version, replaced):
            return False
        number = template_store.next_version(template)
        note, ok = QInputDialog.getText(
            self._window, "Release", f"Releasing {template.name} v{number}. What changed, in one line?"
        )
        if not ok:
            return False
        try:
            entry = template_store.release(
                template.id,
                note,
                errors,
                root=self._templates_root,
                user_root=self._templates_user_root,
                library=self._subgraph_library,
            )
        except template_store.ReleaseRefused as exc:
            self._show_release_refused(template.name, exc.problems)
            return False
        except OSError as exc:
            QMessageBox.critical(self._window, "Release", f"Could not release {template.name}:\n\n{exc}")
            return False
        self._refresh_template_context()
        QMessageBox.information(
            self._window, "Release", f"Released {template.name} v{entry.version}. New Bot offers it from now on."
        )
        return True

    def _confirm_replaced(self, name: str, latest: int | None, replaced: Sequence[str]) -> bool:
        """Ask before releasing a draft that replaces nodes rather than keeping them. True to go on."""
        listed = "\n".join(f"• {line}" for line in replaced)
        reply = QMessageBox.warning(
            self._window,
            "Release",
            f"This draft of {name} replaces nodes v{latest} has instead of keeping them:\n\n{listed}\n\n"
            "Bots are updated node by node, by id, so each of these reaches them as a removal and an "
            "addition, and any edge a user wired to the old node is dropped. Keeping the node and "
            "changing it would offer them the change instead.\n\nRelease anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _show_release_refused(self, name: str, problems: Sequence[str]) -> None:
        listed = "\n".join(f"\u2022 {p}" for p in problems[:12])
        more = f"\n\u2026and {len(problems) - 12} more" if len(problems) > 12 else ""
        # The page on screen draws what it is missing in red: a refused release, like a
        # refused save, is the moment to say so rather than before (#142).
        self._window.bot_canvas.param_panel.show_required_errors()
        QMessageBox.critical(self._window, "Release", f"{name} can't be released yet:\n\n{listed}{more}")

    # ------------------------------------------------------------------
    # Definition documents
    # ------------------------------------------------------------------

    @property
    def document_depth(self) -> int:
        """How many documents sit under the open one."""
        return len(self._doc_stack)

    def _open_definition(self, subgraph_id: str) -> bool:
        """Open a subgraph definition as a document, on top of whatever is open.

        The document underneath is set aside whole -- canvas, undo history, file,
        unsaved flag -- and comes back when this one closes. A definition already open
        further down is refused, since editing it twice at once has no good answer.
        """
        self._subgraph_library.reload()
        defn = self._subgraph_library.get(subgraph_id)
        if defn is None:
            QMessageBox.warning(self._window, "Open Definition", "That subgraph is not in the library.")
            return False
        if subgraph_id in self._open_definition_ids():
            QMessageBox.information(
                self._window, "Open Definition", f"'{defn.name}' is already open: go back to it to edit it."
            )
            return False
        self._push_current()
        self._show_definition(definition_to_config(defn), unsaved=False)
        logger.info("Opened subgraph '%s' v%d as a document", defn.name, defn.version)
        return True

    def _push_current(self) -> None:
        """Set the open document aside on the stack, if there is one."""
        if self._loaded_config is None:
            return
        # The one underneath stops autosaving while it is covered, so its latest
        # edits reach a recovery file now rather than being held only in memory.
        self._do_autosave()
        doc = self._window.bot_canvas.take_document()
        if doc is not None:
            self._doc_stack.append(
                _DocumentFrame(doc, self._last_save_path, self._has_unsaved_changes, self._clean_snapshot)
            )

    def _show_definition(self, config: BotConfig, *, unsaved: bool) -> None:
        canvas = self._window.bot_canvas
        config, report = self._compose(config)
        self._loaded_config = config
        self._last_save_path = None
        self._has_unsaved_changes = unsaved
        canvas.setup(config, mode=BotMode.EDIT)
        # A recovered definition has no saved state here to fall back to.
        self._clean_snapshot = None if unsaved else _file_snapshot(config)
        canvas.set_compose_report(report)
        self._refresh_definition_context()
        # The sidebars describe a bot: its options, combat, drops. A definition has none.
        self._window.left_sidebar.set_config(None)
        self._window.right_sidebar.set_config(None)
        self._window.set_bot_name(config.name, "subgraph")
        self._window.set_save_enabled(True)
        self._window.set_close_enabled(True)
        self._window.set_start_enabled(False)

    def _refresh_definition_context(self) -> None:
        """Hand the canvas the crumb's path -- every document under this one -- and what its header says."""
        config = self._loaded_config
        if config is None or config.definition is None:
            return
        path = [frame.doc.config.name for frame in self._doc_stack]
        origin = self._subgraph_library.origin(config.definition.id)
        used_by = self._bots_linking(config.definition.id)
        # "· Official updated" on a modified Official one whose Official one has changed
        # since, beside the Revert the header offers it in Edit mode (#169).
        updated = self._subgraph_library.official_updated(config.definition.id)
        self._window.bot_canvas.set_definition_context(
            path,
            origin=f"Source: {pill_text(origin, official_updated=updated)}",
            used_by=used_by,
            can_revert=origin == Origin.SHIPPED_OVERRIDE,
            can_rename=origin == Origin.USER,
        )

    def _bots_linking(self, subgraph_id: str) -> int:
        """How many bot files in the library link ``subgraph_id``."""
        return count_subgraph_uses(*bot_roots.library_dirs()).get(subgraph_id, 0)

    def _open_definition_ids(self) -> set[str]:
        """The ids of every definition open as a document, on top or set aside under it."""
        configs = [f.doc.config for f in self._doc_stack]
        if self._loaded_config is not None:
            configs.append(self._loaded_config)
        return {c.definition.id for c in configs if c.definition is not None}

    def _delete_subgraph(self, subgraph_id: str) -> bool:
        """Delete a definition from the library, from its card there. False when refused or cancelled.

        Deleting removes the user's file only (:mod:`bartlebot.content.store_roots`), so a
        modified Official one is reverted rather than deleted, and an unedited one has
        nothing to delete. What linked the definition is never broken by it: a bot keeps
        the snapshot it was saved with and runs that, flagged ``missing``, as does a bot
        linking another definition that nested it (docs/SUBGRAPHS.md). It is still
        asked first, naming them, because from then on none of them gets an update.
        Refused while the definition is open, since the document would save it back.
        """
        library = self._subgraph_library
        library.reload()
        defn = library.get(subgraph_id)
        origin = library.origin(subgraph_id)
        if defn is None or origin not in (Origin.USER, Origin.SHIPPED_OVERRIDE):
            return False
        revert = origin is Origin.SHIPPED_OVERRIDE
        title = "Revert to Official" if revert else "Delete Subgraph"
        verb = "revert" if revert else "delete"
        if subgraph_id in self._open_definition_ids():
            QMessageBox.information(self._window, title, f"'{defn.name}' is open. Close it first, then {verb} it.")
            return False
        if revert:
            # The id stays, so whatever links it follows the Official one from its next compose.
            text = (
                f"Revert '{defn.name}' to the Official version?\n\n"
                "Your changes to it are lost, and every bot linking it uses the Official version "
                "from now on. This can't be undone."
            )
        else:
            text = f"Delete '{defn.name}' from the library?"
            users = []
            if n := self._bots_linking(subgraph_id):
                users.append(f"{n} bot{'s' if n != 1 else ''}")
            users.extend(f"the subgraph '{d.name}'" for d in library.nesting(subgraph_id))
            if users:
                listed = users[0] if len(users) == 1 else f"{', '.join(users[:-1])} and {users[-1]}"
                text += (
                    f"\n\nIt is used by {listed}. Each keeps running the copy it was last saved with, "
                    "flagged as a missing subgraph, and gets no more changes to it."
                )
            text += "\n\nThis can't be undone."
        reply = QMessageBox.question(
            self._window,
            title,
            text,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return False
        try:
            library.delete(subgraph_id)
        except OSError as exc:
            QMessageBox.critical(self._window, title, f"Could not {verb} '{defn.name}':\n\n{exc}")
            return False
        # A recovery file holds edits to a definition that is no longer there to take them.
        self._delete_autosave(stem=f"{_DEFINITION_AUTOSAVE_PREFIX}{subgraph_id}")
        if revert:
            logger.info("Reverted subgraph '%s' to the Official version", defn.name)
        else:
            logger.info("Deleted subgraph '%s' from the library", defn.name)
        canvas = self._window.bot_canvas
        canvas.refresh_subgraph_listing()
        if self._loaded_config is not None:
            # The open document's banner says what its links compose to now. Its config
            # follows at the next open or run, as for any library change.
            _, report = self._compose(self._loaded_config)
            canvas.set_compose_report(report)
        return True

    def _delete_bot(self, path: str) -> bool:
        """Delete a bot from its card in the library. False when refused, cancelled or it failed.

        The user's own bots only: an Official one ships in the install directory and would
        be back with the next launch. Nothing links a bot, so no other bot is affected,
        a fork included (:mod:`bartlebot.content.bot_delete`); the confirmation names the
        Bot Combat Strategies that go with it, as they are the user's work. Refused while
        the bot is open, since the document would save it back.
        """
        title = "Delete Bot"
        if bot_roots.is_shipped(path):
            return False
        try:
            plan = bot_delete.plan_deletion(path)
        except Exception as e:
            QMessageBox.critical(self._window, title, f"The bot could not be read:\n\n{e}")
            return False
        if self._last_save_path is not None and _same_file(self._last_save_path, path):
            QMessageBox.information(self._window, title, f"'{plan.name}' is open. Close it first, then delete it.")
            return False
        text = f"Delete the bot '{plan.name}'?"
        if n := len(plan.strategy_ids):
            text += f"\n\nIts {n} Bot Combat Strateg{'ies' if n != 1 else 'y'} will be deleted with it."
        text += "\n\nThis can't be undone."
        reply = QMessageBox.question(
            self._window,
            title,
            text,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return False
        settings = SettingsManager.instance()
        try:
            bot_delete.delete_bot(plan, settings)
        except OSError as exc:
            QMessageBox.critical(self._window, title, f"Could not delete '{plan.name}':\n\n{exc}")
            return False
        # A recovery file holds edits to a bot that is no longer there to take them. Autosaves
        # are named by file stem, so one the open document is writing under is left alone.
        if self._loaded_config is None or self._autosave_stem() != Path(path).stem:
            self._delete_autosave(path)
        self._window.update_recent_menu(settings.recent_bot_files)
        self._window.bot_canvas.refresh_subgraph_listing()
        return True

    def _rename_subgraph(self, subgraph_id: str) -> bool:
        """Rename a definition, and edit its description, from its card in the library. False when refused or cancelled.

        Written at the version it has: a bot links the id, and an instance keeps the
        label it was given when it was placed, so nothing that links it changes and
        none of them is offered an update (:func:`~bartlebot.content.subgraphs.subgraphs.definition_change`).
        The user's own definitions only. An Official one's name is the shipped file's,
        and renaming it would make a copy that stops its updates arriving -- in dev
        mode every definition is the user's, so that is where an Official one is renamed.
        Refused while the definition is open, whose header renames it instead.
        """
        library = self._subgraph_library
        library.reload()
        defn = library.get(subgraph_id)
        if defn is None or library.origin(subgraph_id) is not Origin.USER:
            return False
        title = "Rename Subgraph"
        if subgraph_id in self._open_definition_ids():
            QMessageBox.information(
                self._window, title, f"'{defn.name}' is open. Rename it there, in its header in Edit mode."
            )
            return False
        from bartlebot.gui.canvas.subgraph_widgets import SubgraphMetaDialog  # noqa: PLC0415

        dlg = SubgraphMetaDialog(
            self._window, name=defn.name, description=defn.description or "", title=title, commit_text="Rename"
        )
        if not dlg.exec():
            return False
        if (dlg.name, dlg.description) == (defn.name, defn.description):
            return False
        renamed = defn.model_copy(update={"name": dlg.name, "description": dlg.description})
        try:
            library.save(renamed)
        except OSError as exc:
            QMessageBox.critical(self._window, title, f"Could not rename '{defn.name}':\n\n{exc}")
            return False
        if renamed.name != defn.name:
            logger.info("Renamed subgraph '%s' to '%s'", defn.name, renamed.name)
        else:
            logger.info("Changed the description of subgraph '%s'", defn.name)
        self._window.bot_canvas.refresh_subgraph_listing()
        return True

    def _on_definition_save_requested(self, then_back: bool) -> None:
        if self._save_definition() and then_back:
            self._definition_back()

    def _save_definition(self) -> bool:
        """Write the open definition to the library as its next version. False when refused or cancelled.

        Only a change to what runs makes a new version (#158): a definition identical
        to the library's copy is not written at all, and one that differs only in
        layout or in name and description (:func:`~bartlebot.content.subgraphs.subgraphs.definition_change`)
        is written at the version it has. Refused while
        the document has errors: a definition that cannot compose must not reach the
        bots that link it. A shipped definition is saved as the user's
        own copy, which shadows it until reverted -- asked first, since from then on
        the shipped one's updates stop arriving.
        """
        config = self._loaded_config
        if config is None or config.definition is None:
            return False
        if self._refused_for_errors(config, "Save to Library"):
            return False
        library = self._subgraph_library
        library.reload()
        sid = config.definition.id
        try:
            defn = config_to_definition(config)
        except ValueError as exc:
            QMessageBox.critical(self._window, "Save to Library", str(exc))
            return False
        stored = library.get(sid)
        change = definition_change(stored, defn) if stored is not None else "content"
        if change == "none":
            # Nothing to write: a new version would send every linking bot an update
            # that changes nothing, and an Official one would gain a copy that shadows it.
            config.definition.version = stored.version
            self._mark_definition_saved(config)
            logger.info("Subgraph '%s' is unchanged; kept version %d", config.name, stored.version)
            return True
        if library.origin(sid) == Origin.SHIPPED and not dev_mode():
            reply = QMessageBox.question(
                self._window,
                "Save to Library",
                f"'{config.name}' is Official. Saving makes a modified copy of it, which every bot "
                "linking it uses from now on, in place of the Official one's updates.\n\n"
                "Save the modified copy?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply != QMessageBox.StandardButton.Save:
                return False
        if stored is not None and not self._confirm_signature_change(stored, defn):
            return False
        if change in ("layout", "meta"):
            version = stored.version
        else:
            version = max(config.definition.version, stored.version if stored is not None else 0) + 1
        defn = defn.model_copy(update={"version": version})
        try:
            library.save(defn)
        except Exception as exc:
            QMessageBox.critical(self._window, "Save to Library", f"Could not save '{config.name}':\n\n{exc}")
            return False
        config.definition.version = version
        self._mark_definition_saved(config)
        logger.info("Saved subgraph '%s' version %d to the library", defn.name, version)
        return True

    def _confirm_signature_change(self, stored, defn) -> bool:
        """Warn before a save that changes what a linking bot depends on (#169). False when cancelled.

        Asked only when the save removes or renames an input or a port, or adds an input
        with no default (:mod:`bartlebot.content.subgraphs.subgraph_signature`), and only when a bot or
        another definition links this one: with nothing linking it, nothing can break.
        A warning, never a refusal: a bot that cannot take the change is held on its
        snapshot.
        """
        changes = signature_changes(stored, defn)
        if not changes:
            return True
        users = bots_linking(defn.id, *bot_roots.library_dirs())
        users += [f"the subgraph '{d.name}'" for d in self._subgraph_library.nesting(defn.id)]
        if not users:
            return True
        reply = QMessageBox.warning(
            self._window,
            "Save to Library",
            signature_warning_text(defn.name, changes, users),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply != QMessageBox.StandardButton.Save:
            return False
        logger.info("Saving subgraph '%s' with %d signature change(s) used by %d", defn.name, len(changes), len(users))
        return True

    def _mark_definition_saved(self, config: BotConfig) -> None:
        self._has_unsaved_changes = False
        self._clean_snapshot = _file_snapshot(config)
        self._delete_autosave(stem=self._autosave_stem())
        self._refresh_definition_context()

    def _refused_for_errors(self, config: BotConfig, title: str) -> bool:
        """Say what blocks saving the open definition, if anything does. True when it is refused."""
        errors = self._window.bot_canvas.validation_errors()
        if not errors:
            return False
        listed = "\n".join(f"\u2022 {e}" for e in errors[:12])
        more = f"\n\u2026and {len(errors) - 12} more" if len(errors) > 12 else ""
        self._window.bot_canvas.param_panel.show_required_errors()
        QMessageBox.critical(self._window, title, f"Fix these before saving '{config.name}':\n\n{listed}{more}")
        return True

    def _save_definition_as(
        self, name: str | None = None, description: str | None = None, categories: list[str] | None = None
    ) -> bool:
        """Save the open definition as a new one, and go on editing the new one. False when refused or cancelled.

        Save As, as for a file: the copy gets its own id at version 1, and the
        document becomes the copy. The bots linking the original -- the one this was
        opened from included -- keep linking it, since the copy is a new thing nothing
        uses yet. Asks for a name, description and categories, starting from the
        original's, unless a name is given; ``categories`` None keeps the original's.
        """
        config = self._loaded_config
        if config is None or config.definition is None:
            return False
        if self._refused_for_errors(config, "Save as New Subgraph"):
            return False
        if name is None:
            from bartlebot.gui.canvas.subgraph_widgets import SubgraphMetaDialog  # noqa: PLC0415

            dlg = SubgraphMetaDialog(
                self._window,
                name=f"{config.name} (copy)",
                description=config.description or "",
                categories=list(config.definition.tags),
                title="Save as New Subgraph",
            )
            if not dlg.exec():
                return False
            name, description, categories = dlg.name, dlg.description, dlg.categories
        try:
            defn = config_to_definition(config, version=1)
        except ValueError as exc:
            QMessageBox.critical(self._window, "Save as New Subgraph", str(exc))
            return False
        update = {"id": str(uuid4()), "name": name, "description": description}
        if categories is not None:
            update["tags"] = list(categories)
        defn = defn.model_copy(update=update)
        try:
            self._subgraph_library.save(defn)
        except Exception as exc:
            QMessageBox.critical(self._window, "Save as New Subgraph", f"Could not save '{name}':\n\n{exc}")
            return False
        # The unsaved edits went into the copy; the original's recovery file has nothing left to recover.
        self._delete_autosave(stem=self._autosave_stem())
        self._reshow_definition(defn)
        logger.info("Saved subgraph '%s' as a new subgraph", defn.name)
        return True

    def _revert_definition(self) -> bool:
        """Discard the user's copy of an Official definition, and show the Official one. False when cancelled.

        Deleting the user copy is the revert (:mod:`bartlebot.content.store_roots`). Every bot
        linking it follows the Official one from its next compose, and unsaved edits
        in the document go with the copy, so it asks first.
        """
        config = self._loaded_config
        if config is None or config.definition is None:
            return False
        sid = config.definition.id
        library = self._subgraph_library
        if library.origin(sid) != Origin.SHIPPED_OVERRIDE:
            return False
        reply = QMessageBox.question(
            self._window,
            "Revert to Official",
            f"Revert '{config.name}' to the Official version?\n\n"
            "Your changes to it are lost, and every bot linking it uses the Official version "
            "from now on. This can't be undone.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return False
        library.delete(sid)
        shipped = library.get(sid)
        if shipped is None:
            QMessageBox.critical(
                self._window, "Revert to Official", f"The Official '{config.name}' could not be loaded."
            )
            return False
        self._delete_autosave(stem=self._autosave_stem())
        self._reshow_definition(shipped)
        logger.info("Reverted subgraph '%s' to the Official version %d", shipped.name, shipped.version)
        return True

    def _reshow_definition(self, defn) -> None:
        """Put ``defn`` in place of the open definition, saved, keeping the camera.

        A fresh document rather than an edit to this one: its undo history belongs to
        a definition with a different id or content, and undoing into it would save
        over the wrong file.
        """
        canvas = self._window.bot_canvas
        camera = canvas.view.camera_state()
        self._show_definition(definition_to_config(defn), unsaved=False)
        canvas.view.restore_camera(camera)

    def _back_to_document(self, depth: int) -> bool:
        """Close definitions until ``depth`` documents sit under the open one, asking at each. False if one was cancelled."""
        while len(self._doc_stack) > depth and self._loaded_config is not None and self._loaded_config.is_definition:
            if not self._definition_back():
                return False
        return True

    def _definition_back(self) -> bool:
        """Close the open definition, asking about unsaved changes first. False when the user cancels."""
        config = self._loaded_config
        if config is None or config.definition is None:
            return True
        if self._has_unsaved_changes and not _matches_clean(config, self._clean_snapshot):
            reply = QMessageBox.question(
                self._window,
                "Close Definition",
                f"Save your changes to '{config.name}' to the library?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return False
            if reply == QMessageBox.StandardButton.Save and not self._save_definition():
                return False
        self._leave_definition()
        return True

    def _leave_definition(self) -> None:
        """Drop the open definition and put back the document underneath, recomposed from the library."""
        stem = self._autosave_stem()
        self._delete_autosave(stem=stem)
        canvas = self._window.bot_canvas
        if not self._doc_stack:
            self._loaded_config = None
            self._last_save_path = None
            self._official_open = False
            self._has_unsaved_changes = False
            self._clean_snapshot = None
            canvas.close_bot()
            self._window.set_bot_name(None, None)
            self._window.set_save_enabled(False)
            self._window.set_close_enabled(False)
            self._window.set_start_enabled(False)
            return
        frame = self._doc_stack.pop()
        canvas.restore_document(frame.doc)
        config = frame.doc.config
        self._loaded_config = config
        self._last_save_path = frame.path
        self._has_unsaved_changes = frame.unsaved
        self._clean_snapshot = frame.clean
        # What was just edited is linked from here, most likely: take it now, as a
        # run would, so the call node shows the new ports straight away.
        composed, report = self._compose(config)
        if composed is not config and composed.model_dump() != config.model_dump():
            canvas.replace_config(composed, "Update subgraphs")
            self._loaded_config = composed
            self._has_unsaved_changes = True
        canvas.set_compose_report(report)
        if self._read_only_open():
            # An Official bot has nothing to save: what composed differently is shown, not kept.
            self._has_unsaved_changes = False
        loaded = self._loaded_config
        if loaded.is_definition:
            self._refresh_definition_context()
            self._window.set_bot_name(loaded.name, "subgraph")
            self._window.set_start_enabled(False)
        else:
            self._window.left_sidebar.set_config(loaded)
            self._window.right_sidebar.set_config(loaded)
            self._window.left_sidebar.set_editable(canvas.mode == BotMode.EDIT)
            self._window.right_sidebar.set_editable(canvas.mode == BotMode.EDIT)
            self._window.set_bot_name(loaded.name, self._file_label(loaded, self._last_save_path))
            self._refresh_template_context()
            self._window.set_start_enabled(self._start_enabled())
        self._enable_save_actions()
        self._window.set_close_enabled(True)

    def _close_definitions(self) -> bool:
        """Close every definition document open, top down. False if the user cancelled one."""
        while self._loaded_config is not None and self._loaded_config.is_definition:
            if not self._definition_back():
                return False
        return True

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
        if self._loaded_config is None:
            return None
        bcs_id = SettingsManager.instance().get_active_strategy(self._loaded_config.id)
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
        from bartlebot.shared.strategy_models import DeckData  # noqa: PLC0415

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
        self._on_config_changed()

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
            title = f"Deck for P{slot} not applied"
            body = "None of the saved deck's cards are in this wizard's spellbook, so the current deck was left alone."
            self._show_deck_notice(title, f"{body}\n\n" + "\n".join(lines), warn=True)
            return

        self._show_deck_notice(
            f"Deck applied for P{slot}, with changes",
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
                heading = f"P{slot}: applied, with changes"
                warn = warn or bool(report.skipped or report.archmastery_failed)
            else:
                heading = f"P{slot}: not applied — none of the saved deck's cards are in this wizard's spellbook"
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
        return "\n".join(f"Deck {action} for P{slot} {reason}." for slot, reason in sorted(failures.items()))

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
        if not self._recompose_before_run():
            return
        if not self._check_restricted_zones():
            return
        if not self._check_player_realms():
            return
        if not self._check_variables():
            return
        from bartlebot.gui.dialogs.safety_warning_dialog import level_scaling_players  # noqa: PLC0415

        clients = self._client_manager.clients
        players = level_scaling_players(self._loaded_config, len(clients))
        # Who is in a fight, and whether a level scaling player already has an elixir, are
        # memory reads, which are async, and what they feed is a blocking dialog, which must
        # not open inside a task (see "qasync + Python 3.13" in bartlebot/gui/CLAUDE.md). So
        # the reads run as a task and the rest of the sequence runs from its done callback,
        # which the loop calls outside any task.
        if self._run_probe is not None and not self._run_probe.done():
            return  # a second press while the first is still reading
        self._run_probe = asyncio.create_task(run_probe.probe_before_run(clients, players))
        self._run_probe.add_done_callback(self._on_run_probe_done)

    def _on_run_probe_done(self, probe: asyncio.Task) -> None:
        """Carry on with the run request once the reads are in. A failed read stops nothing."""
        if probe.cancelled():
            return
        if probe.exception() is not None:
            logger.warning("Could not read the players before the run", exc_info=probe.exception())
            self._finish_run_request(())
            return
        result: run_probe.RunProbe = probe.result()
        if result.fighting:
            self._refuse_while_fighting(result.fighting)
            return
        self._finish_run_request(result.elixir_players)

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

    def _finish_run_request(self, elixir_players) -> None:
        """The rest of the run sequence: the dialogs that ask, then the run itself."""
        if not self._client_manager.clients or self._loaded_config is None:
            return
        from bartlebot.gui.dialogs.safety_warning_dialog import (  # noqa: PLC0415
            SafetyWarningDialog,
            collect_safety_warnings,
        )

        safety_warnings = collect_safety_warnings(self._loaded_config, self._client_manager.clients, elixir_players)
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
        if self._loaded_config is None:
            return True
        found = restricted_zones.check_bot_config(self._loaded_config)
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
        if self._loaded_config is None:
            return True
        value = option_values.option_value(self._loaded_config, player_realms.OPTION, player_realms.TOGETHER)
        reason = player_realms.run_refusal(self._loaded_config, value)
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
        if self._loaded_config is None:
            return True
        reason = variables.run_refusal(self._loaded_config)
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
        if self._loaded_config is None:
            return CombatCheck()

        active_bcs_id = SettingsManager.instance().get_active_strategy(self._loaded_config.id)
        num_clients = len(self._client_manager.clients)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return check_combat(active_bcs_id, num_clients, self._loaded_config)
        finally:
            QApplication.restoreOverrideCursor()

    def _log_combat_sources(self) -> None:
        """Log one line naming where the run's fights get their duel strategies, before the first node."""
        if self._loaded_config is None:
            return
        try:
            pick = SettingsManager.instance().get_active_strategy(self._loaded_config.id)
            resolver = CombatResolver(self._loaded_config, len(self._client_manager.clients), pick)
            logger.info(describe_run(resolver))
        except Exception:
            logger.warning("Could not name the run's combat strategies", exc_info=True)

    async def _start_bot(self) -> None:
        """Build the machine, run the pre-run setup, then the run. Stop works throughout."""
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
            on_explicit_stop=self._on_machine_explicit_stop,
            on_stopped=self._on_machine_stopped,
            recovery_gap=self._client_manager.recovery_gap_for,
            on_variable_change=self._on_machine_variable_change,
            on_fight_fled=partial(self._on_machine_fight_fled, self._loaded_config.id),
        )
        self._current_machine = machine
        self._client_manager.set_machine(machine)
        self._window.set_start_enabled(False)
        self._window.action_import.setEnabled(False)
        self._window.right_sidebar.drops.setup_clients(len(self._client_manager.clients))
        self._window.right_sidebar.drops.clear_drops()
        self._window.right_sidebar.log_panel.on_run_started()
        log_cb = self._window.right_sidebar.log_panel.append_log_formatted
        add_gui_callback(log_cb)
        self._window.bot_canvas.set_running()
        self._window.right_sidebar.clients.set_bot_running(True)
        self._window.left_sidebar.set_bot_running(True)
        self._hotkeys.set_armed(False)
        self._slot_hotkeys.set_armed(False)
        try:
            # A run walks at the game's own speed and fights through its own nodes.
            await self._authoring.suspend_for_run()
            # Inside the try so the finally still tears down if the user stops during
            # a slow apply, and after add_gui_callback so failures reach this run's
            # log panel rather than the previous run's. Its own task, so Stop can cancel it.
            self._pre_run = asyncio.ensure_future(self._run_pre_run_setup())
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
            self._window.bot_canvas.set_idle()
            self._window.right_sidebar.clients.set_bot_running(False)
            self._window.left_sidebar.set_bot_running(False)
            self._authoring.resume_after_run()
            self._on_game_focus_changed(self._window.game_view.focused_hwnd)
            remove_gui_callback(log_cb)
            self._client_manager.set_machine(None)
            self._current_machine = None
            self._window.set_start_enabled(self._start_enabled())
            self._window.action_import.setEnabled(True)

    async def _run_pre_run_setup(self) -> None:
        """Prepare every connected client before the FSM starts.

        Each step must catch its own failures and log rather than raise: a setup problem
        should degrade the run, not cancel it. None of them may show a blocking dialog
        either — this runs inside the _start_bot asyncio task, where a modal .exec()
        deadlocks the qasync loop under Python 3.13 (see CLAUDE.md).
        """
        await self._exit_pre_run_freecam()
        await self._apply_pre_run_decks()
        states = await self._form_pre_run_group()
        await self._survey_pre_run_ports(states)

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
            states = await group_manager.read_group_states(clients)
            if all(s.grouped for s in states) and group_manager.same_group(states) is not False:
                logger.debug("Skipping pre-run grouping — all clients are already in one group")
                return states
            names = await wizard_names.resolve_names(clients)
            result = await group_manager.form_group(clients, names, states=states)
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
        # four clients.
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
        and triggers the reconnect procedure via _on_clients_removed. That reaping is
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

    def _on_metadata_changed(self) -> None:
        """Mark the bot dirty for an edit that cannot have changed its option set.

        The description is the case this exists for.  ``_on_config_changed`` ends in
        ``refresh_options``, which recomposes both option surfaces and rebuilds every
        row when the composed list moved -- around 55ms on a 67-state bot, and it was
        being paid once per *character* typed into the description box, which is what
        made that field unusable.  Nothing composes an option from prose, so there is
        nothing here to rebuild: the guard in ``refresh_options`` would now
        skip it either way, and this still saves the recompose and the description's
        own round trip.
        """
        self._mark_unsaved()

    def _mark_unsaved(self) -> None:
        """Raise the unsaved flag and enable Save -- except on an Official bot, which is never saved (#149)."""
        if self._read_only_open():
            return
        self._has_unsaved_changes = True
        self._window.set_save_enabled(True)

    def _enable_save_actions(self) -> None:
        """Enable Save and Save As for the open document. On an Official bot only Save As,
        which makes a copy: there is nothing of its own to save."""
        if self._read_only_open():
            self._window.set_save_enabled(False, save_as=True)
        else:
            self._window.set_save_enabled(True)

    def _read_only_open(self) -> bool:
        """Whether the document on top is an Official bot. A definition opened over one is not."""
        config = self._loaded_config
        return self._official_open and config is not None and not config.is_definition

    def _on_config_changed(self) -> None:
        canvas_config = self._window.bot_canvas.config
        if canvas_config is not None:
            # First, so the flag below is raised for the document now on top.
            self._loaded_config = canvas_config
        self._mark_unsaved()
        if canvas_config is not None and canvas_config.is_definition:
            # No sidebars to follow and no options to compose: a definition has neither.
            self._window.bot_canvas.refresh_initial(canvas_config.initial)
            self._window.bot_canvas.refresh_edge_flags()
            self._window.set_bot_name(canvas_config.name, "subgraph")
            return
        if canvas_config is not None:
            # Undo/redo replaces the canvas's config object; the sidebar has to
            # follow it or its edits go nowhere.  No-op the rest of the time.
            self._window.left_sidebar.sync_config(canvas_config, self._window.bot_canvas.mode == BotMode.EDIT)
            self._window.right_sidebar.sync_config(canvas_config)
        if self._loaded_config is not None:
            self._window.bot_canvas.refresh_initial(self._loaded_config.initial)
            self._window.bot_canvas.refresh_recovery(self._loaded_config.recovery_state)
            # An edge is drawn dashed while it is conditional, and deleting an option
            # strips its conditions off every transition that guarded on it -- from a
            # surface with no way to reach the canvas.  Cheap: each edge is only
            # repainted when its own flag actually moved.
            self._window.bot_canvas.refresh_edge_flags()
            self._window.left_sidebar.refresh_options(self._loaded_config)
            self._window.set_bot_name(
                self._loaded_config.name, self._file_label(self._loaded_config, self._last_save_path)
            )

    def _on_mode_changed(self, mode: BotMode) -> None:
        self._window.left_sidebar.set_editable(mode == BotMode.EDIT)
        self._window.right_sidebar.set_editable(mode == BotMode.EDIT)
        if mode == BotMode.RUN:
            self._window.set_start_enabled(self._start_enabled())

    def _start_enabled(self) -> bool:
        return (
            bool(self._client_manager.clients)
            and self._loaded_config is not None
            and not self._loaded_config.is_definition
        )

    def _on_clients_changed(self, clients: list) -> None:
        self._window.set_start_enabled(self._start_enabled())
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
        """Read the theme setting and apply palette + stylesheet to the app."""
        from bartlebot.gui.theming.theme import (  # noqa: PLC0415
            THEME,
            build_custom_theme,
            set_theme,
            set_theme_instance,
        )

        sm = SettingsManager.instance()
        theme_mode = sm.get_general("theme", default="system")
        if theme_mode == "custom":
            overrides = sm.get("custom_theme", default={})
            set_theme_instance(build_custom_theme(overrides))
        else:
            set_theme(theme_mode)
        self._app.setPalette(THEME.build_palette())
        # The app font, so every widget that does not set its own size is the body
        # size in px rather than the platform's own 9pt. Passed without a class name,
        # which is what clears the per-class fonts Windows gives menus and tooltips.
        self._app.setFont(THEME.ui_font())
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

    def _autosave_stem(self) -> str:
        """The recovery file's stem: the bot file's, or for a definition document the definition's id."""
        config = self._loaded_config
        if config is not None and config.definition is not None:
            return f"{_DEFINITION_AUTOSAVE_PREFIX}{config.definition.id}"
        if config is not None and config.is_template:
            return f"{_TEMPLATE_AUTOSAVE_PREFIX}{config.id}"
        return Path(self._last_save_path).stem if self._last_save_path else "_unsaved"

    def _autosave_path(self) -> Path:
        """Derive the recovery file path for the currently loaded config."""
        return SettingsManager.autosave_dir() / f"{self._autosave_stem()}.autosave.json"

    def _do_autosave(self) -> None:
        """Timer callback — write a recovery file if there are unsaved changes."""
        if not self._has_unsaved_changes or self._loaded_config is None:
            return
        # The config is read-only while the bot is running, nothing new to save.
        if self._current_machine is not None:
            return
        if _matches_clean(self._loaded_config, self._clean_snapshot):
            # Nothing to recover: no edit was made, or every one was undone. A copy
            # identical to the file would only be offered back at the next launch (#122).
            self._has_unsaved_changes = False
            self._delete_autosave(stem=self._autosave_stem())
            return
        try:
            self._loaded_config.save(self._autosave_path())
            logger.debug("Auto-saved recovery file: %s", self._autosave_path())
            if self._last_save_path and not self._loaded_config.is_definition:
                SettingsManager.instance().set_autosave_origin(self._autosave_stem(), self._last_save_path)
        except Exception:
            logger.exception("Auto-save failed")

    def _delete_autosave(self, save_path: str | None = None, *, stem: str | None = None) -> None:
        """Remove the recovery file after a successful manual save."""
        if stem is None:
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
            if stem == "_unsaved" or stem.startswith(_DEFINITION_AUTOSAVE_PREFIX):
                # A definition's recovery file has no bot file to compare against; it
                # only exists while that definition had unsaved changes.
                original_path: Path | None = None
                is_candidate = True
            else:
                original_path = self._find_original_for_stem(stem)
                if original_path and original_path.exists():
                    is_candidate = autosave_path.stat().st_mtime > original_path.stat().st_mtime
                    if is_candidate and _same_bot_content(autosave_path, original_path):
                        # Newer, but restating the file: nothing to offer (#122).
                        self._delete_autosave(stem=stem)
                        continue
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
        from bartlebot.gui.dialogs.recovery_dialog import RecoveryDialog  # noqa: PLC0415

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
            if config.is_definition:
                self._push_current()
                self._show_definition(config, unsaved=True)
                continue
            config, report = self._compose(config)
            self._loaded_config = config
            self._last_save_path = str(original_path) if original_path else None
            # Unsaved edits to recover, so never read-only: a Save of an Official bot's
            # becomes Save As, into the user's own folder.
            self._official_open = False
            self._window.bot_canvas.set_read_only(False)
            display_name = self._file_label(config, original_path) if original_path else "Untitled (recovered)"
            self._window.set_bot_name(config.name, display_name)
            self._window.bot_canvas.setup(config, mode=BotMode.EDIT)
            self._window.bot_canvas.set_compose_report(report)
            self._refresh_template_context()
            self._window.left_sidebar.set_config(config)
            self._window.right_sidebar.set_config(config)
            self._window.set_save_enabled(True)
            self._window.set_close_enabled(True)
            self._window.set_start_enabled(self._start_enabled())
            # Mark dirty so the user is reminded to save and autosave continues.
            self._has_unsaved_changes = True
            self._clean_snapshot = None

        # Delete autosave files that were not chosen (discarded by the user).
        for c in candidates:
            if c["autosave_path"] not in chosen_paths:
                try:
                    c["autosave_path"].unlink()
                except OSError:
                    pass


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Whether two paths name one file, however each was spelled."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return Path(a) == Path(b)


def _world_folder(path: str | Path | None) -> str | None:
    """The library folder ``path`` is in, under either root, or None for one outside the library."""
    if path is None:
        return None
    parent = Path(path).resolve().parent
    roots = {d.resolve() for d in bot_roots.library_dirs()}
    return parent.name if parent.parent in roots else None


def _save_as_start(last_save_path: str | Path | None) -> Path:
    """Where Save As opens: the user's own folder, in the open bot's world folder, under its file name.

    The user's folder rather than the open file's, which may be an Official bot's in the
    install directory, where nothing may be written (#168). The folder is not created here:
    opening a dialog writes nothing, and the save makes it if the user keeps the path.
    """
    folder = bot_roots.user_bots_dir()
    world = _world_folder(last_save_path)
    if world is not None:
        folder = folder / world
    return folder / Path(last_save_path).name if last_save_path else folder


def _changed_linked_regions(before: BotConfig, after: BotConfig) -> list[tuple[str, str]]:
    """``(region id, label)`` for each linked region whose members or version differ between the two."""

    def snapshot(config: BotConfig, region_id: str) -> tuple | None:
        region = next((r for r in config.regions if r.id == region_id), None)
        if region is None or region.source is None:
            return None
        members = set(region.members)
        states = tuple(s.model_dump() for s in config.states if s.id in members)
        internal = tuple(t.model_dump() for t in config.transitions if t.source in members and t.dest in members)
        return region.source.version, states, internal

    changed: list[tuple[str, str]] = []
    for region in after.regions:
        if region.source is None:
            continue
        if snapshot(before, region.id) != snapshot(after, region.id):
            changed.append((region.id, region.label or region.id))
    return changed
