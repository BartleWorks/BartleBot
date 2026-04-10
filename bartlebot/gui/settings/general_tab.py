"""Settings tab: General (window and layout defaults)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bartlebot.settings_manager import BOTCANVAS_MIN_HEIGHT, SIDEBAR_MIN_WIDTH, SettingsManager


def _section_label(title: str) -> QLabel:
    """Bold section header label, styled like _SectionHeader in sidebar_section.py."""
    lbl = QLabel(title)
    font = lbl.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 1)
    lbl.setFont(font)
    lbl.setContentsMargins(0, 8, 0, 4)
    return lbl


class GeneralTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        sm = SettingsManager.instance()

        # ------------------------------------------------------------------
        # Log section
        # ------------------------------------------------------------------
        layout.addWidget(_section_label("Log"))

        log_form = QFormLayout()
        log_form.setSpacing(8)

        self._clear_log_cb = QCheckBox()
        self._clear_log_cb.setChecked(sm.get_general("clear_log_on_run", default=True))
        log_form.addRow("Clear log on Run:", self._clear_log_cb)

        self._log_max_lines_sb = QSpinBox()
        self._log_max_lines_sb.setRange(500, 50000)
        self._log_max_lines_sb.setSingleStep(500)
        self._log_max_lines_sb.setSuffix(" lines")
        self._log_max_lines_sb.setValue(sm.get_general("log_max_lines", default=5000))
        log_form.addRow("Max log lines:", self._log_max_lines_sb)

        layout.addLayout(log_form)

        # ------------------------------------------------------------------
        # Layout section
        # ------------------------------------------------------------------
        layout.addWidget(_section_label("Layout"))

        layout_form = QFormLayout()
        layout_form.setSpacing(8)

        self._maximize_cb = QCheckBox()
        self._maximize_cb.setChecked(sm.get_general("maximize_on_open", default=False))
        layout_form.addRow("Maximize on open:", self._maximize_cb)

        self._width_sb = QSpinBox()
        self._width_sb.setRange(800, 3840)
        self._width_sb.setSuffix(" px")
        self._width_sb.setValue(sm.get_general("window_width", default=1400))

        self._height_sb = QSpinBox()
        self._height_sb.setRange(600, 2160)
        self._height_sb.setSuffix(" px")
        self._height_sb.setValue(sm.get_general("window_height", default=800))

        size_row = QHBoxLayout()
        size_row.setSpacing(6)
        size_row.addWidget(self._width_sb)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self._height_sb)
        size_row.addStretch()
        layout_form.addRow("Default window size:", size_row)

        self._left_sb = QSpinBox()
        self._left_sb.setRange(SIDEBAR_MIN_WIDTH, 1200)
        self._left_sb.setSuffix(" px")
        self._left_sb.setValue(sm.get_general("left_sidebar_width", default=300))
        layout_form.addRow("Default left sidebar width:", self._left_sb)

        self._right_sb = QSpinBox()
        self._right_sb.setRange(SIDEBAR_MIN_WIDTH, 1200)
        self._right_sb.setSuffix(" px")
        self._right_sb.setValue(sm.get_general("right_sidebar_width", default=300))
        layout_form.addRow("Default right sidebar width:", self._right_sb)

        self._bottom_sb = QSpinBox()
        self._bottom_sb.setRange(BOTCANVAS_MIN_HEIGHT, 800)
        self._bottom_sb.setSuffix(" px")
        self._bottom_sb.setValue(sm.get_general("bottom_section_height", default=220))
        layout_form.addRow("Default bottom section height:", self._bottom_sb)

        layout.addLayout(layout_form)

        # ------------------------------------------------------------------
        # Auto-save section
        # ------------------------------------------------------------------
        layout.addWidget(_section_label("Auto-save"))

        autosave_form = QFormLayout()
        autosave_form.setSpacing(8)

        self._autosave_cb = QCheckBox()
        self._autosave_cb.setChecked(sm.get_general("auto_save_enabled", default=True))
        autosave_form.addRow("Auto-save enabled:", self._autosave_cb)

        self._autosave_interval_sb = QSpinBox()
        self._autosave_interval_sb.setRange(30, 3600)
        self._autosave_interval_sb.setSingleStep(30)
        self._autosave_interval_sb.setSuffix(" sec")
        self._autosave_interval_sb.setValue(sm.get_general("auto_save_interval_seconds", default=300))
        autosave_form.addRow("Auto-save interval:", self._autosave_interval_sb)

        layout.addLayout(autosave_form)
        layout.addStretch()

        # Disable size fields when maximized is checked
        self._maximize_cb.toggled.connect(self._on_maximize_toggled)
        self._on_maximize_toggled(self._maximize_cb.isChecked())

        # Disable interval spinbox when auto-save is unchecked
        self._autosave_cb.toggled.connect(self._on_autosave_toggled)
        self._on_autosave_toggled(self._autosave_cb.isChecked())

    def _on_maximize_toggled(self, checked: bool) -> None:
        self._width_sb.setEnabled(not checked)
        self._height_sb.setEnabled(not checked)

    def _on_autosave_toggled(self, checked: bool) -> None:
        self._autosave_interval_sb.setEnabled(checked)

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.set_general("clear_log_on_run", self._clear_log_cb.isChecked())
        sm.set_general("log_max_lines", self._log_max_lines_sb.value())
        sm.set_general("maximize_on_open", self._maximize_cb.isChecked())
        sm.set_general("window_width", self._width_sb.value())
        sm.set_general("window_height", self._height_sb.value())
        sm.set_general("left_sidebar_width", self._left_sb.value())
        sm.set_general("right_sidebar_width", self._right_sb.value())
        sm.set_general("bottom_section_height", self._bottom_sb.value())
        sm.set_general("auto_save_enabled", self._autosave_cb.isChecked())
        sm.set_general("auto_save_interval_seconds", self._autosave_interval_sb.value())
