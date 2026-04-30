"""Settings tab: Canvas (node editor display and layout options)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from bartlebot.settings_manager import SettingsManager


class CanvasTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        sm = SettingsManager.instance()

        # Enable minimap
        self._minimap_cb = QCheckBox()
        self._minimap_cb.setChecked(sm.get("canvas", "minimap_visible", default=True))
        form.addRow("Enable minimap:", self._minimap_cb)

        # Snap to grid
        self._snap_cb = QCheckBox()
        self._snap_cb.setChecked(sm.get("canvas", "snap_to_grid", default=False))
        form.addRow("Snap nodes to grid:", self._snap_cb)

        # LOD compact threshold
        self._lod_sb = QDoubleSpinBox()
        self._lod_sb.setRange(0.10, 1.00)
        self._lod_sb.setSingleStep(0.05)
        self._lod_sb.setDecimals(2)
        self._lod_sb.setValue(sm.get("canvas", "lod_compact_threshold", default=0.6))
        form.addRow("Compact zoom threshold:", self._lod_sb)

        hint = QLabel(
            "Nodes switch to a simplified label-only view below this zoom level.\n"
            "Lower values keep full detail longer; higher values compact sooner.\n"
            "Range: 0.10 (compact only when very zoomed out) – 1.00 (always compact)."
        )
        hint.setWordWrap(True)
        from bartlebot.gui.theme import THEME

        hint.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")

        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addStretch()

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.set("canvas", "minimap_visible", value=self._minimap_cb.isChecked())
        sm.set("canvas", "snap_to_grid", value=self._snap_cb.isChecked())
        sm.set("canvas", "lod_compact_threshold", value=round(self._lod_sb.value(), 2))
