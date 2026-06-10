"""Shared P1–P4 checkbox widget used in param forms and strategy editors."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QWidget

_BTN_STYLE = "QPushButton { font-size: 10px; padding: 1px 4px; }"


class PlayerListWidget(QWidget):
    """P1–P4 checkboxes with Select All / None buttons.

    Returns list[int] of checked 1-based player numbers via get_value().
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._checks: list[QCheckBox] = []
        for i in range(1, 5):
            chk = QCheckBox(f"P{i}")
            chk.checkStateChanged.connect(self.changed)
            layout.addWidget(chk)
            self._checks.append(chk)
        layout.addStretch()
        all_btn = QPushButton("All")
        all_btn.setFixedHeight(20)
        all_btn.setStyleSheet(_BTN_STYLE)
        all_btn.clicked.connect(self._select_all)
        none_btn = QPushButton("None")
        none_btn.setFixedHeight(20)
        none_btn.setStyleSheet(_BTN_STYLE)
        none_btn.clicked.connect(self._deselect_all)
        layout.addWidget(all_btn)
        layout.addWidget(none_btn)

    def get_value(self) -> list[int]:
        return [i + 1 for i, chk in enumerate(self._checks) if chk.isChecked()]

    def set_value(self, value: list[int]) -> None:
        for i, chk in enumerate(self._checks):
            chk.setChecked((i + 1) in value)

    def _select_all(self) -> None:
        for chk in self._checks:
            chk.setChecked(True)

    def _deselect_all(self) -> None:
        for chk in self._checks:
            chk.setChecked(False)
