from PyQt6.QtWidgets import (
    QMainWindow, QLabel,
    QVBoxLayout, QHBoxLayout, QWidget,
    QMenuBar, QFrame, QTabWidget, QStackedWidget,
)
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtCore import Qt

from bartlebot.gui.bot_info_panel import BotInfoPanel
from bartlebot.gui.drop_panel import DropPanel
from bartlebot.gui.running_tab import RunningTab
from bartlebot.gui.sidebar import ClientSidebar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")
        self.resize(1400, 800)
        self.setMinimumSize(1100, 650)

        # Callbacks wired from bartlebot.py
        self.on_open_bot = lambda: None
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

        open_action = QAction("Open Bot…", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(lambda: self.on_open_bot())
        file_menu.addAction(open_action)

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
        # ── Tabs (center) ─────────────────────────────────────────────
        self.running_tab = RunningTab()
        self.edit_tab = _placeholder_tab("Edit — coming soon")

        self.tabs = QTabWidget()
        self.tabs.addTab(self.edit_tab, "Edit")
        self.tabs.addTab(self.running_tab, "Run")
        self.tabs.currentChanged.connect(lambda _: self._on_tab_changed())

        # ── Right sidebar (swaps per tab) ──────────────────────────────
        self.client_sidebar = ClientSidebar()
        self.drop_panel = DropPanel()

        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(self.client_sidebar)  # index 0 — Edit
        self._right_stack.addWidget(self.drop_panel)       # index 1 — Run
        self._right_stack.setCurrentIndex(0)

        # ── Left sidebar (permanent) ───────────────────────────────────
        self.bot_info_panel = BotInfoPanel()

        # ── Outer layout ───────────────────────────────────────────────
        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self.bot_info_panel)
        outer.addWidget(_vdivider())
        outer.addWidget(self.tabs, 1)
        outer.addWidget(_vdivider())
        outer.addWidget(self._right_stack)

        central = QWidget()
        central.setLayout(outer)
        self.setCentralWidget(central)

    def _build_statusbar(self) -> None:
        self.zone_label = QLabel("Waiting for client…")
        self.statusBar().addWidget(self.zone_label, 1)

    # ------------------------------------------------------------------
    # Public API (called from bartlebot.py — signatures must stay stable)
    # ------------------------------------------------------------------

    def set_zone(self, zone: str | None) -> None:
        self.zone_label.setText(zone if zone else "Unknown")

    def set_fsm_state(self, state_id: str) -> None:
        self.running_tab.set_state(state_id)

    def set_bot_name(self, _name: str, filename: str | None) -> None:
        if filename:
            self.setWindowTitle(f"BartleBot — {filename}")
            self.bot_info_panel.set_file(filename)
        else:
            self.setWindowTitle("BartleBot")
            self.bot_info_panel.set_file(None)

    def set_start_enabled(self, enabled: bool) -> None:
        self.running_tab.set_run_enabled(enabled)

    def set_save_enabled(self, enabled: bool) -> None:
        self._save_action.setEnabled(enabled)
        self._save_as_action.setEnabled(enabled)

    def switch_to_running_tab(self) -> None:
        self.tabs.setCurrentWidget(self.running_tab)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_tab_changed(self) -> None:
        if self.tabs.currentWidget() is self.running_tab:
            self._right_stack.setCurrentIndex(1)
        else:
            self._right_stack.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _placeholder_tab(text: str) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)
    return widget


def _vdivider() -> QFrame:
    div = QFrame()
    div.setFrameShape(QFrame.Shape.VLine)
    div.setFrameShadow(QFrame.Shadow.Sunken)
    return div
