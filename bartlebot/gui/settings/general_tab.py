"""Settings tab: General (the game folder, log, window layout, privacy, auto-save).

The Bot section that used to sit at the bottom is :mod:`bartlebot.gui.settings.bot_tab`
now. What is left is the app itself -- how it opens, what it keeps, what it shows --
and none of it is a level of any bot option's resolution.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox, QWidget

from bartlebot.game.game_sizing import (
    BOTCANVAS_DEFAULT_HEIGHT,
    BOTCANVAS_MIN_HEIGHT,
    LEFT_SIDEBAR_MIN_WIDTH,
    RIGHT_SIDEBAR_MIN_WIDTH,
)
from bartlebot.gui import screen_fit
from bartlebot.gui.settings.game_folder_row import apply_game_folder, game_folder_row
from bartlebot.gui.settings.rows import Binding, SettingGroup, SettingRow, SettingsForm
from bartlebot.settings_manager import SettingsManager, default_for
from bartlebot.shared.preferences import PreferenceStore

#: Each of the window size's two boxes, which share one row and one control slot.
_SIZE_BOX_PX = 112


def _default(key: str):
    return default_for("general", key)


class GeneralTab(SettingsForm):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        sm = SettingsManager.instance()

        # ------------------------------------------------------------------
        # Game section
        # ------------------------------------------------------------------
        self.add_heading("Game")

        # A shared preference, not a settings.json key: BartleBuddy launches from it too.
        self._prefs = PreferenceStore()
        self._game_folder_row, self._game_folder = game_folder_row(self._prefs)
        self._game_folder_opened = self._game_folder.value()
        self.add_row(self._game_folder_row)

        # ------------------------------------------------------------------
        # Log section
        # ------------------------------------------------------------------
        self.add_heading("Log")

        self._clear_log_cb = QCheckBox()
        self._clear_log_cb.setChecked(sm.get_general("clear_log_on_run", default=True))
        self.add_row(
            SettingRow(
                "Clear log on Run",
                "Empty the Log tab each time a bot starts.",
                self._clear_log_cb,
                default=_default("clear_log_on_run"),
                details=(
                    "Keeps what is on screen to the run you are watching. The session file in logs/ "
                    "keeps everything either way."
                ),
            )
        )

        self._log_max_lines_sb = QSpinBox()
        self._log_max_lines_sb.setRange(500, 50000)
        self._log_max_lines_sb.setSingleStep(500)
        self._log_max_lines_sb.setSuffix(" lines")
        self._log_max_lines_sb.setValue(sm.get_general("log_max_lines", default=5000))
        self.add_row(
            SettingRow(
                "Max log lines",
                "How much the Log tab holds before the oldest lines drop.",
                self._log_max_lines_sb,
                default=_default("log_max_lines"),
                details="Affects the Log tab only. The session file on disk is never trimmed.",
            )
        )

        # ------------------------------------------------------------------
        # Layout section
        # ------------------------------------------------------------------
        self.add_heading("Layout")

        self._maximize_cb = QCheckBox()
        self._maximize_cb.setChecked(sm.get_general("maximize_on_open", default=True))
        self.add_row(
            SettingRow(
                "Maximize on open",
                "Open the main window maximized.",
                self._maximize_cb,
                default=_default("maximize_on_open"),
            )
        )

        self._width_sb = QSpinBox()
        self._width_sb.setRange(800, 3840)
        self._width_sb.setSuffix(" px")
        self._width_sb.setValue(sm.get_general("window_width", default=1400))

        self._height_sb = QSpinBox()
        self._height_sb.setRange(600, 2160)
        self._height_sb.setSuffix(" px")
        self._height_sb.setValue(sm.get_general("window_height", default=800))

        size = QWidget()
        size_row = QHBoxLayout(size)
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(4)
        for box in (self._width_sb, self._height_sb):
            box.setFixedWidth(_SIZE_BOX_PX)
        size_row.addWidget(self._width_sb)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self._height_sb)
        # Not a level the dialog writes as a pair, but one fact to a reader: a single row,
        # marked and reset together.
        self._size_row = self.add_row(
            SettingRow(
                "Default window size",
                "The size the main window opens at when not maximized.",
                size,
                default=(_default("window_width"), _default("window_height")),
                details="Resizing the window afterwards does not change this.",
                binding=Binding(
                    read=lambda: (self._width_sb.value(), self._height_sb.value()),
                    write=self._set_window_size,
                    signals=(self._width_sb.valueChanged, self._height_sb.valueChanged),
                    describe=lambda v: f"{v[0]} × {v[1]} px",
                ),
            )
        )

        # Each pane's range runs to a 4K screen's size: setup fits the panes to the screen,
        # and a range below what it wrote would clamp it on the next Save.
        self._left_sb = QSpinBox()
        self._left_sb.setRange(screen_fit.left_sidebar_floor(), 3840)
        self._left_sb.setSuffix(" px")
        self._left_sb.setValue(sm.get_general("left_sidebar_width", default=LEFT_SIDEBAR_MIN_WIDTH))
        self.add_row(
            SettingRow(
                "Default left sidebar width",
                "The bot's own configuration: Options and Combat, or Options and Properties.",
                self._left_sb,
                default=_default("left_sidebar_width"),
            )
        )

        self._right_sb = QSpinBox()
        self._right_sb.setRange(screen_fit.right_sidebar_floor(), 3840)
        self._right_sb.setSuffix(" px")
        self._right_sb.setValue(sm.get_general("right_sidebar_width", default=RIGHT_SIDEBAR_MIN_WIDTH))
        self.add_row(
            SettingRow(
                "Default right sidebar width",
                "Players, Drops and Log.",
                self._right_sb,
                default=_default("right_sidebar_width"),
            )
        )

        self._bottom_sb = QSpinBox()
        self._bottom_sb.setRange(BOTCANVAS_MIN_HEIGHT, 2160)
        self._bottom_sb.setSuffix(" px")
        self._bottom_sb.setValue(sm.get_general("bottom_section_height", default=BOTCANVAS_DEFAULT_HEIGHT))
        self.add_row(
            SettingRow(
                "Default bottom section height",
                "The bot canvas under the game view.",
                self._bottom_sb,
                default=_default("bottom_section_height"),
                details=(
                    "Setup fits it and both sidebars to your screen, leaving the game view room for "
                    "your launches, until you change them here. Every section is draggable once open."
                ),
            )
        )

        # ------------------------------------------------------------------
        # Privacy section
        # ------------------------------------------------------------------
        self.add_heading("Privacy")

        self._streamer_mode_cb = QCheckBox()
        self._streamer_mode_cb.setChecked(sm.streamer_mode)
        self.add_row(
            SettingRow(
                "Streamer Mode",
                "Show 'Character 1', 'Character 2' and so on in place of wizard names.",
                self._streamer_mode_cb,
                default=_default("streamer_mode"),
                details=(
                    "A wizard name links your account to whoever posted the picture, and the launch "
                    "screen and Players list are the two most screenshotted parts of this app. Display "
                    "only: bots keep using the real names, and the logs never contained one in the "
                    "first place."
                ),
            )
        )

        # ------------------------------------------------------------------
        # Auto-save section
        # ------------------------------------------------------------------
        self.add_heading("Auto-save")

        self._autosave_cb = QCheckBox()
        self._autosave_cb.setChecked(sm.get_general("auto_save_enabled", default=True))
        autosave = SettingRow(
            "Enable auto-save",
            "Keep a recovery copy of a bot with unsaved edits.",
            self._autosave_cb,
            default=_default("auto_save_enabled"),
            details=(
                "So a crash does not lose them. It is a separate file, never the bot's own: nothing "
                "here writes over your bot behind your back, and the copy is deleted once you save."
            ),
        )

        self._autosave_interval_sb = QSpinBox()
        self._autosave_interval_sb.setRange(30, 3600)
        self._autosave_interval_sb.setSingleStep(30)
        self._autosave_interval_sb.setSuffix(" sec")
        self._autosave_interval_sb.setValue(sm.get_general("auto_save_interval_seconds", default=300))
        interval = SettingRow(
            "Auto-save interval",
            "How often the recovery copy is rewritten.",
            self._autosave_interval_sb,
            default=_default("auto_save_interval_seconds"),
            details="At most this much work is at risk in a crash.",
        )
        self.add_group(SettingGroup("autosave", autosave, [interval]))

        self.finish()

        self._maximize_cb.toggled.connect(self._on_maximize_toggled)
        self._on_maximize_toggled(self._maximize_cb.isChecked())

    def _set_window_size(self, size) -> None:
        width, height = size
        self._width_sb.setValue(width)
        self._height_sb.setValue(height)

    def _on_maximize_toggled(self, checked: bool) -> None:
        # Not a group: Maximize being *on* is what makes the size moot, the reverse of a
        # parent setting whose rows only count while it is on.
        self._size_row.set_available(not checked, "Maximize on open is on, so this has no effect.")

    @property
    def game_folder_row(self) -> SettingRow:
        return self._game_folder_row

    def apply(self) -> None:
        if apply_game_folder(self._prefs, self._game_folder_opened, self._game_folder.value()):
            self._game_folder_opened = self._game_folder.value()
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
