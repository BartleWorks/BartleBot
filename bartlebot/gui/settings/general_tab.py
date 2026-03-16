"""Settings tab: General (window and layout defaults)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget,
)

from bartlebot.settings_manager import BOTCANVAS_MIN_HEIGHT, SIDEBAR_MIN_WIDTH, SettingsManager


class GeneralTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        sm = SettingsManager.instance()

        # Maximize on open
        self._maximize_cb = QCheckBox()
        self._maximize_cb.setChecked(sm.get_general("maximize_on_open", default=False))
        form.addRow("Maximize on open:", self._maximize_cb)

        # Default window size (width × height)
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
        form.addRow("Default window size:", size_row)

        # Left sidebar width
        self._left_sb = QSpinBox()
        self._left_sb.setRange(SIDEBAR_MIN_WIDTH, 1200)
        self._left_sb.setSuffix(" px")
        self._left_sb.setValue(sm.get_general("left_sidebar_width", default=300))
        form.addRow("Default left sidebar width:", self._left_sb)

        # Right sidebar width
        self._right_sb = QSpinBox()
        self._right_sb.setRange(SIDEBAR_MIN_WIDTH, 1200)
        self._right_sb.setSuffix(" px")
        self._right_sb.setValue(sm.get_general("right_sidebar_width", default=300))
        form.addRow("Default right sidebar width:", self._right_sb)

        # Bottom section height
        self._bottom_sb = QSpinBox()
        self._bottom_sb.setRange(BOTCANVAS_MIN_HEIGHT, 800)
        self._bottom_sb.setSuffix(" px")
        self._bottom_sb.setValue(sm.get_general("bottom_section_height", default=220))
        form.addRow("Default bottom section height:", self._bottom_sb)

        layout.addLayout(form)
        layout.addStretch()

        # Disable size fields when maximized is checked
        self._maximize_cb.toggled.connect(self._on_maximize_toggled)
        self._on_maximize_toggled(self._maximize_cb.isChecked())

    def _on_maximize_toggled(self, checked: bool) -> None:
        self._width_sb.setEnabled(not checked)
        self._height_sb.setEnabled(not checked)

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.set_general("maximize_on_open",      self._maximize_cb.isChecked())
        sm.set_general("window_width",          self._width_sb.value())
        sm.set_general("window_height",         self._height_sb.value())
        sm.set_general("left_sidebar_width",    self._left_sb.value())
        sm.set_general("right_sidebar_width",   self._right_sb.value())
        sm.set_general("bottom_section_height", self._bottom_sb.value())
