import asyncio
import traceback

from PyQt6.QtWidgets import QMainWindow, QLabel, QMenu, QMenuBar, QMessageBox, QSplitter
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtCore import Qt, pyqtSignal

from bartlebot.credentials import CredentialStore
from bartlebot.gui.canvas.bot_canvas import BotCanvas
from bartlebot.gui.game_embed.game_view import GameView
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.right_sidebar import RightSidebar
from bartlebot.settings_manager import SettingsManager


class MainWindow(QMainWindow):
    # Emitted when a recent-file menu entry is clicked (passes the file path)
    open_recent_requested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")

        self._build_menu()
        self._build_central()
        self._build_statusbar()
        self._apply_minimum_size()
        self._apply_startup_layout()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        file_menu = menu_bar.addMenu("File")

        self.action_new = QAction("New Bot", self)
        self.action_new.setShortcut(QKeySequence("Ctrl+N"))
        file_menu.addAction(self.action_new)

        self.action_open = QAction("Open Bot…", self)
        self.action_open.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addAction(self.action_open)

        self._recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self._recent_menu)
        self._update_recent_placeholder()

        file_menu.addSeparator()

        self.action_save = QAction("Save Bot", self)
        self.action_save.setShortcut(QKeySequence("Ctrl+S"))
        self.action_save.setEnabled(False)
        file_menu.addAction(self.action_save)

        self.action_save_as = QAction("Save Bot As…", self)
        self.action_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.action_save_as.setEnabled(False)
        file_menu.addAction(self.action_save_as)

        # Settings menu
        settings_menu = menu_bar.addMenu("Settings")
        settings_action = QAction("Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        settings_menu.addAction(settings_action)

        # View menu
        view_menu = menu_bar.addMenu("View")
        self.action_toggle_game_view = QAction("Toggle Game View", self)
        self.action_toggle_game_view.setShortcut(QKeySequence("Ctrl+G"))
        view_menu.addAction(self.action_toggle_game_view)

        # Tools menu (launcher)
        self._tools_menu = menu_bar.addMenu("Tools")
        self._rebuild_launch_menu()

    def _build_central(self) -> None:
        # BotCanvas must be created first — it owns param_panel and log_widget
        self.bot_canvas = BotCanvas()
        self.left_sidebar = LeftSidebar(
            self.bot_canvas.param_panel,
            self.bot_canvas.log_widget,
        )
        self.right_sidebar = RightSidebar()
        self.game_view = GameView()

        # Wire View menu
        self.action_toggle_game_view.triggered.connect(
            lambda: self.show_game_view(not self._game_view_visible)
        )

        # Wire sidebar tab switching from canvas
        self.bot_canvas.tab_switch.connect(self._on_canvas_tab_switch)

        # Top row: left sidebar | game view | right sidebar
        _splitter_style = (
            "QSplitter::handle { background: #888; }"
            "QSplitter::handle:horizontal { width: 2px; }"
            "QSplitter::handle:vertical   { height: 2px; }"
        )

        self._top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._top_splitter.setChildrenCollapsible(False)
        self._top_splitter.addWidget(self.left_sidebar)
        self._top_splitter.addWidget(self.game_view)
        self._top_splitter.addWidget(self.right_sidebar)
        self._top_splitter.setStretchFactor(1, 1)
        sm = SettingsManager.instance()
        left_w  = sm.get_general("left_sidebar_width",    default=300)
        right_w = sm.get_general("right_sidebar_width",   default=300)
        bot_h   = sm.get_general("bottom_section_height", default=220)
        self._top_splitter.setSizes([left_w, 800, right_w])
        self._top_splitter.setStyleSheet(_splitter_style)

        # Outer vertical splitter: top row (game + sidebars) | bot canvas
        self._outer_splitter = QSplitter(Qt.Orientation.Vertical)
        self._outer_splitter.setChildrenCollapsible(False)
        self._outer_splitter.addWidget(self._top_splitter)
        self._outer_splitter.addWidget(self.bot_canvas)
        self._outer_splitter.setStretchFactor(0, 1)
        self._outer_splitter.setSizes([600, bot_h])
        self._outer_splitter.setStyleSheet(_splitter_style)

        self.setCentralWidget(self._outer_splitter)

        # Initially hide GameView (no clients yet); BotCanvas expands
        self._game_view_visible: bool = False
        self.game_view.setVisible(False)

    def _build_statusbar(self) -> None:
        self.zone_label = QLabel("Waiting for client…")
        self.statusBar().addWidget(self.zone_label, 1)

    def _apply_minimum_size(self) -> None:
        min_w, min_h = SettingsManager.instance().minimum_window_size()
        self.setMinimumSize(min_w, min_h)

    def _apply_startup_layout(self) -> None:
        sm = SettingsManager.instance()
        min_w, min_h = sm.minimum_window_size()
        if sm.get_general("maximize_on_open", default=False):
            self.showMaximized()
        else:
            w = max(sm.get_general("window_width",  default=1400), min_w)
            h = max(sm.get_general("window_height", default=800),  min_h)
            self.resize(w, h)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_zone(self, zone: str | None) -> None:
        self.zone_label.setText(zone if zone else "Unknown")

    def set_fsm_state(self, state_id: str) -> None:
        self.bot_canvas.set_state(state_id)

    def set_bot_name(self, _name: str, filename: str | None) -> None:
        if filename:
            self.setWindowTitle(f"BartleBot — {filename}")
            self.left_sidebar.bot_info.set_file(filename)
        else:
            self.setWindowTitle("BartleBot")
            self.left_sidebar.bot_info.set_file(None)

    def set_start_enabled(self, enabled: bool) -> None:
        self.bot_canvas.set_run_enabled(enabled)

    def update_recent_menu(self, paths: list) -> None:
        """Rebuild the Open Recent submenu from a list of file paths."""
        self._recent_menu.clear()
        if not paths:
            self._update_recent_placeholder()
            return
        for path in paths:
            action = QAction(path, self)
            action.triggered.connect(lambda _checked, p=path: self.open_recent_requested.emit(p))
            self._recent_menu.addAction(action)

    def set_save_enabled(self, enabled: bool) -> None:
        self.action_save.setEnabled(enabled)
        self.action_save_as.setEnabled(enabled)

    def show_game_view(self, visible: bool) -> None:
        """Show or hide the top game-view area. When hidden, bot canvas fills the window."""
        if visible == self._game_view_visible:
            return
        self._game_view_visible = visible
        self.game_view.setVisible(visible)
        bot_h = self._outer_splitter.sizes()[1]
        if visible:
            self._outer_splitter.setSizes([600, bot_h])
        else:
            self._outer_splitter.setSizes([0, bot_h])

    def refresh_minimum_size(self) -> None:
        """Re-apply minimum size after a settings change."""
        self._apply_minimum_size()

    def _rebuild_launch_menu(self) -> None:
        """Rebuild Tools → Launch submenu from current accounts and profiles."""
        self._tools_menu.clear()

        launch_menu = QMenu("Launch", self)
        self._tools_menu.addMenu(launch_menu)

        # --- Profiles section ---
        profiles_lbl = QAction("─ Profiles ─", self)
        profiles_lbl.setEnabled(False)
        launch_menu.addAction(profiles_lbl)

        profiles = SettingsManager.instance().launch_profiles
        if profiles:
            for profile in profiles:
                name = profile.get("name", "Unnamed")
                action = QAction(name, self)
                action.triggered.connect(
                    lambda _checked, n=name: asyncio.ensure_future(self._launch_profile(n))
                )
                launch_menu.addAction(action)
        else:
            empty = QAction("(No profiles)", self)
            empty.setEnabled(False)
            launch_menu.addAction(empty)

        launch_menu.addSeparator()

        # --- Accounts section ---
        accounts_lbl = QAction("─ Accounts ─", self)
        accounts_lbl.setEnabled(False)
        launch_menu.addAction(accounts_lbl)

        accounts = SettingsManager.instance().accounts
        if accounts:
            for acct in accounts:
                username = acct.get("username", "")
                alias    = acct.get("alias") or None
                display  = CredentialStore.display_name(username, alias)
                action   = QAction(display, self)
                action.triggered.connect(
                    lambda _checked, u=username, d=display:
                        asyncio.ensure_future(self._launch_single_account(u, d))
                )
                launch_menu.addAction(action)
        else:
            empty2 = QAction("(No accounts)", self)
            empty2.setEnabled(False)
            launch_menu.addAction(empty2)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_canvas_tab_switch(self, tab: str) -> None:
        if tab == "log":
            self.left_sidebar.switch_to_log()
        elif tab == "properties":
            self.left_sidebar.switch_to_properties()
        elif tab == "bot":
            self.left_sidebar.switch_to_bot()

    def _open_settings(self) -> None:
        from bartlebot.gui.settings.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._rebuild_launch_menu()
            self.refresh_minimum_size()

    def _open_launcher(self) -> None:
        from bartlebot.gui.launcher_popup import LauncherPopup
        popup = LauncherPopup(self)
        popup.show()

    async def _launch_profile(self, profile_name: str) -> None:
        try:
            from bartlebot.launcher.launch_profile import launch_profile
            await launch_profile(profile_name)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            print(traceback.format_exc())

    async def _launch_single_account(self, username: str, display_name: str) -> None:
        try:
            from bartlebot.launcher.launch_profile import launch_account
            password = CredentialStore.retrieve(username)
            if password is None:
                QMessageBox.critical(
                    self, "Launch failed",
                    f"No stored password for '{username}'.\n"
                    "Please add the account in Settings → Accounts.",
                )
                return
            await launch_account(username, password, display_name)
        except Exception as e:
            QMessageBox.critical(self, "Launch failed", str(e))
            print(traceback.format_exc())

    def _update_recent_placeholder(self) -> None:
        action = QAction("(No recent files)", self)
        action.setEnabled(False)
        self._recent_menu.addAction(action)
