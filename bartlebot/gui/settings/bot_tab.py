"""Settings tab: Bot (how runs behave, and the app-setting level of four).

Split out of ``GeneralTab``, which had grown a twelve-row Bot section under five
unrelated ones. The split is not only length: every row here is the **app
setting** level of the four-level resolution in :mod:`bartlebot.options.bot_options`, so
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

from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QLineEdit

from bartlebot.gui.settings.rows import SettingGroup, SettingRow, SettingsForm
from bartlebot.gui.widgets.list_text import format_list_text, parse_list_text
from bartlebot.gui.widgets.realm_combo import RealmCombo
from bartlebot.nodes.utility import quicksell
from bartlebot.options.bot_options import SAFETY_DELAY_DESCRIPTION
from bartlebot.settings_manager import SettingsManager, default_for

_INTRO = (
    "Starting answers, not rules. A bot's author can override them, and your value in a bot's "
    "Options tab overrides both."
)


def _default(key: str):
    return default_for("general", key)


class BotTab(SettingsForm):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, intro=_INTRO)
        sm = SettingsManager.instance()

        # ------------------------------------------------------------------
        # Timing
        # ------------------------------------------------------------------
        self.add_heading("Timing")

        self._safety_delay_sb = QDoubleSpinBox()
        self._safety_delay_sb.setRange(0.0, 30.0)
        self._safety_delay_sb.setSingleStep(0.5)
        self._safety_delay_sb.setDecimals(1)
        self._safety_delay_sb.setSuffix(" sec")
        self._safety_delay_sb.setValue(sm.default_safety_delay)
        self.add_row(
            SettingRow(
                "Default Safety Delay",
                "How long the game server gets before an action counts as failed.",
                self._safety_delay_sb,
                default=_default("default_safety_delay"),
                # The option's own text: one value, described once (see settings/CLAUDE.md).
                details=SAFETY_DELAY_DESCRIPTION,
            )
        )

        self._relog_interval_sb = QDoubleSpinBox()
        self._relog_interval_sb.setRange(0.0, 24.0)
        self._relog_interval_sb.setSingleStep(0.5)
        self._relog_interval_sb.setDecimals(1)
        self._relog_interval_sb.setSuffix(" hours")
        self._relog_interval_sb.setSpecialValueText("Off")
        self._relog_interval_sb.setValue(sm.relog_interval_hours)
        self.add_row(
            SettingRow(
                "Relog interval",
                "Log every player out and back in to clear client slowdown.",
                self._relog_interval_sb,
                default=_default("relog_interval_hours"),
                details=(
                    "A game client gets slower the longer it stays logged in — over one measured "
                    "overnight run the same fight went from 23 to 32 seconds — and relogging is the "
                    "only thing that clears it. Costs about three minutes per cycle for four players. "
                    "The run pauses at a node boundary, every player is closed and brought back by "
                    "the same path used after a crash, and the bot resumes at its recovery state. It "
                    "is skipped entirely if any player could not be logged back in. Off never relogs."
                ),
            )
        )

        # ------------------------------------------------------------------
        # Players
        # ------------------------------------------------------------------
        self.add_heading("Players")

        self._auto_group_cb = QCheckBox()
        self._auto_group_cb.setChecked(sm.auto_group_players)
        self.add_row(
            SettingRow(
                "Automatically group players",
                "Put every player in one in-game group when a run starts.",
                self._auto_group_cb,
                default=_default("auto_group_players"),
                details=(
                    "And again after one crashes or is closed. Group members get wisps after combat "
                    "and take the first turn in battle, and crashing drops a player out of the group. "
                    "Requires every account to be on the others' friends lists in-game."
                ),
            )
        )

        self._friend_name_edit = QLineEdit(sm.default_friend_name)
        self._friend_name_edit.setPlaceholderText("Wizard name")
        self.add_row(
            SettingRow(
                "Friend teleport target",
                "Who a bot ports to when it names none of its own players.",
                self._friend_name_edit,
                default=_default("default_friend_name"),
                details=(
                    "The wizard teleport_to_friend ports to. This one is yours: a name in a shared bot "
                    "file would send everyone else to a wizard who is not on their friends list, so no "
                    "bot stores one. That wizard must be online and on every player's friends list."
                ),
            )
        )

        self._refill_potions_cb = QCheckBox()
        self._refill_potions_cb.setChecked(sm.get_general("default_refill_potions", default=True))
        self.add_row(
            SettingRow(
                "Refill potions",
                "Buy potions from Hilda Brewer when a player runs out.",
                self._refill_potions_cb,
                default=_default("default_refill_potions"),
                details=(
                    "Players go to the Commons, buy, then recall to where they were. Buying costs "
                    "gold. Off, they keep farming without potions."
                ),
            )
        )

        # ------------------------------------------------------------------
        # Realms
        # ------------------------------------------------------------------
        self.add_heading("Realms")

        self._favorite_realm_combo = RealmCombo()
        self._favorite_realm_combo.set_value(sm.favorite_realm)
        self.add_row(
            SettingRow(
                "Favorite realm",
                "The realm Change Realm switches every player to.",
                self._favorite_realm_combo,
                default=_default("favorite_realm"),
                details=(
                    "Yours rather than the bot's: a realm in a shared bot file would send everyone who "
                    "runs it to the same place. With one set, every launched player also has the game's "
                    "Remember Realm option turned on, so a relogged player comes back to the realm it "
                    "left."
                ),
            )
        )

        # ------------------------------------------------------------------
        # Quicksell
        # ------------------------------------------------------------------
        self.add_heading("Quicksell")

        self._quicksell_enabled_cb = QCheckBox()
        self._quicksell_enabled_cb.setChecked(sm.get_general("default_quicksell_enabled", default=True))
        enable = SettingRow(
            "Enable Quicksell",
            "Sell drops so backpacks never fill.",
            self._quicksell_enabled_cb,
            default=_default("default_quicksell_enabled"),
            details=(
                "Off means backpacks fill, further drops go to your shared bank, and once that is full "
                "too only Crowns items are kept."
            ),
        )

        self._sell_pets_cb = QCheckBox()
        self._sell_pets_cb.setChecked(sm.get_general("default_quicksell_sell_pets", default=True))
        sell_pets = SettingRow(
            "Sell Pets",
            "Off is the only way to keep a pet.",
            self._sell_pets_cb,
            default=_default("default_quicksell_sell_pets"),
            details="The quicksell window lists pets by nickname, so no keep-list keyword can match one.",
        )

        self._sell_socket_wrenches_cb = QCheckBox()
        self._sell_socket_wrenches_cb.setChecked(
            sm.get_general("default_quicksell_sell_socket_wrenches", default=False)
        )
        sell_wrenches = SettingRow(
            "Sell Socket Wrenches",
            "Wrenches unlock jewel sockets on gear, and are not otherwise farmable.",
            self._sell_socket_wrenches_cb,
            default=_default("default_quicksell_sell_socket_wrenches"),
        )

        self._sell_skeleton_keys_cb = QCheckBox()
        self._sell_skeleton_keys_cb.setChecked(sm.get_general("default_quicksell_sell_skeleton_keys", default=False))
        sell_keys = SettingRow(
            "Sell Skeleton Keys",
            "Keys open the skeleton-key dungeons, and are not otherwise farmable.",
            self._sell_skeleton_keys_cb,
            default=_default("default_quicksell_sell_skeleton_keys"),
        )

        self._explain_quicksell_cb = QCheckBox()
        self._explain_quicksell_cb.setChecked(sm.show_quicksell_explanation)
        explain = SettingRow(
            "Show Quicksell explanation before each run",
            "Explain what quicksell sells before a run starts.",
            self._explain_quicksell_cb,
            default=_default("show_quicksell_explanation"),
            details=(
                "The safety check explains it before each run of a bot that has quicksell. Off skips the "
                "explanation, but a bot that sells something your settings here keep is still flagged, "
                "once for each bot."
            ),
        )

        # Tall controls go last in their card.
        self._keep_list_edit = QLineEdit(
            format_list_text([str(k) for k in sm.get_general(quicksell.USER_KEEP_LIST_KEY, default=[]) or []])
        )
        self._keep_list_edit.setPlaceholderText("e.g. Amber, Pearl")
        keep = SettingRow(
            "Always keep",
            "Items every bot keeps, on top of its own keep list.",
            self._keep_list_edit,
            default=format_list_text(default_for("general", quicksell.USER_KEEP_LIST_KEY) or []),
            details=(
                "Separate them with commas; any item whose name contains one is not sold. Pets are listed "
                "by nickname, so this cannot keep one."
            ),
            wide=True,
        )
        self.add_group(SettingGroup("quicksell", enable, [sell_pets, sell_wrenches, sell_keys, explain, keep]))

        # ------------------------------------------------------------------
        # Decks
        # ------------------------------------------------------------------
        self.add_heading("Decks")

        self._apply_archmastery_cb = QCheckBox()
        self._apply_archmastery_cb.setChecked(sm.get_general("default_apply_archmastery", default=False))
        self.add_row(
            SettingRow(
                "Set archmastery school on deck apply",
                "Applying a saved deck also sets its archmastery school.",
                self._apply_archmastery_cb,
                default=_default("default_apply_archmastery"),
                details=(
                    "Off by default: the school outlives the run, and most decks do not care what it is. "
                    "An individual deck can pin its own answer, which wins over this."
                ),
            )
        )

        self.finish()

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
        sm.set_general("default_refill_potions", self._refill_potions_cb.isChecked())
        sm.set_general("default_quicksell_enabled", self._quicksell_enabled_cb.isChecked())
        sm.set_general("default_quicksell_sell_pets", self._sell_pets_cb.isChecked())
        sm.set_general("default_quicksell_sell_socket_wrenches", self._sell_socket_wrenches_cb.isChecked())
        sm.set_general("default_quicksell_sell_skeleton_keys", self._sell_skeleton_keys_cb.isChecked())
        sm.set_general(quicksell.USER_KEEP_LIST_KEY, parse_list_text(self._keep_list_edit.text()))
        sm.show_quicksell_explanation = self._explain_quicksell_cb.isChecked()
        sm.set_general("default_apply_archmastery", self._apply_archmastery_cb.isChecked())
