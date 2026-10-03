"""Settings tab: Bot (how runs behave, and the app-setting level of four).

Split out of ``GeneralTab``, which had grown a twelve-row Bot section under five
unrelated ones. The split is not only length: every row here is the **app
setting** level of the four-level resolution in :mod:`bartlebot.bot_options`, so
a value set for one bot in its Options tab wins over anything on this tab, and so
does an author who declared an answer their dungeon needs. That is one idea, and
it now has one page to say it on -- the note at the top of the tab, rather than
the same disclaimer repeated into eight tooltips.

The two rows with no option behind them -- automatic grouping and the relog
interval -- are here rather than in General because they describe what a *run*
does, which is what a reader opens this tab to find. That is also the line that
sent character selection and quick friend teleport to the Game tab: both are done
to a client while it is starting, and happen whether or not a bot is ever run.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot_options import SAFETY_DELAY_DESCRIPTION
from bartlebot.gui.settings.rows import hint
from bartlebot.gui.widgets.realm_combo import RealmCombo
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.settings_manager import SettingsManager


class BotTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        sm = SettingsManager.instance()

        layout.addWidget(
            hint(
                "These are starting answers, not rules. A bot whose author needed a particular "
                "answer overrides them, and a value you set for one bot in its Options tab "
                "overrides both."
            )
        )

        # ------------------------------------------------------------------
        # Timing
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Timing"))

        timing_form = QFormLayout()
        timing_form.setSpacing(4)

        self._safety_delay_sb = QDoubleSpinBox()
        # The floor is the bot option's, and for the same reason: a negative delay
        # fails silently, because asyncio.sleep returns at once and every window
        # polled *for* that many seconds expires on its first check.
        self._safety_delay_sb.setRange(0.0, 30.0)
        self._safety_delay_sb.setSingleStep(0.5)
        self._safety_delay_sb.setDecimals(1)
        self._safety_delay_sb.setSuffix(" sec")
        self._safety_delay_sb.setValue(sm.default_safety_delay)
        timing_form.addRow("Default Safety Delay:", self._safety_delay_sb)
        # Word-for-word the Safety Delay bot option's own description, which every
        # NodeSpec declaring it carries. They are the same value now, and one value
        # described two ways is how the two descriptions come to disagree; the guard
        # is test_the_safety_delay_setting_and_option_read_the_same.
        timing_form.addRow(hint(SAFETY_DELAY_DESCRIPTION))

        self._relog_interval_sb = QDoubleSpinBox()
        self._relog_interval_sb.setRange(0.0, 24.0)
        self._relog_interval_sb.setSingleStep(0.5)
        self._relog_interval_sb.setDecimals(1)
        self._relog_interval_sb.setSuffix(" hours")
        self._relog_interval_sb.setSpecialValueText("Off")
        self._relog_interval_sb.setValue(sm.relog_interval_hours)
        self._relog_interval_sb.setToolTip(
            "The run pauses at a node boundary, every player is closed and brought back by the "
            "same path used after a crash, and the bot resumes at its recovery state. It is "
            "skipped entirely if any player could not be logged back in."
        )
        timing_form.addRow("Relog players every:", self._relog_interval_sb)
        timing_form.addRow(
            hint(
                "How often a running bot logs every player out and back in. A game client gets "
                "slower the longer it stays logged in — over one measured overnight run the same "
                "fight went from 23 to 32 seconds — and relogging is the only thing that clears "
                "it. Costs about three minutes per cycle for four players. Off never relogs."
            )
        )

        layout.addLayout(timing_form)

        # ------------------------------------------------------------------
        # Players
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Players"))

        players_form = QFormLayout()
        players_form.setSpacing(4)

        self._auto_group_cb = QCheckBox()
        self._auto_group_cb.setChecked(sm.auto_group_players)
        players_form.addRow("Automatically group players:", self._auto_group_cb)
        players_form.addRow(
            hint(
                "Put every player into one in-game group at the start of a run, and again after "
                "one crashes or is closed. Group members get wisps after combat and take the "
                "first turn in battle, and crashing drops a player out of the group. Requires "
                "every account to be on the others' friends lists in-game."
            )
        )

        self._friend_name_edit = QLineEdit(sm.default_friend_name)
        self._friend_name_edit.setPlaceholderText("Wizard name")
        players_form.addRow("Friend teleport target:", self._friend_name_edit)
        players_form.addRow(
            hint(
                "The wizard teleport_to_friend ports to when a bot names none of its own players "
                "as the target. This one is yours: a name in a shared bot file would send "
                "everyone else to a wizard who is not on their friends list, so no bot stores "
                "one. That wizard must be online and on every player's friends list."
            )
        )

        layout.addLayout(players_form)

        # ------------------------------------------------------------------
        # Realms
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Realms"))

        realms_form = QFormLayout()
        realms_form.setSpacing(4)

        self._favorite_realm_combo = RealmCombo()
        self._favorite_realm_combo.set_value(sm.favorite_realm)
        realms_form.addRow("Favorite realm:", self._favorite_realm_combo)
        realms_form.addRow(
            hint(
                "The realm Change Realm switches every player to. Yours rather than the "
                "bot's: a realm in a shared bot file would send everyone who runs it to the same "
                "place. With one set, every launched player also has the game's Remember Realm "
                "option turned on, so a relogged player comes back to the realm it left."
            )
        )

        layout.addLayout(realms_form)

        # ------------------------------------------------------------------
        # Quicksell
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Quicksell"))

        quicksell_form = QFormLayout()
        quicksell_form.setSpacing(4)

        self._quicksell_enabled_cb = QCheckBox()
        self._quicksell_enabled_cb.setChecked(sm.get_general("default_quicksell_enabled", default=True))
        quicksell_form.addRow("Enable Quicksell:", self._quicksell_enabled_cb)
        quicksell_form.addRow(
            hint(
                "Whether bots sell drops at all. Off means backpacks fill, further drops go to "
                "your shared bank, and once that is full too only Crowns items are kept."
            )
        )

        self._sell_pets_cb = QCheckBox()
        self._sell_pets_cb.setChecked(sm.get_general("default_quicksell_sell_pets", default=True))
        quicksell_form.addRow("Sell Pets:", self._sell_pets_cb)
        quicksell_form.addRow(
            hint(
                "Off is the only way to keep a pet: the quicksell window lists pets by nickname, "
                "so no keep-list keyword can match one."
            )
        )

        self._sell_socket_wrenches_cb = QCheckBox()
        self._sell_socket_wrenches_cb.setChecked(
            sm.get_general("default_quicksell_sell_socket_wrenches", default=False)
        )
        quicksell_form.addRow("Sell Socket Wrenches:", self._sell_socket_wrenches_cb)
        quicksell_form.addRow(hint("Wrenches unlock jewel sockets on gear, and are not otherwise farmable."))

        self._sell_skeleton_keys_cb = QCheckBox()
        self._sell_skeleton_keys_cb.setChecked(sm.get_general("default_quicksell_sell_skeleton_keys", default=False))
        quicksell_form.addRow("Sell Skeleton Keys:", self._sell_skeleton_keys_cb)
        quicksell_form.addRow(hint("Keys open the locked skeleton-key dungeons, and are not otherwise farmable."))

        layout.addLayout(quicksell_form)

        # ------------------------------------------------------------------
        # Decks
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("Decks"))

        decks_form = QFormLayout()
        decks_form.setSpacing(4)

        self._apply_archmastery_cb = QCheckBox()
        self._apply_archmastery_cb.setChecked(sm.get_general("default_apply_archmastery", default=False))
        decks_form.addRow("Set archmastery school on deck apply:", self._apply_archmastery_cb)
        decks_form.addRow(
            hint(
                "Whether applying a saved deck also sets the wizard's archmastery school to the "
                "one the deck was saved with. Off by default: the school outlives the run, and "
                "most decks do not care what it is. An individual deck can pin its own answer, "
                "which wins over this."
            )
        )

        layout.addLayout(decks_form)
        layout.addStretch()

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.default_safety_delay = self._safety_delay_sb.value()
        sm.relog_interval_hours = self._relog_interval_sb.value()
        sm.auto_group_players = self._auto_group_cb.isChecked()
        friend_name = self._friend_name_edit.text().strip()
        if friend_name != sm.default_friend_name:
            sm.default_friend_name = friend_name
            sm.add_recent_friend_name(friend_name)
        sm.favorite_realm = self._favorite_realm_combo.get_value()
        sm.set_general("default_quicksell_enabled", self._quicksell_enabled_cb.isChecked())
        sm.set_general("default_quicksell_sell_pets", self._sell_pets_cb.isChecked())
        sm.set_general("default_quicksell_sell_socket_wrenches", self._sell_socket_wrenches_cb.isChecked())
        sm.set_general("default_quicksell_sell_skeleton_keys", self._sell_skeleton_keys_cb.isChecked())
        sm.set_general("default_apply_archmastery", self._apply_archmastery_cb.isChecked())
