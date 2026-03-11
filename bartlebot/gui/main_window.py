from PyQt6.QtWidgets import QMainWindow, QLabel
from PyQt6.QtCore import Qt


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BartleBot")
        self.setMinimumSize(800, 500)

        self.zone_label = QLabel("Waiting for client…")
        self.zone_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(self.zone_label)

    def set_zone(self, zone: str | None) -> None:
        self.zone_label.setText(zone if zone else "Unknown")
