"""Settings tab: General (window and layout defaults)."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
        # Privacy section
        # ------------------------------------------------------------------
        layout.addWidget(_section_label("Privacy"))

        privacy_form = QFormLayout()
        privacy_form.setSpacing(8)

        self._streamer_mode_cb = QCheckBox()
        self._streamer_mode_cb.setChecked(sm.streamer_mode)
        self._streamer_mode_cb.setToolTip(
            "Replace every wizard name shown in this app with a positional placeholder — "
            "'Character 1', 'Character 2', and so on.\n\n"
            "A wizard name links your account to whoever posted the picture, and the launch "
            "screen and Players list are the two most screenshotted parts of this app. The "
            "placeholder still tells the characters apart, which is all a name is doing here.\n\n"
            "Affects display only. Bots keep using the real names, and the logs never contained "
            "one in the first place."
        )
        privacy_form.addRow("Streamer Mode:", self._streamer_mode_cb)

        layout.addLayout(privacy_form)

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

        # ------------------------------------------------------------------
        # Bot section
        # ------------------------------------------------------------------
        layout.addWidget(_section_label("Bot"))

        bot_form = QFormLayout()
        bot_form.setSpacing(8)

        self._safety_delay_sb = QDoubleSpinBox()
        self._safety_delay_sb.setRange(0.0, 30.0)
        self._safety_delay_sb.setSingleStep(0.5)
        self._safety_delay_sb.setDecimals(1)
        self._safety_delay_sb.setSuffix(" sec")
        self._safety_delay_sb.setValue(sm.default_safety_delay)
        bot_form.addRow("Default Safety Delay:", self._safety_delay_sb)

        self._auto_group_cb = QCheckBox()
        self._auto_group_cb.setChecked(sm.auto_group_players)
        self._auto_group_cb.setToolTip(
            "Put all players into one in-game group at the start of every run, and again "
            "after any player crashes or is closed.\n\n"
            "Group members get health and mana wisps after combat and always take the first "
            "turn in battle. Crashing removes a player from the group, so without this a bot "
            "that relies on going first becomes unreliable after a crash.\n\n"
            "Requires every account to be on the other accounts' friends lists in-game."
        )
        bot_form.addRow("Automatically group players:", self._auto_group_cb)

        self._auto_select_cb = QCheckBox()
        self._auto_select_cb.setChecked(sm.auto_select_character)
        self._auto_select_cb.setToolTip(
            "After a launched client logs in, press Play on the most recently played "
            "character, with no input from you.\n\n"
            "The loading screens before character select are skipped either way — they ask "
            "for a keypress and nothing else — so with this off every launched window still "
            "arrives at its character-select screen, embedded in the game view, waiting for "
            "you to pick. Turning it on is what saves the picking.\n\n"
            "Also used to bring a client back after it crashes mid-run."
        )
        bot_form.addRow("Automatically select a character:", self._auto_select_cb)

        self._relog_interval_sb = QDoubleSpinBox()
        self._relog_interval_sb.setRange(0.0, 24.0)
        self._relog_interval_sb.setSingleStep(0.5)
        self._relog_interval_sb.setDecimals(1)
        self._relog_interval_sb.setSuffix(" hours")
        self._relog_interval_sb.setSpecialValueText("Off")
        self._relog_interval_sb.setValue(sm.relog_interval_hours)
        self._relog_interval_sb.setToolTip(
            "How often a running bot logs every player out and back in.\n\n"
            "A game client gets slower the longer it stays logged in — over one measured "
            "overnight run the same fight went from 23 to 32 seconds — and relogging is the "
            "only thing that clears it. The cost is roughly three minutes per cycle for four "
            "players, against about ten minutes an hour lost to the slowdown by the end of a "
            "long session.\n\n"
            "The run pauses at a node boundary, every player is closed and brought back by the "
            "same path used after a crash, and the bot resumes at its recovery state. It is "
            "skipped entirely if any player could not be logged back in.\n\n"
            "Set to Off to never relog."
        )
        bot_form.addRow("Relog players every:", self._relog_interval_sb)

        self._quick_tp_cb = QCheckBox()
        self._quick_tp_cb.setChecked(sm.disable_quick_friend_teleport)
        self._quick_tp_cb.setToolTip(
            "Turn off the game's quick friend teleport option in every client this app "
            "launches.\n\n"
            "With that option on, double-clicking a name in the Friends List teleports to "
            "the friend. Nodes that work with the Friends List double-click a name to open "
            "the friend's profile, so they teleport instead and quietly do the wrong "
            "thing.\n\n"
            "The game only reads this at startup, so it applies to clients launched from "
            "here — not to one that is already running."
        )
        bot_form.addRow("Disable quick friend teleport:", self._quick_tp_cb)

        self._friend_name_edit = QLineEdit(sm.default_friend_name)
        self._friend_name_edit.setPlaceholderText("Wizard name")
        self._friend_name_edit.setToolTip(
            "The wizard teleport_to_friend ports to when a bot does not name one of its own "
            "players as the target.\n\n"
            "This is yours, not the bot's. Bots are shared, and a character name in a bot file "
            "would send everyone else to a wizard who is not on their friends list — so bot files "
            "do not store one, and every bot on this machine uses the name here.\n\n"
            "That wizard must be online and on every player's friends list."
        )
        bot_form.addRow("Friend teleport target:", self._friend_name_edit)

        # Defaults, not policy: a quicksell node whose matching param is left on
        # "Use app setting" follows these, and one that pins Yes or No does not.
        # Labelled that way so nobody reads the row as a guarantee.
        _quicksell_tip = (
            "Applies to every quicksell node left on 'Use app setting', which is how they come.\n\n"
            "A bot that needs one answer specifically can pin it in the node's parameters, and "
            "that pin wins over this."
        )

        self._sell_pets_cb = QCheckBox()
        self._sell_pets_cb.setChecked(sm.get_general("default_quicksell_sell_pets", default=True))
        self._sell_pets_cb.setToolTip(
            _quicksell_tip + "\n\nOff is the only way to keep a pet: the quicksell window lists pets by "
            "nickname, so no keep-list keyword can match one."
        )
        bot_form.addRow("Quicksell default — Sell Pets:", self._sell_pets_cb)

        self._sell_socket_wrenches_cb = QCheckBox()
        self._sell_socket_wrenches_cb.setChecked(
            sm.get_general("default_quicksell_sell_socket_wrenches", default=False)
        )
        self._sell_socket_wrenches_cb.setToolTip(_quicksell_tip)
        bot_form.addRow("Quicksell default — Sell Socket Wrenches:", self._sell_socket_wrenches_cb)

        self._sell_skeleton_keys_cb = QCheckBox()
        self._sell_skeleton_keys_cb.setChecked(sm.get_general("default_quicksell_sell_skeleton_keys", default=False))
        self._sell_skeleton_keys_cb.setToolTip(_quicksell_tip)
        bot_form.addRow("Quicksell default — Sell Skeleton Keys:", self._sell_skeleton_keys_cb)

        self._apply_archmastery_cb = QCheckBox()
        self._apply_archmastery_cb.setChecked(sm.get_general("default_apply_archmastery", default=False))
        self._apply_archmastery_cb.setToolTip(
            "Whether applying a saved deck also sets the wizard's archmastery school to the one "
            "the deck was saved with.\n\n"
            "Off by default: the school is a persistent character setting that outlives the run, "
            "and most decks do not care what it is. Turn this on if your decks depend on "
            "archmastery pips of a particular school.\n\n"
            "An individual deck can pin its own answer, which wins over this."
        )
        bot_form.addRow("Set archmastery school on deck apply:", self._apply_archmastery_cb)

        layout.addLayout(bot_form)
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
        sm.default_safety_delay = self._safety_delay_sb.value()
        sm.auto_group_players = self._auto_group_cb.isChecked()
        sm.auto_select_character = self._auto_select_cb.isChecked()
        sm.relog_interval_hours = self._relog_interval_sb.value()
        sm.disable_quick_friend_teleport = self._quick_tp_cb.isChecked()
        friend_name = self._friend_name_edit.text().strip()
        if friend_name != sm.default_friend_name:
            sm.default_friend_name = friend_name
            sm.add_recent_friend_name(friend_name)
        sm.set_general("default_quicksell_sell_pets", self._sell_pets_cb.isChecked())
        sm.set_general("default_quicksell_sell_socket_wrenches", self._sell_socket_wrenches_cb.isChecked())
        sm.set_general("default_quicksell_sell_skeleton_keys", self._sell_skeleton_keys_cb.isChecked())
        sm.set_general("default_apply_archmastery", self._apply_archmastery_cb.isChecked())
