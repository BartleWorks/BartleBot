from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QWidget
)
from PyQt6.QtCore import Qt


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")
        self.setMinimumSize(800, 500)

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
        layout.addWidget(self.fsm_label)
        layout.addWidget(self.zone_label)
        layout.addLayout(btn_row)
        layout.addStretch()

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    def set_zone(self, zone: str | None) -> None:
        self.zone_label.setText(zone if zone else "Unknown")

    def set_fsm_state(self, state_id: str) -> None:
        self.fsm_label.setText(f"Bot: {state_id}")

    def set_start_enabled(self, enabled: bool) -> None:
        self.start_button.setEnabled(enabled)

    def set_stop_enabled(self, enabled: bool) -> None:
        self.stop_button.setEnabled(enabled)
