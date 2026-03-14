from PyQt6.QtWidgets import QMainWindow, QLabel, QMenu, QMenuBar, QSplitter
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtCore import Qt

from bartlebot.gui.bot_canvas import BotCanvas
from bartlebot.gui.left_sidebar import LeftSidebar
from bartlebot.gui.right_sidebar import RightSidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")
        self.resize(1400, 800)
        self.setMinimumSize(1100, 650)

        # Callbacks wired from bartlebot.py
        self.on_new_bot = lambda: None
        self.on_open_bot = lambda: None
        self.on_open_recent = lambda _path: None
        self.on_save_bot = lambda: None
        self.on_save_bot_as = lambda: None

        self._build_menu()
        self._build_central()
        self._build_statusbar()

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

    def _build_central(self) -> None:
        self.left_sidebar = LeftSidebar()
        self.bot_canvas = BotCanvas()
        self.right_sidebar = RightSidebar()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.left_sidebar)
        splitter.addWidget(self.bot_canvas)
        splitter.addWidget(self.right_sidebar)
        splitter.setStretchFactor(1, 1)  # canvas takes all extra space
        splitter.setSizes([220, 800, 220])

        self.setCentralWidget(splitter)

    def _build_statusbar(self) -> None:
        self.zone_label = QLabel("Waiting for client…")
        self.statusBar().addWidget(self.zone_label, 1)

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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_recent_placeholder(self) -> None:
        action = QAction("(No recent files)", self)
        action.setEnabled(False)
        self._recent_menu.addAction(action)
