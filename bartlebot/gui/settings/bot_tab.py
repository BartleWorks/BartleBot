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

from bartlebot.gui.settings.quicksell_group import QuicksellSettings
from bartlebot.gui.settings.realm_delay_rows import favorite_realm_row, safety_delay_row
from bartlebot.gui.settings.rows import SettingRow, SettingsForm
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

        # The same row setup's realm page shows, from the same builder.
        self._safety_delay_sb = self.add_row(safety_delay_row(sm)).control

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

        self._favorite_realm_combo = self.add_row(favorite_realm_row(sm)).control

        # ------------------------------------------------------------------
        # Quicksell
        # ------------------------------------------------------------------
        self.add_heading("Quicksell")

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
        # The same rows setup's Quicksell page shows, written through the same functions.
        self._quicksell = QuicksellSettings(sm, extra_rows=[explain])
        self.add_group(self._quicksell.group)

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
        self._quicksell.write(sm)
        sm.show_quicksell_explanation = self._explain_quicksell_cb.isChecked()
        sm.set_general("default_apply_archmastery", self._apply_archmastery_cb.isChecked())
