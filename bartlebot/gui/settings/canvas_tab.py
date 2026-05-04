"""Settings tab: Canvas (node editor display and layout options)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
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

        # Grid style
        self._grid_combo = QComboBox()
        self._grid_combo.addItems(["Dots", "Lines", "None"])
        _grid_map = {"dots": 0, "lines": 1, "none": 2}
        self._grid_combo.setCurrentIndex(_grid_map.get(sm.get("canvas", "grid_style", default="dots"), 0))
        form.addRow("Grid style:", self._grid_combo)

        # LOD compact threshold
        self._lod_sb = QDoubleSpinBox()
        self._lod_sb.setRange(0.10, 1.00)
        self._lod_sb.setSingleStep(0.05)
        self._lod_sb.setDecimals(2)
        self._lod_sb.setValue(sm.get("canvas", "lod_compact_threshold", default=0.6))
        form.addRow("Compact zoom threshold:", self._lod_sb)

        # Zoom step
        self._zoom_sb = QDoubleSpinBox()
        self._zoom_sb.setRange(1.05, 1.30)
        self._zoom_sb.setSingleStep(0.01)
        self._zoom_sb.setDecimals(2)
        self._zoom_sb.setValue(sm.get("canvas", "zoom_step", default=1.15))
        form.addRow("Zoom step:", self._zoom_sb)

        # Pan animation duration
        self._pan_sb = QSpinBox()
        self._pan_sb.setRange(0, 500)
        self._pan_sb.setSingleStep(50)
        self._pan_sb.setSuffix(" ms")
        self._pan_sb.setSpecialValueText("Instant")
        self._pan_sb.setValue(sm.get("canvas", "pan_animation_ms", default=300))
        form.addRow("Pan animation speed:", self._pan_sb)

        from bartlebot.gui.theme import THEME

        def _hint(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
            return lbl

        layout.addLayout(form)
        layout.addWidget(
            _hint(
                "Compact zoom threshold: nodes switch to label-only view below this zoom level.\n"
                "Zoom step: wheel zoom multiplier per tick — smaller = finer control, larger = faster traversal.\n"
                "Pan animation speed: how long the camera takes to pan to a node (0 = instant)."
            )
        )
        layout.addStretch()

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.set("canvas", "minimap_visible", value=self._minimap_cb.isChecked())
        sm.set("canvas", "snap_to_grid", value=self._snap_cb.isChecked())
        sm.set("canvas", "lod_compact_threshold", value=round(self._lod_sb.value(), 2))
        sm.set("canvas", "zoom_step", value=round(self._zoom_sb.value(), 2))
        sm.set("canvas", "pan_animation_ms", value=self._pan_sb.value())
        _grid_values = ["dots", "lines", "none"]
        sm.set("canvas", "grid_style", value=_grid_values[self._grid_combo.currentIndex()])
