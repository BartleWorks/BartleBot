import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMenu, QMenuBar, QMessageBox, QSplitter

from bartlebot.clients.credentials import CredentialStore
from bartlebot.game.game_sizing import LEFT_SIDEBAR_MIN_WIDTH, RIGHT_SIDEBAR_MIN_WIDTH
from bartlebot.gui import screen_fit
from bartlebot.gui.canvas.bot_canvas import BotCanvas
from bartlebot.gui.game_embed.game_view_panel import GameViewPanel
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.right_sidebar import RightSidebar
from bartlebot.gui.title_bar.native_frame import NativeFrame
from bartlebot.gui.title_bar.title_bar import TitleBar
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager


def _shorten_recent_path(path: str, root: Path) -> str:
    """Display a recent-file path relative to the project root when possible."""
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def _splitter_sizes(total: int, fixed: list[int], flexible: int) -> list[int]:
    """*fixed* pane sizes with the rest of *total* given to the pane at index *flexible*.

    Pure so it can be tested without a QApplication, which is the whole point: the
    arithmetic is what went wrong, not the widgets. Clamped at zero rather than allowed
    to go negative -- a window too narrow for both sidebars hands Qt a nonsense size for
    the middle otherwise, where zero lets it fall back on the panes' own minimums.
    """
    sizes = list(fixed)
    sizes.insert(flexible, max(0, total - sum(fixed)))
    return sizes


