"""Settings tab: Canvas (node editor display and layout options)."""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox

from bartlebot.gui.settings.rows import SettingGroup, SettingRow, SettingsForm
from bartlebot.settings_manager import SettingsManager, default_for


def _default(key: str):
    return default_for("canvas", key)


class CanvasTab(SettingsForm):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        sm = SettingsManager.instance()

        self.add_heading("View")

        self._minimap_cb = QCheckBox()
        self._minimap_cb.setChecked(sm.get("canvas", "minimap_visible", default=True))
        self.add_row(
            SettingRow(
                "Show minimap",
                "Show an overview of the bot in the canvas corner.",
                self._minimap_cb,
                default=_default("minimap_visible"),
                details="Click or drag in the minimap to move the view.",
            )
        )

        self._lod_sb = QDoubleSpinBox()
        self._lod_sb.setRange(0.10, 1.00)
        self._lod_sb.setSingleStep(0.05)
        self._lod_sb.setDecimals(2)
        self._lod_sb.setValue(sm.get("canvas", "lod_compact_threshold", default=0.6))
        self.add_row(
            SettingRow(
                "Compact zoom threshold",
                "Below this zoom, nodes show their label only.",
                self._lod_sb,
                default=_default("lod_compact_threshold"),
            )
        )

        self._zoom_sb = QDoubleSpinBox()
        self._zoom_sb.setRange(1.05, 1.30)
        self._zoom_sb.setSingleStep(0.01)
        self._zoom_sb.setDecimals(2)
        self._zoom_sb.setValue(sm.get("canvas", "zoom_step", default=1.15))
        self.add_row(
            SettingRow(
                "Zoom step",
                "How far one wheel tick zooms.",
                self._zoom_sb,
                default=_default("zoom_step"),
                details="Smaller steps suit trackpads.",
            )
        )

        self._pan_sb = QSpinBox()
        self._pan_sb.setRange(0, 500)
        self._pan_sb.setSingleStep(50)
        self._pan_sb.setSuffix(" ms")
        self._pan_sb.setSpecialValueText("Instant")
        self._pan_sb.setValue(sm.get("canvas", "pan_animation_ms", default=300))
        self.add_row(
            SettingRow(
                "Pan animation time",
                "How long the camera takes to glide to a node.",
                self._pan_sb,
                default=_default("pan_animation_ms"),
            )
        )

        self.add_heading("Grid")

        self._snap_cb = QCheckBox()
        self._snap_cb.setChecked(sm.get("canvas", "snap_to_grid", default=True))
        snap = SettingRow(
            "Snap nodes to grid",
            "Align a dragged node to the grid.",
            self._snap_cb,
            default=_default("snap_to_grid"),
        )

        self._grid_combo = QComboBox()
        for label, value in (("Dots", "dots"), ("Lines", "lines"), ("None", "none")):
            self._grid_combo.addItem(label, userData=value)
        current = sm.get("canvas", "grid_style", default="dots")
        self._grid_combo.setCurrentIndex(max(self._grid_combo.findData(current), 0))
        grid = SettingRow(
            "Grid style",
            "Which grid to draw behind the nodes.",
            self._grid_combo,
            default=_default("grid_style"),
        )
        self.add_group(SettingGroup("grid", snap, [grid]))

        self.finish()

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.set("canvas", "minimap_visible", value=self._minimap_cb.isChecked())
        sm.set("canvas", "snap_to_grid", value=self._snap_cb.isChecked())
        sm.set("canvas", "lod_compact_threshold", value=round(self._lod_sb.value(), 2))
        sm.set("canvas", "zoom_step", value=round(self._zoom_sb.value(), 2))
        sm.set("canvas", "pan_animation_ms", value=self._pan_sb.value())
        sm.set("canvas", "grid_style", value=self._grid_combo.currentData())
