"""Settings tab: Combat, the Solo and Team Default Duel Strategies (epic #201, Stage 5), and
whether the session log records each turn's decisions.

A fight with nothing tailored to it -- no Bot Combat Strategy, or one that leaves the
fight unassigned and has no default -- runs one of two defaults: Solo with one client
connected, Team with two to four. This tab is where the user chooses them, and the
banner on any bot's Combat tab leads here through Change defaults….

TWO CARDS, NOT TWO ROWS
-----------------------

Each default is core's ``DuelCard`` (``bartleby.qt.combat.duel_card``, which BartleBuddy's
Settings › Combat is built from too): the duel in use, and two verbs.

**Change…** opens ``CombatStrategyDialog`` in Pick mode on the library alone, with no Bot
Combat Strategy, since a default is for every bot. **Use recommended** goes back to the duel
that ships, and picking that duel by name is the same.

THE APPLY RULE
--------------

The dialog's own (``bartlebot/gui/settings/CLAUDE.md``): a choice made here is pending
until OK, and Cancel drops it. ``apply`` writes only a card whose choice changed, through
``PreferenceStore`` -- one key each, re-read and replaced, so BartleBuddy's write to the
other key survives -- and "recommended" is written as no key, so a later fix to the
shipped duel reaches everyone who kept it (``combat_defaults.set_default_choice``).

A write that fails is logged, not raised, and the card keeps showing the choice: the
next OK tries again.

THE LOG ROW
-----------

*Record combat decisions* lets wizsprinter's turn trace into the session file: the
hand each round, every line tried and why it was passed over, every fuse, and the cast.
A ``settings.json`` key rather than a preference, since it is this app's log, and
applied on OK through ``logging_setup.set_turn_trace``, so it needs no restart.
"""

from __future__ import annotations

import logging

from bartleby.qt.app.logging_setup import set_turn_trace
from bartleby.qt.combat.duel_card import DuelCard, DuelCardText, Unset
from bartleby.qt.settings.rows import SettingRow, SettingsForm
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.shared.combat_defaults import SHIPPED_DUEL_IDS, TeamSize, chosen_default, set_default_choice
from bartleby.shared.preferences import PreferenceStore
from PyQt6.QtWidgets import QCheckBox, QWidget

from bartlebot.combat.bot_strategy_owner import load_working_set
from bartlebot.combat.combat_resolution import default_choice_notes
from bartlebot.gui.combat_strategies.dialog import CombatStrategyDialog, DialogMode
from bartlebot.settings_manager import SettingsManager, default_for

logger = logging.getLogger(__name__)

INTRO = "Used for any fight that the bot's Combat tab doesn't give a Duel Strategy."
HEADING = "Default Duel Strategies"

USE_RECOMMENDED = "Use recommended"

#: Each card's title and the line under it.
TITLES = {TeamSize.SOLO: "Solo", TeamSize.TEAM: "Team"}
SUMMARIES = {
    TeamSize.SOLO: "With one game client connected.",
    TeamSize.TEAM: "With two to four game clients connected.",
}
RECOMMENDED_NOTE = "The recommended Duel Strategy, included with BartleBot."
#: What a card says when not even the shipped duel can be read.
UNREADABLE = "The recommended Duel Strategy couldn't be read, so these fights use a basic built-in one."

LOG_HEADING = "Log"
RECORD_TITLE = "Record combat decisions"
RECORD_SUMMARY = "Write each turn's reasoning to the session log."
RECORD_DETAILS = (
    "Recording adds a few kilobytes per round for each client and names players by slot, never by "
    "wizard name. Turn recording on to look into a fight that goes wrong."
)


class DefaultDuelCard(DuelCard):
    """One default, Solo or Team: core's card, with BartleBot's words, library and dialog."""

    def __init__(self, size: TeamSize, chosen: str | None, parent: QWidget | None = None) -> None:
        text = DuelCardText(
            title=TITLES[size],
            summary=SUMMARIES[size],
            change_tooltip=f"Pick the Duel Strategy for {TITLES[size].lower()} fights",
            unset=USE_RECOMMENDED,
            unset_tooltip="Use the Duel Strategy that comes with BartleBot",
            unreadable=UNREADABLE,
        )
        super().__init__(
            size,
            text,
            chosen,
            library=lambda: load_working_set(),
            unset=lambda _ws: Unset(SHIPPED_DUEL_IDS[size], RECOMMENDED_NOTE),
            notes=lambda _ws, choice: default_choice_notes(size, choice),
            # BartleBot's dialog has a mode of its own beside core's two, whose values are core's.
            window=lambda ws, mode, **how: CombatStrategyDialog(ws, mode=DialogMode(mode), **how),
            same_as_unset=SHIPPED_DUEL_IDS[size],
            parent=parent,
        )

    @property
    def recommended_button(self) -> ActionButton:
        return self.unset_button


class CombatDefaultsTab(SettingsForm):
    """The Solo and Team Default Duel Strategies, chosen here and written on OK."""

    def __init__(self, parent: QWidget | None = None, *, prefs: PreferenceStore | None = None) -> None:
        super().__init__(parent, intro=INTRO)
        self._prefs = prefs if prefs is not None else PreferenceStore()
        self.add_heading(HEADING)
        self._cards: dict[TeamSize, DefaultDuelCard] = {}
        # What the file held when the tab opened, so OK writes only what changed here and
        # leaves a key the other product wrote meanwhile alone.
        self._stored: dict[TeamSize, str | None] = {}
        for size in (TeamSize.SOLO, TeamSize.TEAM):
            stored = chosen_default(self._prefs, size)
            self._stored[size] = stored
            card = DefaultDuelCard(size, stored, parent=self)
            self._cards[size] = card
            self.add_card(card)

        self.add_heading(LOG_HEADING)
        self._record_cb = QCheckBox()
        self._record_cb.setChecked(SettingsManager.instance().record_combat_decisions)
        self.add_row(
            SettingRow(
                RECORD_TITLE,
                RECORD_SUMMARY,
                self._record_cb,
                default=default_for("general", "record_combat_decisions"),
                details=RECORD_DETAILS,
            )
        )
        self.finish()

    def card(self, size: TeamSize) -> DefaultDuelCard:
        return self._cards[size]

    @property
    def record_check_box(self) -> QCheckBox:
        return self._record_cb

    def apply(self) -> None:
        """Write each default whose choice changed here, logging a failed write, and the
        trace's setting, which takes effect at once."""
        sm = SettingsManager.instance()
        sm.record_combat_decisions = self._record_cb.isChecked()
        set_turn_trace(sm.record_combat_decisions)
        for size, card in self._cards.items():
            if card.choice == self._stored[size]:
                continue
            if set_default_choice(self._prefs, size, card.choice):
                self._stored[size] = card.choice
            else:
                logger.warning("Could not save the %s default duel strategy; it is kept for the next OK", size.value)