class MainWindow(QMainWindow):
    # Emitted when a recent-file menu entry is clicked (passes the file path)
    open_recent_requested = pyqtSignal(str)
    # Emitted after the Settings dialog is accepted, so AppController can
    # reconfigure timers and other settings-dependent state.
    settings_applied = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")
        self._shutdown_handler = None

        self._build_menu()
        self._build_central()
        # After _build_central, not inside _build_menu: whether an entry can launch is
        # asked of the game view, which _build_central is what creates.
        self._rebuild_launch_menu()
        self._apply_minimum_size()
        # Cleared by the first showEvent, which is the earliest the splitters have a real
        # extent to size against -- see _apply_configured_splitter_sizes.
        self._startup_sizes_pending = True
        self._apply_startup_layout()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        # Not setMenuBar: the menu bar goes in the title bar, built at the end of this.
        menu_bar = QMenuBar()

        file_menu = menu_bar.addMenu("File")

        self.action_new = QAction("New Bot", self)
        self.action_new.setShortcut(QKeySequence("Ctrl+N"))
        file_menu.addAction(self.action_new)

        self.action_open = QAction("Open Bot File…", self)
        self.action_open.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addAction(self.action_open)

        self._recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self._recent_menu)
        self._update_recent_placeholder()

        self.action_browse_library = QAction("Browse Bot Library…", self)
        file_menu.addAction(self.action_browse_library)

        # Beside the bot library because it is the same kind of entry: a library of
        # reusable things, reachable without a bot open. Combat strategies depend on
        # which wizards the user owns, not on which bot is loaded, so this is the one
        # editor that should not be behind opening one.
        self.action_combat_strategies = QAction("Browse Combat Strategy Library…", self)
        file_menu.addAction(self.action_combat_strategies)

        # Every template, Official and the user's own, and the verbs that make and
        # change templates of your own (#164). A user starts a bot from one through
        # New Bot, which only ever offers released versions.
        self.action_templates = QAction("Templates…", self)
        file_menu.addAction(self.action_templates)

        self.action_close = QAction("Close Bot", self)
        self.action_close.setShortcut(QKeySequence("Ctrl+W"))
        self.action_close.setEnabled(False)
        file_menu.addAction(self.action_close)

        file_menu.addSeparator()

        self.action_save = QAction("Save Bot", self)
        self.action_save.setShortcut(QKeySequence("Ctrl+S"))
        self.action_save.setEnabled(False)
        file_menu.addAction(self.action_save)

        self.action_save_as = QAction("Save Bot As…", self)
        self.action_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.action_save_as.setEnabled(False)
        file_menu.addAction(self.action_save_as)

        # The open bot, or the open subgraph, as a share file (#170). Every other kind
        # exports from its own menu: a library card, the strategy dialog's cards, and the
        # Combat tab's Bot Combat Strategy.
        self.action_export = QAction("Export…", self)
        self.action_export.setEnabled(False)
        file_menu.addAction(self.action_export)

        # A share file of any kind (#171), whatever is open. Dropping one on the window
        # does the same: see gui/share_drop.py, which is why the window accepts drops.
        self.action_import = QAction("Import…", self)
        file_menu.addAction(self.action_import)
        self.setAcceptDrops(True)

        file_menu.addSeparator()

        settings_action = QAction("Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        # A lambda, not a bare connect: QAction.triggered carries its checked state, so
        # a direct connection lands that bool in tab_name and asks for a tab called False.
        settings_action.triggered.connect(lambda: self._open_settings())
        file_menu.addAction(settings_action)
        # Setup again, every page on the current values (epic #215). AppController opens it.
        self.action_setup = QAction("Set up BartleBot…", self)
        file_menu.addAction(self.action_setup)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # Edit menu
        edit_menu = menu_bar.addMenu("Edit")

        self.action_undo = QAction("Undo", self)
        self.action_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.action_undo.setEnabled(False)
        edit_menu.addAction(self.action_undo)

        self.action_redo = QAction("Redo", self)
        self.action_redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self.action_redo.setEnabled(False)
        edit_menu.addAction(self.action_redo)

        # View menu. The four panes are checkable, so the menu says what is showing; the
        # title bar's toggles are bound to these same actions (see _build_title_bar).
        view_menu = menu_bar.addMenu("View")
        self.action_toggle_left_sidebar = QAction("Left Sidebar", self)
        self.action_toggle_game_view = QAction("Game View", self)
        self.action_toggle_game_view.setShortcut(QKeySequence("Ctrl+G"))
        self.action_toggle_right_sidebar = QAction("Right Sidebar", self)
        self.action_toggle_bot_canvas = QAction("Bot Canvas", self)
        for action in self._pane_actions():
            action.setCheckable(True)
            action.setChecked(True)
            view_menu.addAction(action)

        view_menu.addSeparator()

        self.action_maximize_game_view = QAction("Maximize Game View", self)
        self.action_maximize_game_view.setShortcut(QKeySequence("Ctrl+Alt+G"))
        self.action_maximize_game_view.setCheckable(True)
        view_menu.addAction(self.action_maximize_game_view)

        # Run menu
        run_menu = menu_bar.addMenu("Run")

        self.action_run_pause = QAction("Run / Pause", self)
        self.action_run_pause.setShortcut(QKeySequence("F5"))
        self.action_run_pause.setEnabled(False)
        run_menu.addAction(self.action_run_pause)

        self.action_stop_bot = QAction("Stop Bot", self)
        self.action_stop_bot.setShortcut(QKeySequence("Shift+F5"))
        self.action_stop_bot.setEnabled(False)
        run_menu.addAction(self.action_stop_bot)

        run_menu.addSeparator()

        self.action_validate_bot = QAction("Validate Bot", self)
        self.action_validate_bot.setEnabled(False)
        run_menu.addAction(self.action_validate_bot)

        # Launch menu
        self._launch_menu = menu_bar.addMenu("Launch")
        # Qt suppresses action tooltips in menus unless asked. A disabled entry here
        # carries the reason it is disabled, and a grayed row with no way to read why is
        # the thing the disabled-not-hidden rule exists to avoid.
        self._launch_menu.setToolTipsVisible(True)
        # Rebuilt on every open: whether a launch fits depends on the client count and
        # the screen the window is on, both of which move while the menu is closed.
        self._launch_menu.aboutToShow.connect(self._rebuild_launch_menu)

        self._build_title_bar(menu_bar)

    def _pane_actions(self) -> tuple[QAction, QAction, QAction, QAction]:
        """The View menu's pane toggles, in the order the title bar draws them."""
        return (
            self.action_toggle_left_sidebar,
            self.action_toggle_game_view,
            self.action_toggle_right_sidebar,
            self.action_toggle_bot_canvas,
        )

    def _build_title_bar(self, menu_bar: QMenuBar) -> None:
        """The one row above the panes: logo, menus, title, pane toggles, window buttons.

        ``NativeFrame`` is what makes it a title bar rather than a strip under one, and it
        has to exist before the window is first shown, since it sets the window flags.
        """
        self._title_bar = TitleBar(menu_bar, self)
        self.setMenuWidget(self._title_bar)
        icons = ("panel_left", "panel_top", "panel_right", "panel_bottom")
        for action, icon in zip(self._pane_actions(), icons):
            self._title_bar.add_toggle(action, shown_icon=f"{icon}_shown", hidden_icon=f"{icon}_hidden")
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_requested.connect(self._toggle_maximized)
        self._title_bar.close_requested.connect(self.close)
        self._title_bar.set_title(self.windowTitle())
        self.windowTitleChanged.connect(self._title_bar.set_title)
        self._frame = NativeFrame(self, self._title_bar)

    @property
    def title_bar(self) -> TitleBar:
        """The row above the panes, which holds the menu bar."""
        return self._title_bar

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _build_central(self) -> None:
        # BotCanvas must be created first — it owns param_panel
        self.bot_canvas = BotCanvas()
        self.left_sidebar = LeftSidebar(self.bot_canvas.param_panel)
        self.right_sidebar = RightSidebar()
        # The document the Setup tab was last decided for, and whether it has been shown
        # for that document: see _sync_setup_tab.
        self._setup_document: str | None = None
        self._setup_tab_kept = False
        # Later on a template review: put away until the bot is next opened.
        self._review_later = False
        # Whether a subgraph definition was open over that document at the last sync.
        self._setup_over_definition = False
        self._game_view_panel = GameViewPanel()
        self.game_view = self._game_view_panel.game_view
        # The authoring utilities in Bar A (#81); AppController drives them.
        self.authoring_bar = self._game_view_panel.authoring_bar

        # Wire View menu. ``triggered`` carries the action's new checked state, which is
        # the visibility asked for.
        self.action_toggle_game_view.triggered.connect(self.show_game_view)
        self.action_maximize_game_view.triggered.connect(self._toggle_maximize_game_view)
        self.action_toggle_left_sidebar.triggered.connect(self.left_sidebar.setVisible)
        self.action_toggle_right_sidebar.triggered.connect(self.right_sidebar.setVisible)
        self.action_toggle_bot_canvas.triggered.connect(self.show_bot_canvas)

        # Wire wiki import from handle_combat node panel → possible drops list
        self.bot_canvas.param_panel.wiki_import_completed.connect(self.right_sidebar.possible_drops.add_drops)
        # Wire drop list edits → the quicksell keep-list picker in Option Values, so it lists
        # the current drops. The picker is a bot option now, so it lives over there.
        self.right_sidebar.possible_drops.drops_changed.connect(self.left_sidebar.refresh_drops)
        # A setup step can ask for the drop list, so its tick follows the list.
        self.right_sidebar.possible_drops.drops_changed.connect(self.bot_canvas.refresh_setup)
        # The Setup tab: a template's steps, beside the node each one points at. The
        # canvas says when they may have moved; the tab's edits to a draft go back
        # through the canvas, which owns the undo history.
        setup = self.right_sidebar.setup_panel
        self.bot_canvas.setup_changed.connect(self._sync_setup_tab)
        self.bot_canvas.setup_tab_requested.connect(self._reopen_setup_tab)
        self.bot_canvas.drops_imported.connect(self.right_sidebar.possible_drops.add_drops)
        setup.go_to_requested.connect(self.bot_canvas.show_setup_target)
        setup.drops_requested.connect(self.right_sidebar.switch_to_drops)
        setup.import_requested.connect(self.bot_canvas.import_drops_for)
        setup.edit_starting.connect(self.bot_canvas.begin_setup_edit)
        setup.structural_change.connect(self.bot_canvas.begin_setup_change)
        setup.changed.connect(self.bot_canvas.setup_edited)
        # A template review asks; the canvas, which owns the bot and its undo history, does.
        setup.apply_requested.connect(self.bot_canvas.apply_template_change)
        setup.finish_requested.connect(self.bot_canvas.finish_template_review)
        setup.later_requested.connect(self._put_review_away)
        # Wire Browse Bot Library menu action
        self.action_browse_library.triggered.connect(lambda: self.bot_canvas.open_library_dialog())
        # The combat strategy dialog is owned by the Combat tab: it is the surface that
        # has to reload when the dialog closes, and routing through it keeps one caller.
        # Wrapped: ``triggered`` passes a checked flag, which would arrive as ``kind``.
        self.action_combat_strategies.triggered.connect(lambda: self.left_sidebar.combat_tab.open_combat_dialog())
        # Wire sidebar tab switching from canvas
        self.bot_canvas.tab_switch.connect(self._on_canvas_tab_switch)
        # Wire bot status to game view panel title bar
        self.bot_canvas.bot_status_changed.connect(self._on_bot_status_changed)
        # Wire canvas placeholder quick-start buttons to file menu actions
        self.bot_canvas.new_bot_requested.connect(self.action_new.trigger)
        self.bot_canvas.open_bot_requested.connect(self.action_open.trigger)
        self.bot_canvas.open_recent_requested.connect(self.open_recent_requested)
        # Wire undo/redo
        self.action_undo.triggered.connect(self.bot_canvas.undo)
        self.action_redo.triggered.connect(self.bot_canvas.redo)
        self.bot_canvas.undo_state_changed.connect(self._on_undo_state_changed)

        # Set by AppController once ClientManager exists; see set_auto_select_driver
        # and set_launch_registrar.
        self._auto_select_driver = None
        self._begin_launch = None
        self._end_launch = None
        self._launch_progress = None
        self._dismiss_launch_fn = None

        # Wire placeholder quick-launch signals
        self._game_view_panel.launch_account_requested.connect(
            lambda u, d: asyncio.ensure_future(self.launch_account_named(u, d))
        )
        self._game_view_panel.launch_profile_requested.connect(
            lambda n: asyncio.ensure_future(self.launch_profile_named(n))
        )
        self._game_view_panel.manage_accounts_requested.connect(lambda: self._open_settings(tab_name="game"))
        self._game_view_panel.dismiss_launch_requested.connect(self._dismiss_launch)
        # Option Values's rows name the app setting they fall back to; this is the way there.
        self.left_sidebar.app_settings_requested.connect(lambda: self._open_settings(tab_name="bot"))
        # ...and back: those rows resolve through the values that dialog just wrote.
        self.settings_applied.connect(self.left_sidebar.refresh_option_values)
        # The Combat tab's banner names the Solo or Team default a bot falls to; Change
        # defaults… is the way to Settings › Combat, and the banner re-reads on the way back.
        self.left_sidebar.combat_tab.combat_defaults_requested.connect(lambda: self._open_settings(tab_name="combat"))
        self.settings_applied.connect(self.left_sidebar.combat_tab.refresh_source)
        # Straight to the one editor the gear asks for. It used to go through
        # _open_settings, which built the whole Settings dialog to reach it and left it
        # standing behind — see _edit_launch_profile.
        self._game_view_panel.edit_profile_requested.connect(self._edit_launch_profile)
        # The Add card at the foot of each column, which goes to the same editor the Game
        # tab's Add button does and nowhere near the Settings dialog around it.
        self._game_view_panel.add_account_requested.connect(self._add_account)
        self._game_view_panel.add_profile_requested.connect(self._add_launch_profile)
        self._game_view_panel.edit_account_requested.connect(self._edit_account)
        self._game_view_panel.delete_account_requested.connect(self._delete_account)
        self._game_view_panel.duplicate_profile_requested.connect(self._duplicate_launch_profile)
        self._game_view_panel.delete_profile_requested.connect(self._delete_launch_profile)
        # A column dragged into a new order. Through the same _after_card_action as
        # the rest, because order is not only what the column draws: the Launch menu lists
        # both of these, in this order.
        self._game_view_panel.reorder_accounts_requested.connect(self._reorder_accounts)
        self._game_view_panel.reorder_profiles_requested.connect(self._reorder_launch_profiles)

        # Top row: left sidebar | game view | right sidebar
        self._top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._top_splitter.setChildrenCollapsible(False)
        self._top_splitter.addWidget(self.left_sidebar)
        self._top_splitter.addWidget(self._game_view_panel)
        self._top_splitter.addWidget(self.right_sidebar)
        self._top_splitter.setStretchFactor(1, 1)
        sm = SettingsManager.instance()
        bot_h = sm.get_general("bottom_section_height", default=220)
        # A seed only. Nothing here sticks: the splitter has no extent yet, so this is a
        # ratio rather than a size, and the real one is applied on the first showEvent.
        self._top_splitter.setSizes([300, 800, 300])

        # Outer vertical splitter: top row (game + sidebars) | bot canvas
        self._outer_splitter = QSplitter(Qt.Orientation.Vertical)
        self._outer_splitter.setChildrenCollapsible(False)
        self._outer_splitter.addWidget(self._top_splitter)
        self._outer_splitter.addWidget(self.bot_canvas)
        self._outer_splitter.setStretchFactor(0, 1)
        self._outer_splitter.setSizes([600, bot_h])

        self.setCentralWidget(self._outer_splitter)

        # Show GameViewPanel immediately (placeholder shown when no clients)
        self._game_view_visible: bool = True
        self._game_view_maximized: bool = False
        self._pre_maximize_sizes: dict = {}
        self._game_view_panel.setVisible(True)

        # Wire Run menu
        self.action_run_pause.triggered.connect(lambda: self.bot_canvas._run_pause_btn.click())
        self.action_stop_bot.triggered.connect(lambda: self.bot_canvas.stop_button.click())

    def _apply_minimum_size(self) -> None:
        """Declare the floor the panes below are built to honor.

        Measured at this window's own scale factor: a resolution is physical pixels while
        everything else in the sum is logical ones, so on a 150% display the undivided
        number is half again too big a floor to hold the window to.
        """
        min_w, min_h = SettingsManager.instance().minimum_window_size(
            dpr=self.devicePixelRatio() or 1.0,
            right_sidebar_floor=screen_fit.right_sidebar_floor(),
            left_sidebar_floor=screen_fit.left_sidebar_floor(),
        )
        self.setMinimumSize(min_w, min_h)

    def _apply_configured_splitter_sizes(self) -> None:
        """Give the sidebars and the bot canvas their configured sizes, and the game panel the rest.

        Applied once the window has been shown, not only from ``_build_central``. A
        ``setSizes`` call against a splitter that has not been laid out yet is a *ratio*
        rather than a size -- Qt scales it to whatever extent the splitter has at the
        time, and the window then grows to its startup size with the surplus shared out
        across all three panes. A 1400x900 window opened that way put 480px in each
        sidebar and 600px in the bot canvas, none of which the user asked for.

        That is not only a cosmetic mis-layout. The game area left over is what a launch
        is judged against (see gui/screen_fit.budget_for), so the panes taking space they
        were never configured for is what refused profiles the screen had room for --
        and dragging a sidebar could not always give it back, because the height had gone
        to the canvas at the same time.

        Sized against the splitters' *current* extent rather than the screen, so it is
        correct whether the window was resized or maximized, and clamped at zero so a
        window narrower than both sidebars leaves Qt its own minimums to enforce.
        """
        sm = SettingsManager.instance()
        left_w = max(
            int(sm.get_general("left_sidebar_width", default=LEFT_SIDEBAR_MIN_WIDTH)),
            screen_fit.left_sidebar_floor(),
        )
        right_w = max(
            int(sm.get_general("right_sidebar_width", default=RIGHT_SIDEBAR_MIN_WIDTH)),
            screen_fit.right_sidebar_floor(),
        )
        bot_h = int(sm.get_general("bottom_section_height", default=220))

        top_total = sum(self._top_splitter.sizes())
        if top_total > 0:
            self._top_splitter.setSizes(_splitter_sizes(top_total, [left_w, right_w], flexible=1))
        outer_total = sum(self._outer_splitter.sizes())
        # Skipped while the game view is hidden: the bot canvas owns the whole window
        # then, and handing the top pane a size would put an empty strip above it.
        if outer_total > 0 and self._game_view_visible:
            self._outer_splitter.setSizes(_splitter_sizes(outer_total, [bot_h], flexible=0))

    def showEvent(self, event) -> None:  # noqa: N802
        """Apply the configured layout on the way in, once.

        Once, because every later show -- restoring from minimised, coming back from
        another desktop -- would otherwise throw away every splitter drag the user has
        made since, and nothing persists those.
        """
        super().showEvent(event)
        self._frame.on_shown()
        if self._startup_sizes_pending:
            self._startup_sizes_pending = False
            self._apply_configured_splitter_sizes()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._title_bar.set_maximized(self.isMaximized())

    def nativeEvent(self, event_type, message):  # noqa: N802
        # Hit testing and the caption's system menu: see gui/title_bar/native_frame.py.
        # Never super().nativeEvent -- calling the base from a Python override crashes PyQt6.
        return self._frame.native_event(message)

    def _apply_startup_layout(self) -> None:
        sm = SettingsManager.instance()
        min_w, min_h = sm.minimum_window_size(
            dpr=self.devicePixelRatio() or 1.0,
            right_sidebar_floor=screen_fit.right_sidebar_floor(),
            left_sidebar_floor=screen_fit.left_sidebar_floor(),
        )
        if sm.get_general("maximize_on_open", default=True):
            self.showMaximized()
        else:
            w = max(sm.get_general("window_width", default=1400), min_w)
            h = max(sm.get_general("window_height", default=800), min_h)
            self.resize(w, h)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def game_view_panel(self) -> GameViewPanel:
        """The pane the game view sits in, its own header strip included.

        Public because the height of that strip is a term in the window-floor arithmetic
        (``GAME_PANEL_CHROME_PX``), and the guard that keeps the declared floor and the
        enforced one in step has to be able to measure it.
        """
        return self._game_view_panel

    def set_shutdown_handler(self, handler) -> None:
        """Install the quit gate: *handler* returns True to let the window close.

        The window owns the close event but not the clients, so the decision belongs to
        AppController: it may refuse this close, unhook every client asynchronously, and
        close the window again once that lands.
        """
        self._shutdown_handler = handler

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._shutdown_handler is not None and not self._shutdown_handler():
            event.ignore()
            return
        super().closeEvent(event)

    def set_fsm_state(self, state_id: str) -> None:
        self.bot_canvas.set_state(state_id)

    def set_bot_name(self, name: str, filename: str | None) -> None:
        """Retitle the window and the header above the graph.

        *name* is not pushed anywhere: the header reads it from the BotConfig it was
        given, and in Edit mode it is the field the user is typing into.
        """
        self.setWindowTitle(f"BartleBot — {filename}" if filename else "BartleBot")
        self.bot_canvas.set_bot_file(filename)

    def set_client_summary(self, connected: int) -> None:
        """Tell Bar A how many clients are hooked — it is the bar that owns them."""
        self._game_view_panel.set_client_summary(connected)

    def set_start_enabled(self, enabled: bool) -> None:
        self.bot_canvas.set_run_enabled(enabled)

    def update_recent_menu(self, paths: list) -> None:
        """Rebuild the Open Recent submenu from a list of file paths."""
        self._recent_menu.clear()
        if not paths:
            self._update_recent_placeholder()
            return
        root = project_root()
        for path in paths:
            label = _shorten_recent_path(path, root)
            action = QAction(label, self)
            action.triggered.connect(lambda _checked, p=path: self.open_recent_requested.emit(p))
            self._recent_menu.addAction(action)

    def set_save_enabled(self, enabled: bool, *, save_as: bool | None = None) -> None:
        """Enable Save, and Save As with it unless *save_as* says otherwise: an Official bot
        has nothing to save, but Save As still makes a copy of it."""
        self.action_save.setEnabled(enabled)
        self.action_save_as.setEnabled(enabled if save_as is None else save_as)

    def set_close_enabled(self, enabled: bool) -> None:
        """Enable Close, and Export with it: both act on whatever document is open."""
        self.action_close.setEnabled(enabled)
        self.action_export.setEnabled(enabled)

    def show_game_view(self, visible: bool) -> None:
        """Show or hide the top game-view area. When hidden, bot canvas fills the window."""
        self.action_toggle_game_view.setChecked(visible)
        if visible == self._game_view_visible:
            return
        self._game_view_visible = visible
        self._game_view_panel.setVisible(visible)
        bot_h = self._outer_splitter.sizes()[1]
        if visible:
            self._outer_splitter.setSizes([600, bot_h])
        else:
            self._outer_splitter.setSizes([0, bot_h])

    def show_bot_canvas(self, visible: bool) -> None:
        """Show or hide the bot canvas. When hidden, the game view and sidebars fill the window."""
        self.bot_canvas.setVisible(visible)
        self.action_toggle_bot_canvas.setChecked(visible)

    def _sync_pane_actions(self) -> None:
        """Check each pane toggle by whether its pane is showing.

        ``isHidden`` rather than ``isVisible``: a pane is showing when nothing hid it,
        whether or not the window itself is on screen yet.
        """
        self.action_toggle_left_sidebar.setChecked(not self.left_sidebar.isHidden())
        self.action_toggle_game_view.setChecked(self._game_view_visible)
        self.action_toggle_right_sidebar.setChecked(not self.right_sidebar.isHidden())
        self.action_toggle_bot_canvas.setChecked(not self.bot_canvas.isHidden())

    def refresh_minimum_size(self) -> None:
        """Re-apply minimum size after a settings change."""
        self._apply_minimum_size()

    def _toggle_maximize_game_view(self) -> None:
        if not self._game_view_maximized:
            # Save current state before maximizing
            self._pre_maximize_sizes = {
                "top": self._top_splitter.sizes(),
                "outer": self._outer_splitter.sizes(),
                "game_visible": self._game_view_visible,
                "left_visible": not self.left_sidebar.isHidden(),
                "right_visible": not self.right_sidebar.isHidden(),
                "canvas_visible": not self.bot_canvas.isHidden(),
            }
            self._game_view_maximized = True
            self.action_maximize_game_view.setChecked(True)
            # Show game view and hide everything else
            if not self._game_view_visible:
                self._game_view_visible = True
                self._game_view_panel.setVisible(True)
            self.left_sidebar.setVisible(False)
            self.right_sidebar.setVisible(False)
            self.bot_canvas.setVisible(False)
        else:
            self._game_view_maximized = False
            self.action_maximize_game_view.setChecked(False)
            # Restore pre-maximize state
            self.left_sidebar.setVisible(self._pre_maximize_sizes.get("left_visible", True))
            self.right_sidebar.setVisible(self._pre_maximize_sizes.get("right_visible", True))
            self.bot_canvas.setVisible(self._pre_maximize_sizes.get("canvas_visible", True))
            if not self._pre_maximize_sizes.get("game_visible", True):
                self._game_view_visible = False
                self._game_view_panel.setVisible(False)
            self._top_splitter.setSizes(self._pre_maximize_sizes.get("top", [300, 800, 300]))
            self._outer_splitter.setSizes(self._pre_maximize_sizes.get("outer", [600, 220]))
        self._sync_pane_actions()

    def _on_bot_status_changed(self, color: str, status: str, state_label: str, elapsed: str) -> None:
        # The dot, the status word and the clock live in Bar B now; the only part of
        # this Bar A still wants is which node the clients are being driven through.
        self._game_view_panel.set_active_node(state_label)
        idle = status == "Idle"
        running_or_paused = status in ("Running", "Paused")
        editing = status == "Editing"
        self.action_run_pause.setEnabled(not editing)
        self.action_stop_bot.setEnabled(running_or_paused)
        self.action_validate_bot.setEnabled(idle)

    def _on_undo_state_changed(self, can_undo: bool, can_redo: bool, undo_desc: str, redo_desc: str) -> None:
        self.action_undo.setEnabled(can_undo)
        self.action_undo.setText(f"Undo: {undo_desc}" if undo_desc else "Undo")
        self.action_redo.setEnabled(can_redo)
        self.action_redo.setText(f"Redo: {redo_desc}" if redo_desc else "Redo")

    @property
    def live_game_area(self) -> tuple[int, int]:
        """The logical pixels the tile grid has right now.

        Public because it is what every launch surface has to be judged against, including
        the settings dialog, which is handed it rather than reaching back through its own
        parent chain to find this window. See gui/screen_fit.budget_for.
        """
        return self.game_view.game_area_size

    @staticmethod
    def _disable_if_unfittable(action: QAction, reason: str | None) -> None:
        """Gray *action* out and explain, or leave it alone when *reason* is None."""
        if reason is None:
            return
        action.setEnabled(False)
        action.setToolTip(reason)
        action.setStatusTip(reason)

    def _single_account_fit_problem(self) -> str | None:
        """Why launching one more client would not fit, or None if it would.

        Judged against the *resulting* count, so adding a third client to a fitting pair
        is refused when the 2x2 grid that forces would not fit. Resolution is fixed at
        launch and a running client cannot be resized without relaunching it, so the
        client arriving is the one that has to be refused.

        Sized at the larger of what that client will launch at and what is already on
        screen: mixed-resolution fleets are supported, and a fleet whose windows are
        bigger than the current setting still has to be fitted at the size it actually is.
        """
        launching = SettingsManager.instance().game_resolution
        running = self.game_view.largest_game_size
        resolution = launching if running is None else (max(launching[0], running[0]), max(launching[1], running[1]))
        return screen_fit.fit_problem(resolution, self.game_view.client_count + 1, self, self.live_game_area)

    def _rebuild_launch_menu(self) -> None:
        """Rebuild Launch menu from current accounts and profiles."""
        self._launch_menu.clear()

        # --- Profiles section ---
        profiles_lbl = QAction("─ Profiles ─", self)
        profiles_lbl.setEnabled(False)
        self._launch_menu.addAction(profiles_lbl)

        profiles = SettingsManager.instance().launch_profiles
        if profiles:
            for profile in profiles:
                name = profile.get("name", "Unnamed")
                action = QAction(name, self)
                action.triggered.connect(lambda _checked, n=name: asyncio.ensure_future(self.launch_profile_named(n)))
                # Disabled rather than dropped, carrying the reason as its tooltip — the
                # same rule the Quick Launch cards follow. This menu is rebuilt on every
                # show, so the answer tracks the client count and the screen.
                self._disable_if_unfittable(action, screen_fit.profile_fit_problem(profile, self, self.live_game_area))
                self._launch_menu.addAction(action)
        else:
            empty = QAction("(No profiles)", self)
            empty.setEnabled(False)
            self._launch_menu.addAction(empty)

        self._launch_menu.addSeparator()

        # --- Accounts section ---
        accounts_lbl = QAction("─ Accounts ─", self)
        accounts_lbl.setEnabled(False)
        self._launch_menu.addAction(accounts_lbl)

        accounts = SettingsManager.instance().accounts
        if accounts:
            for acct in accounts:
                username = acct.get("username", "")
                alias = acct.get("alias") or None
                display = CredentialStore.display_name(username, alias)
                action = QAction(display, self)
                action.triggered.connect(
                    lambda _checked, u=username, d=display: asyncio.ensure_future(self.launch_account_named(u, d))
                )
                self._disable_if_unfittable(action, self._single_account_fit_problem())
                self._launch_menu.addAction(action)
        else:
            empty2 = QAction("(No accounts)", self)
            empty2.setEnabled(False)
            self._launch_menu.addAction(empty2)

        self._launch_menu.addSeparator()

        manage_action = QAction("Manage Accounts…", self)
        manage_action.triggered.connect(lambda: self._open_settings(tab_name="game"))
        self._launch_menu.addAction(manage_action)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sync_setup_tab(self) -> None:
        """Redraw the Setup tab from the canvas, and decide whether it is there at all.

        There for a template draft, for a bot with steps still to do, and for a bot
        whose template has released a newer version, until that review is finished or
        put away with Later. A bot whose steps are all done gets no tab when it is
        opened, but one that finishes them keeps it until it is closed -- a tab that
        vanished on the last tick would take "All 5 done" with it -- and Bar B's
        template chip brings it back.

        A subgraph definition opened over the bot is a visit, not another document: the
        bot is still open underneath. So it has no tab of its own, and coming back out
        neither forgets a Later nor pulls the sidebar over to Setup again.
        """
        canvas = self.bot_canvas
        config = canvas.config
        visiting = config is not None and config.is_definition
        returning = self._setup_over_definition and not visiting
        self._setup_over_definition = visiting
        document = self._setup_document if visiting else (config.id if config is not None else None)
        if document != self._setup_document:
            self._setup_document = document
            self._setup_tab_kept = False
            self._review_later = False
            fresh = True
        else:
            fresh = False
        draft = config is not None and config.is_template
        review = None if self._review_later else canvas.template_review
        wanted = (
            draft
            or canvas.setup_steps_left > 0
            or review is not None
            or (self._setup_tab_kept and canvas.has_setup_steps)
        )
        self.right_sidebar.setup_panel.show_document(
            config if (draft or canvas.has_setup_steps or review is not None) else None,
            canvas.setup_progress(),
            canvas.subgraph_library,
            review,
        )
        was_shown = self.right_sidebar.setup_tab_shown
        self.right_sidebar.set_setup_tab_shown(wanted)
        if wanted:
            self._setup_tab_kept = True
            if not draft and (fresh or (not was_shown and not returning)):
                # Steps to do are the first thing to know about a bot just opened.
                self.right_sidebar.switch_to_setup()

    def _reopen_setup_tab(self) -> None:
        """Bar B's template chip: show the bot's setup steps again, done or not, and a review put away."""
        if not self.bot_canvas.has_setup_steps and self.bot_canvas.template_review is None:
            return
        self._setup_tab_kept = True
        self._review_later = False
        self._sync_setup_tab()
        self.right_sidebar.switch_to_setup()

    def _put_review_away(self) -> None:
        """Later, on a template review: nothing changes in the bot, and its base stays where it is."""
        self._review_later = True
        # Dismissed by hand, so steps that are all done no longer hold the tab open.
        self._setup_tab_kept = False
        self._sync_setup_tab()

    def _on_canvas_tab_switch(self, tab: str) -> None:
        if tab == "log":
            self.right_sidebar.switch_to_log()
        elif tab == "properties":
            self.left_sidebar.switch_to_properties()
        elif tab == "combat":
            self.left_sidebar.switch_to_combat()
        elif tab == "drops":
            self.right_sidebar.switch_to_drops()

    # ------------------------------------------------------------------
    # Card actions
    # ------------------------------------------------------------------
    # The verbs on the launch screen's account and profile cards: the pencil, and
    # the entries on each card's right-click menu. Every one of them is a settings write
    # the app has to catch up with, and every one of them is the same catching up, which
    # is what _after_card_action is.
    #
    # None of them do the work. The module functions in settings/accounts_tab.py are what
    # both this screen and the Game tab call, so the two surfaces cannot drift on what a
    # delete cascades to or on what a duplicate is named.
    #
    # All are called from synchronous Qt slots: each runs exec() on a dialog or a message
    # box, which Python 3.13 forbids from inside a running asyncio task — see CLAUDE.md.

    def _after_card_action(self, written: bool) -> None:
        """Bring the app back in line with settings after a card action.

        *written* gates only the two that read settings for something other than the
        cards: the Launch menu's entries, and the window minimum, which is a function of
        what the largest profile would launch.

        The cards themselves are rebuilt either way. The action was modal, so the screen
        behind it has been sitting on cards nothing has re-judged since it opened — and
        hanging that half off a dialog's result is exactly the second-order bug in the profile-editor dialog work,
        where cancelling the outer Settings dialog dropped the refresh for a profile the
        inner editor had already written.
        """
        if written:
            self._rebuild_launch_menu()
            self.refresh_minimum_size()
        self._game_view_panel.refresh_launch_lists()

    def catch_up_with_accounts(self) -> None:
        """Bring the app back in line with accounts and profiles another surface changed.

        For a surface that writes them through the card actions without going through
        this window: setup's Accounts page. The Launch menu, the window minimum and the
        launch screen's cards all read the store, and none of them is told on its own.
        """
        self._after_card_action(True)

    def _edit_launch_profile(self, name: str) -> None:
        """Edit one launch profile, from the pencil on its card or its context menu.

        The card asks to edit a profile, so that is the only dialog it gets. This used to
        open Settings on the Game tab with the editor on top, which left the whole
        dialog standing behind the editor once it was dismissed.
        """
        from bartlebot.gui.settings.accounts_tab import edit_launch_profile

        self._after_card_action(edit_launch_profile(name, self, self.live_game_area))

    def _duplicate_launch_profile(self, name: str) -> None:
        """Copy one launch profile, from its context menu. The only action with no dialog."""
        from bartlebot.gui.settings.accounts_tab import duplicate_launch_profile

        self._after_card_action(duplicate_launch_profile(name))

    def _delete_launch_profile(self, name: str) -> None:
        """Delete one launch profile, from its context menu. Confirms first."""
        from bartlebot.gui.settings.accounts_tab import delete_launch_profile

        self._after_card_action(delete_launch_profile(name, self))

    def _add_launch_profile(self) -> None:
        """Add a launch profile, from the Add card at the foot of the Profiles column.

        The live game area goes through so the editor's resolution picker judges a launch
        the same way the screen behind it does.
        """
        from bartlebot.gui.settings.accounts_tab import add_launch_profile

        self._after_card_action(add_launch_profile(self, self.live_game_area))

    def _add_account(self) -> None:
        """Add an account, from the Add card at the foot of the Accounts column."""
        from bartlebot.gui.settings.accounts_tab import add_account

        self._after_card_action(add_account(self))

    def _edit_account(self, username: str) -> None:
        """Edit one account, from the pencil on its card or its context menu."""
        from bartlebot.gui.settings.accounts_tab import edit_account

        self._after_card_action(edit_account(username, self))

    def _delete_account(self, username: str) -> None:
        """Delete one account, from its context menu. Confirms first.

        The heaviest of these: it takes the password out of the Credential Manager, the
        character roster off disk, and the account out of every profile that launched it
        — so the profile column is redrawn by the same refresh, with a pill fewer on each
        card that carried this one.
        """
        from bartlebot.gui.settings.accounts_tab import delete_account

        self._after_card_action(delete_account(username, self))

    def _reorder_accounts(self, order: list) -> None:
        """Persist a drag in the Accounts column. The only card action with no dialog behind it."""
        from bartlebot.gui.settings.accounts_tab import reorder_accounts

        self._after_card_action(reorder_accounts(list(order)))

    def _reorder_launch_profiles(self, order: list) -> None:
        """Persist a drag in the Launch Profiles column."""
        from bartlebot.gui.settings.accounts_tab import reorder_launch_profiles

        self._after_card_action(reorder_launch_profiles(list(order)))

    def _open_settings(self, tab_name: str = "general") -> None:
        """Open Settings on the named tab.

        Called from synchronous Qt slots only. exec() re-enters Qt's event loop, which
        Python 3.13 forbids from inside a running asyncio task — see CLAUDE.md.
        """
        from bartlebot.gui.settings.settings_dialog import SettingsDialog

        # Handed the live game area so its resolution pickers judge a launch the same
        # way the Launch menu behind them does — a splitter drag moves both.
        dlg = SettingsDialog(self, live_game_area=self.live_game_area)
        dlg.open_tab(tab_name)
        if dlg.exec():
            self._rebuild_launch_menu()
            self.refresh_minimum_size()
            self.bot_canvas._apply_canvas_settings()
            self.settings_applied.emit()
            # Streamer Mode changes how a tile renders a name it already has, and a tile
            # only repaints when its name or label changes — neither did.
            self._game_view_panel.repaint_tile_labels()
        self._game_view_panel.refresh_launch_lists()

    def _open_launcher(self) -> None:
        from bartlebot.gui.launcher_popup import LauncherPopup

        popup = LauncherPopup(self)
        popup.show()

    def set_auto_select_driver(self, driver) -> None:
        """Supply the coroutine that advances a launched window to in-game.

        Injected by AppController because the drive's completion signal ("is a hooked
        client with this title live yet?") belongs to ClientManager, which the window does
        not own. None leaves launched windows sitting at character select.
        """
        self._auto_select_driver = driver

    @property
    def auto_select_driver(self):
        return self._auto_select_driver

    def set_launch_registrar(self, begin, end, progress=None, dismiss=None) -> None:
        """Supply the pair that opens and closes a launch for a set of accounts.

        Injected alongside the driver, and for the same reason — both the slot ordering and
        the set of launches in flight belong to ClientManager. Without *begin*, players come
        up in whatever order their characters finish loading in rather than the order the
        profile lists them, and nothing rejects a second click on an account that is already
        launching.

        Two callables rather than one because the launch has an end as well as a start, and
        the end is the half that was missing: a launch that fails holds its slot forever
        unless something gives it back. See ``ClientManager.end_launch``.

        *progress* is the third: what the launcher calls as each account moves between the
        stages of its launch, so a pending tile can say which one it is in rather than only
        that it is in one. *dismiss* clears a launch that failed, which nothing else does:
        a failed attempt is kept open on purpose so its tile can keep saying so.
        """
        self._begin_launch = begin
        self._end_launch = end
        self._launch_progress = progress
        self._dismiss_launch_fn = dismiss

    def _open_launch(self, usernames: list[str]) -> list[str]:
        """Claim slots for *usernames*, returning the ones not already launching.

        Every account launched from this window passes through here, whichever surface
        asked — a Quick Launch card, the menu, or the floating popup. That is the point:
        the guard can only reject a duplicate where all of them meet.
        """
        if self._begin_launch is None:
            return list(usernames)
        return self._begin_launch(usernames)

    def _close_launch(self, usernames: list[str]) -> None:
        """Release the launches opened by ``_open_launch``. Always from a ``finally``."""
        if self._end_launch is not None:
            self._end_launch(usernames)

    def _dismiss_launch(self, username: str) -> None:
        """Clear a failed launch the user clicked away, and give its slot back.

        A failed launch is the one attempt that ``_close_launch`` leaves open — it has a
        tile and something to say on it — so this is the only thing that ever ends one.
        """
        if self._dismiss_launch_fn is not None:
            self._dismiss_launch_fn(username)

    async def launch_profile_named(self, profile_name: str) -> None:
        """Launch a profile's accounts, skipping any that are already launching.

        Public because it is the only way in: the Quick Launch cards, the File menu and
        the floating launcher popup all land here, so the in-flight guard and the recent
        list see every launch this window makes.
        """
        try:
            from bartlebot.launcher.launch_profile import launch_profile

            profile = next(
                (p for p in SettingsManager.instance().launch_profiles if p.get("name") == profile_name), None
            )
            # Backstop for a path that did not go through a disabled control — a
            # keybinding, or a profile edited while its menu entry was already built.
            # Every surface offering this launch has already refused it, so arriving here
            # means one of them was stale rather than that the user was warned.
            reason = None if profile is None else screen_fit.profile_fit_problem(profile, self, self.live_game_area)
            if reason is not None:
                QMessageBox.critical(self, "Launch failed", reason)
                return

            # Claimed here rather than inside launch_profile, so a second click on a
            # profile that is already coming up is rejected before anything spawns. The
            # accounts already in flight drop out and the rest still launch — a profile of
            # four with one account up should bring up the other three.
            accepted = self._open_launch(list(profile.get("accounts", [])))
            if not accepted:
                # Said out loud rather than logged, because this is the click that looks
                # broken: nothing spawns, nothing changes on screen, and the tiles that
                # explain why were already there before the click. Only when the *whole*
                # profile drops out -- a partial one still visibly brings up the rest.
                logger.info("Launch profile '%s' has nothing left to launch", profile_name)
                QMessageBox.information(
                    self,
                    "Nothing to launch",
                    f"Every account in '{profile_name}' is already running or launching.",
                )
                return
            try:
                self._game_view_panel.set_launch_profile(profile_name)
                await launch_profile(
                    profile_name,
                    drive=self.auto_select_driver,
                    usernames=accepted,
                    progress=self._launch_progress,
                )
            finally:
                # In a finally so the slots come back on the paths that do not return
                # normally, which is every failure the launcher has: a missing password
                # raises from inside launch_profile, and a window that never reaches the
                # login screen never gets one to hold a slot for.
                self._close_launch(accepted)
            self._record_recent_launch("profile", profile_name)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            logger.exception("Profile launch failed")

    async def launch_account_named(self, username: str, display_name: str) -> None:
        """Launch one account, unless a launch for it is already running."""
        try:
            from bartlebot.launcher.launch_profile import launch_account

            if (reason := self._single_account_fit_problem()) is not None:
                QMessageBox.critical(self, "Launch failed", reason)
                return
            password = CredentialStore.retrieve(username)
            if password is None:
                QMessageBox.critical(
                    self,
                    "Launch failed",
                    f"No stored password for '{display_name}'.\nPlease add the account in Settings → Accounts.",
                )
                return
            # Claim the slot at the moment of launching, so launching two accounts
            # back-to-back orders them by when they were asked for, not by which one
            # happens to load in first.
            if not self._open_launch([username]):
                logger.info("Account '%s' is already running or launching", display_name)
                QMessageBox.information(
                    self,
                    "Nothing to launch",
                    f"'{display_name}' is already running or launching.",
                )
                return
            try:
                await launch_account(
                    username,
                    password,
                    display_name,
                    drive=self.auto_select_driver,
                    progress=self._launch_progress,
                )
            finally:
                self._close_launch([username])
            self._record_recent_launch("account", username)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            logger.exception("Account launch failed")

    def _record_recent_launch(self, entry_type: str, identifier: str) -> None:
        SettingsManager.instance().add_recent_launch(entry_type, identifier)
        self._game_view_panel.refresh_launch_lists()

    def _update_recent_placeholder(self) -> None:
        action = QAction("(No recent files)", self)
        action.setEnabled(False)
        self._recent_menu.addAction(action)
