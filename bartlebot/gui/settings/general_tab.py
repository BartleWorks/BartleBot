"""Settings tab: General (log, window layout, privacy, auto-save).

The Bot section that used to sit at the bottom is :mod:`bartlebot.gui.settings.bot_tab`
now. What is left is the app itself -- how it opens, what it keeps, what it shows --
and none of it is a level of any bot option's resolution.
"""

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

from bartlebot.game_sizing import BOTCANVAS_MIN_HEIGHT, LEFT_SIDEBAR_MIN_WIDTH, RIGHT_SIDEBAR_MIN_WIDTH
from bartlebot.gui import screen_fit
from bartlebot.gui.settings.rows import hint
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.settings_manager import SettingsManager


class GeneralTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        sm = SettingsManager.instance()

        # ------------------------------------------------------------------
        # Log section
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Log"))

        log_form = QFormLayout()
        log_form.setSpacing(4)

        self._clear_log_cb = QCheckBox()
        self._clear_log_cb.setChecked(sm.get_general("clear_log_on_run", default=True))
        log_form.addRow("Clear log on Run:", self._clear_log_cb)
        log_form.addRow(
            hint(
                "Empty the Log tab each time a bot starts, so what is on screen belongs to the "
                "run you are watching. The session file in logs/ keeps everything either way."
            )
        )

        self._log_max_lines_sb = QSpinBox()
        self._log_max_lines_sb.setRange(500, 50000)
        self._log_max_lines_sb.setSingleStep(500)
        self._log_max_lines_sb.setSuffix(" lines")
        self._log_max_lines_sb.setValue(sm.get_general("log_max_lines", default=5000))
        log_form.addRow("Max log lines:", self._log_max_lines_sb)
        log_form.addRow(
            hint(
                "How much the Log tab holds before the oldest lines are dropped. Affects the "
                "widget only — the session file on disk is never trimmed."
            )
        )

        layout.addLayout(log_form)

        # ------------------------------------------------------------------
        # Layout section
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Layout"))

        layout_form = QFormLayout()
        layout_form.setSpacing(4)

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
        layout_form.addRow(
            hint(
                "The size the main window opens at, unless Maximize on open is ticked. Resizing "
                "the window afterwards does not change this."
            )
        )

        self._left_sb = QSpinBox()
        # The measured floor, not the constant -- see the identical note on the right one.
        self._left_sb.setRange(screen_fit.left_sidebar_floor(), 1200)
        self._left_sb.setSuffix(" px")
        self._left_sb.setValue(sm.get_general("left_sidebar_width", default=LEFT_SIDEBAR_MIN_WIDTH))
        layout_form.addRow("Default left sidebar width:", self._left_sb)
        layout_form.addRow(hint("The bot's own configuration — Options and Combat, or Options and Properties."))

        self._right_sb = QSpinBox()
        # The measured floor, not the constant: on a machine with larger system text the
        # panel will not go as narrow as the constant says, and a spin box that offers a
        # width the splitter refuses is a setting that silently does not take.
        self._right_sb.setRange(screen_fit.right_sidebar_floor(), 1200)
        self._right_sb.setSuffix(" px")
        self._right_sb.setValue(sm.get_general("right_sidebar_width", default=RIGHT_SIDEBAR_MIN_WIDTH))
        layout_form.addRow("Default right sidebar width:", self._right_sb)
        layout_form.addRow(hint("Players, Drops and Log."))

        self._bottom_sb = QSpinBox()
        self._bottom_sb.setRange(BOTCANVAS_MIN_HEIGHT, 800)
        self._bottom_sb.setSuffix(" px")
        self._bottom_sb.setValue(sm.get_general("bottom_section_height", default=220))
        layout_form.addRow("Default bottom section height:", self._bottom_sb)
        layout_form.addRow(hint("The bot canvas under the game view. Both sections are draggable once open."))

        layout.addLayout(layout_form)

        # ------------------------------------------------------------------
        # Privacy section
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Privacy"))

        privacy_form = QFormLayout()
        privacy_form.setSpacing(4)

        self._streamer_mode_cb = QCheckBox()
        self._streamer_mode_cb.setChecked(sm.streamer_mode)
        self._streamer_mode_cb.setToolTip(
            "Affects display only. Bots keep using the real names, and the logs never contained one in the first place."
        )
        privacy_form.addRow("Streamer Mode:", self._streamer_mode_cb)
        privacy_form.addRow(
            hint(
                "Replace every wizard name shown in this app with a positional placeholder — "
                "'Character 1', 'Character 2', and so on. A wizard name links your account to "
                "whoever posted the picture, and the launch screen and Players list are the two "
                "most screenshotted parts of this app."
            )
        )

        layout.addLayout(privacy_form)

        # ------------------------------------------------------------------
        # Auto-save section
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Auto-save"))

        autosave_form = QFormLayout()
        autosave_form.setSpacing(4)

        self._autosave_cb = QCheckBox()
        self._autosave_cb.setChecked(sm.get_general("auto_save_enabled", default=True))
        autosave_form.addRow("Auto-save enabled:", self._autosave_cb)
        autosave_form.addRow(
            hint(
                "Keep a recovery copy of a bot with unsaved edits, so a crash does not lose them. "
                "It is a separate file, never the bot's own — nothing here writes over your bot "
                "behind your back, and the copy is deleted once you save."
            )
        )

        self._autosave_interval_sb = QSpinBox()
        self._autosave_interval_sb.setRange(30, 3600)
        self._autosave_interval_sb.setSingleStep(30)
        self._autosave_interval_sb.setSuffix(" sec")
        self._autosave_interval_sb.setValue(sm.get_general("auto_save_interval_seconds", default=300))
        autosave_form.addRow("Auto-save interval:", self._autosave_interval_sb)
        autosave_form.addRow(hint("How often the recovery copy is rewritten. At most this much work is at risk."))

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
        sm.streamer_mode = self._streamer_mode_cb.isChecked()
        sm.set_general("auto_save_enabled", self._autosave_cb.isChecked())
        sm.set_general("auto_save_interval_seconds", self._autosave_interval_sb.value())
