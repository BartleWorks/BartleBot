from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QWidget, QMenuBar
)
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtCore import Qt


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")
        self.setMinimumSize(800, 500)

        # Callbacks wired from bartlebot.py
        self.on_open_bot = lambda: None
        self.on_save_bot = lambda: None
        self.on_save_bot_as = lambda: None

        self._build_menu()
        self._build_central()

    def _build_menu(self):
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

    def _build_central(self):
        self.bot_label = QLabel("No bot loaded")
        self.bot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.fsm_label = QLabel("Bot: Idle")
        self.fsm_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.zone_label = QLabel("Waiting for client…")
        self.zone_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.start_button = QPushButton("Start")
        self.start_button.setEnabled(False)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.start_button)
        btn_row.addWidget(self.stop_button)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(self.bot_label)
        layout.addWidget(self.fsm_label)
        layout.addWidget(self.zone_label)
        layout.addLayout(btn_row)
        layout.addStretch()

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    # --- Update methods ---

    def set_zone(self, zone: str | None) -> None:
        self.zone_label.setText(zone if zone else "Unknown")

    def set_fsm_state(self, state_id: str) -> None:
        self.fsm_label.setText(f"Bot: {state_id}")

    def set_bot_name(self, name: str, filename: str | None) -> None:
        if filename:
            self.bot_label.setText(f"{name} ({filename})")
        else:
            self.bot_label.setText(name)

    def set_start_enabled(self, enabled: bool) -> None:
        self.start_button.setEnabled(enabled)

    def set_stop_enabled(self, enabled: bool) -> None:
        self.stop_button.setEnabled(enabled)

    def set_save_enabled(self, enabled: bool) -> None:
        self._save_action.setEnabled(enabled)
        self._save_as_action.setEnabled(enabled)
