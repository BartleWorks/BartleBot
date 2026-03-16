from PyQt6.QtWidgets import QMainWindow, QLabel, QMenu, QMenuBar, QSplitter
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtCore import Qt

from bartlebot.gui.canvas.bot_canvas import BotCanvas
from bartlebot.gui.game_embed.game_view import GameView
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.right_sidebar import RightSidebar
from bartlebot.settings_manager import SettingsManager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")

        # Callbacks wired from bartlebot.py
        self.on_new_bot = lambda: None
        self.on_open_bot = lambda: None
        self.on_open_recent = lambda _path: None
        self.on_save_bot = lambda: None
        self.on_save_bot_as = lambda: None

        self._build_menu()
        self._build_central()
        self._build_statusbar()
        self._apply_minimum_size()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        file_menu = menu_bar.addMenu("File")

        new_action = QAction("New Bot", self)
        new_action.setShortcut(QKeySequence("Ctrl+N"))
        new_action.triggered.connect(lambda: self.on_new_bot())
        file_menu.addAction(new_action)

        open_action = QAction("Open Bot…", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(lambda: self.on_open_bot())
        file_menu.addAction(open_action)

        self._recent_menu = QMenu("Open Recent", self)
        file_menu.addMenu(self._recent_menu)
        self._update_recent_placeholder()

        file_menu.addSeparator()

        self._save_action = QAction("Save Bot", self)
        self._save_action.setShortcut(QKeySequence("Ctrl+S"))
        self._save_action.setEnabled(False)
        self._save_action.triggered.connect(lambda: self.on_save_bot())
        file_menu.addAction(self._save_action)

        self._save_as_action = QAction("Save Bot As…", self)
        self._save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._save_as_action.setEnabled(False)
        self._save_as_action.triggered.connect(lambda: self.on_save_bot_as())
        file_menu.addAction(self._save_as_action)

        # Settings menu
        settings_menu = menu_bar.addMenu("Settings")
        settings_action = QAction("Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        settings_menu.addAction(settings_action)

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

        # Wire sidebar tab switching from canvas
        self.bot_canvas.on_tab_switch = self._on_canvas_tab_switch

        # Top row: left sidebar | game view | right sidebar
        self._top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._top_splitter.setChildrenCollapsible(False)
        self._top_splitter.addWidget(self.left_sidebar)
        self._top_splitter.addWidget(self.game_view)
        self._top_splitter.addWidget(self.right_sidebar)
        self._top_splitter.setStretchFactor(1, 1)
        self._top_splitter.setSizes([300, 800, 300])

        # Outer vertical splitter: top row (game + sidebars) | bot canvas
        self._outer_splitter = QSplitter(Qt.Orientation.Vertical)
        self._outer_splitter.setChildrenCollapsible(False)
        self._outer_splitter.addWidget(self._top_splitter)
        self._outer_splitter.addWidget(self.bot_canvas)
        self._outer_splitter.setStretchFactor(0, 1)
        self._outer_splitter.setSizes([600, 220])

        self.setCentralWidget(self._outer_splitter)

        # Initially hide GameView (no clients yet); BotCanvas expands
        self.game_view.setVisible(False)

    def _build_statusbar(self) -> None:
        self.zone_label = QLabel("Waiting for client…")
        self.statusBar().addWidget(self.zone_label, 1)

    def _apply_minimum_size(self) -> None:
        min_w, min_h = SettingsManager.instance().minimum_window_size()
        self.setMinimumSize(min_w, min_h)
        self.resize(max(1400, min_w), max(800, min_h))

    # ------------------------------------------------------------------
    # Public API (called from bartlebot.py — signatures must stay stable)
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
            action.triggered.connect(lambda _checked, p=path: self.on_open_recent(p))
            self._recent_menu.addAction(action)

    def set_save_enabled(self, enabled: bool) -> None:
        self._save_action.setEnabled(enabled)
        self._save_as_action.setEnabled(enabled)

    def show_game_view(self, visible: bool) -> None:
        """Show or hide the top game-view area. When hidden, bot canvas fills the window."""
        self.game_view.setVisible(visible)
        if visible:
            self._outer_splitter.setSizes([600, 220])
        else:
            self._outer_splitter.setSizes([0, 1])

    def refresh_minimum_size(self) -> None:
        """Re-apply minimum size after a settings change."""
        self._apply_minimum_size()

    def _rebuild_launch_menu(self) -> None:
        """Rebuild Tools → Launch menu from current launch profiles."""
        self._tools_menu.clear()
        launch_action = QAction("Launch…", self)
        launch_action.triggered.connect(self._open_launcher)
        self._tools_menu.addAction(launch_action)

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
            self.refresh_minimum_size()
            # Notify keybinding manager to reload if wired
            if callable(getattr(self, "on_settings_saved", None)):
                self.on_settings_saved()

    def _open_launcher(self) -> None:
        from bartlebot.gui.launcher_popup import LauncherPopup
        popup = LauncherPopup(self)
        popup.show()

    def _update_recent_placeholder(self) -> None:
        action = QAction("(No recent files)", self)
        action.setEnabled(False)
        self._recent_menu.addAction(action)
