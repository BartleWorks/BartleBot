"""Settings tab: Game Embed (resolution selection)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QLabel, QVBoxLayout, QWidget,
)

from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager


class GameEmbedTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self._res_combo = QComboBox()
        for w, h in VALID_RESOLUTIONS:
            self._res_combo.addItem(f"{w}×{h}", userData=(w, h))

        # Select current setting
        current = SettingsManager.instance().game_resolution
        for i in range(self._res_combo.count()):
            if self._res_combo.itemData(i) == current:
                self._res_combo.setCurrentIndex(i)
                break

        form.addRow("Game resolution:", self._res_combo)
        layout.addLayout(form)

        hint = QLabel(
            "Set Wizard101 to Windowed mode in\n"
            "Options → Graphics, with this resolution."
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)
        layout.addStretch()

    def apply(self) -> None:
        """Persist the selected resolution to SettingsManager."""
        resolution = self._res_combo.currentData()
        if resolution:
            SettingsManager.instance().game_resolution = resolution
