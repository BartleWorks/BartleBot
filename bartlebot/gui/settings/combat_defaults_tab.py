"""Settings tab: Combat, the Solo and Team Default Duel Strategies (epic #201, Stage 5), and
whether the session log records each turn's decisions.

A fight with nothing tailored to it -- no Bot Combat Strategy, or one that leaves the
fight unassigned and has no default -- runs one of two defaults: Solo with one client
connected, Team with two to four. This tab is where the user chooses them, and the
banner on any bot's Combat tab leads here through Change defaults….

TWO CARDS, NOT TWO ROWS
-----------------------

Each default is a card: a heading line, the duel in use drawn as the same
``StrategyCard`` the combat dialog lists, and two verbs. A ``SettingRow``'s control slot
is 210px and holds a field; a duel's summary is a name, a source pill and a line of
facets, which is what the user is choosing between, so it is drawn whole.

**Change…** opens ``CombatStrategyDialog`` in Pick mode on the library's duels, filtered
to the team size the card answers for (one, or two) as a chip the user can remove.
**Use recommended** goes back to the duel that ships.

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
from dataclasses import replace

from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.shared.combat_defaults import SHIPPED_DUEL_IDS, TeamSize, chosen_default, set_default_choice
from bartleby.shared.preferences import PreferenceStore
from bartleby.shared.store_files import held_listings
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from bartlebot.combat.combat_resolution import SIZE_RANGE, default_choice_notes
from bartlebot.combat.strategy_store import StrategyKind
from bartlebot.combat.strategy_summary import summarize_duel
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.gui.art_signal import follow_art
from bartlebot.gui.combat_strategies.dialog import CombatStrategyDialog, DialogMode
from bartlebot.gui.combat_strategies.dsl_checks import DeferredDslChecks
from bartlebot.gui.settings.rows import SettingCard, SettingRow, SettingsForm
from bartlebot.gui.widgets.cards.strategy_card import StrategyCard
from bartlebot.logging_setup import set_turn_trace
from bartlebot.settings_manager import SettingsManager, default_for

logger = logging.getLogger(__name__)

INTRO = "Used for any fight that the bot's Combat tab doesn't give a Duel Strategy."
HEADING = "Default Duel Strategies"

CHANGE = "Change…"
USE_RECOMMENDED = "Use recommended"

#: Each card's title and the line under it.
TITLES = {TeamSize.SOLO: "Solo", TeamSize.TEAM: "Team"}
SUMMARIES = {
    TeamSize.SOLO: "With one game client connected.",
    TeamSize.TEAM: "With two to four game clients connected.",
}
RECOMMENDED_NOTE = "The recommended Duel Strategy, included with BartleBot."
CHOSEN_NOTE = "Chosen by you."
#: What a card says when not even the shipped duel can be read.
UNREADABLE = "The recommended Duel Strategy couldn't be read, so these fights use a basic built-in one."

LOG_HEADING = "Log"
RECORD_TITLE = "Record combat decisions"
RECORD_SUMMARY = "Write each turn's reasoning to the session log."
RECORD_DETAILS = (
    "Recording adds a few kilobytes per round for each client and names players by slot, never by "
    "wizard name. Turn recording on to look into a fight that goes wrong."
)

#: The team size Change… filters to: the smallest the card answers for.
PICK_TEAM_SIZE = {size: sizes[0] for size, sizes in SIZE_RANGE.items()}


class DefaultDuelCard(SettingCard):
    """One default, Solo or Team: the duel in use, whose it is, and the two ways to change it."""

    #: The choice changed: the duel id, or None for the recommended one.
    changed = pyqtSignal(object)

    def __init__(self, size: TeamSize, chosen: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._chosen = None if chosen == SHIPPED_DUEL_IDS[size] else chosen
        self._strategy_card: StrategyCard | None = None
        # The card's health is drawn from the DSL checks already known, so opening
        # Settings parses nothing; the rest are checked off the GUI thread.
        self._checks = DeferredDslChecks(self, lambda _keys: self._redraw())

        body = QWidget(self)
        column = QVBoxLayout(body)
        column.setContentsMargins(12, 8, 12, 10)
        column.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        self._title = ThemedTextLabel(TITLES[size], color="text", css=f"font-size: {THEME.font_body_px}px;")
        self._summary = ThemedTextLabel(SUMMARIES[size], css=f"font-size: {THEME.font_subtext_px}px;")
        text.addWidget(self._title)
        text.addWidget(self._summary)
        head.addLayout(text, 1)
        self._change_btn = ActionButton("search", CHANGE, style=ActionStyle.SECONDARY, fill=False)
        self._change_btn.setToolTip(f"Pick the Duel Strategy for {TITLES[size].lower()} fights")
        self._change_btn.clicked.connect(self._on_change)
        head.addWidget(self._change_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._recommended_btn = ActionButton("revert", USE_RECOMMENDED, fill=False)
        self._recommended_btn.setToolTip("Use the Duel Strategy that comes with BartleBot")
        self._recommended_btn.clicked.connect(lambda: self.set_choice(None))
        head.addWidget(self._recommended_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(head)

        # Rebuilt per choice: a StrategyCard is drawn from one summary and has no setter.
        self._card_slot = QVBoxLayout()
        self._card_slot.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self._card_slot)

        self._note = ThemedTextLabel("", css=f"font-size: {THEME.font_subtext_px}px;")
        self._note.setWordWrap(True)
        column.addWidget(self._note)

        self.layout().addWidget(body)
        self._redraw()
        # The card draws its schools as medallions or as words once, when it is made, and
        # Settings is open behind the extraction its own General tab starts.
        follow_art(self, self._redraw)

    # -- reading --------------------------------------------------------

    @property
    def size(self) -> TeamSize:
        return self._size

    @property
    def choice(self) -> str | None:
        """The duel id chosen, or None for the recommended one. Pending until the dialog's OK."""
        return self._chosen

    @property
    def change_button(self) -> ActionButton:
        return self._change_btn

    @property
    def recommended_button(self) -> ActionButton:
        return self._recommended_btn

    @property
    def strategy_card(self) -> StrategyCard | None:
        """The duel in use, as the combat dialog draws it, or None when none can be read."""
        return self._strategy_card

    @property
    def note_text(self) -> str:
        return self._note.text()

    # -- writing --------------------------------------------------------

    def set_choice(self, duel_id: str | None) -> None:
        """Choose *duel_id*, or None for the recommended one; the shipped duel's id is None too."""
        duel_id = None if duel_id == SHIPPED_DUEL_IDS[self._size] else duel_id
        if duel_id == self._chosen:
            return
        self._chosen = duel_id
        self._redraw()
        self.changed.emit(duel_id)

    def _on_change(self) -> None:
        """Pick a duel from the library, filtered to the team this card answers for.

        Select-only, and given a working set of the library alone: no Bot Combat Strategy,
        since a default is for every bot. Opened from a click, a synchronous slot, as
        ``exec()`` must be (``bartlebot/gui/CLAUDE.md``, qasync).
        """
        ws = StrategyWorkingSet.from_store()
        current = self._chosen or SHIPPED_DUEL_IDS[self._size]
        dlg = CombatStrategyDialog(
            ws,
            kind=StrategyKind.DUEL,
            mode=DialogMode.PICK,
            current_id=current,
            allow_none=False,
            team_size=PICK_TEAM_SIZE[self._size],
            parent=self,
        )
        if dlg.exec() and dlg.selected_id():
            self.set_choice(dlg.selected_id())

    def _redraw(self) -> None:
        with held_listings():
            self._redraw_now()

    def _redraw_now(self) -> None:
        self._checks.reset()
        library = StrategyWorkingSet.from_store()
        duel = library.get(StrategyKind.DUEL, self._chosen) if self._chosen else None
        notes = default_choice_notes(self._size, self._chosen)
        shown = duel or library.get(StrategyKind.DUEL, SHIPPED_DUEL_IDS[self._size])

        if self._strategy_card is not None:
            self._card_slot.removeWidget(self._strategy_card)
            self._strategy_card.deleteLater()
            self._strategy_card = None
        if shown is not None:
            summary = summarize_duel(
                shown, library.lookup().players, name=shown.name, validate=self._checks.validator()
            )
            # With its source pill, as the combat dialog draws it: Official for the
            # recommended one, and whatever the user's choice is.
            summary = replace(summary, source=library.status(StrategyKind.DUEL, shown.id))
            self._strategy_card = StrategyCard(summary, parent=self)
            self._card_slot.addWidget(self._strategy_card)

        if shown is None:
            lead = UNREADABLE
        elif self._chosen is None:
            lead = RECOMMENDED_NOTE
        else:
            lead = CHOSEN_NOTE
        self._note.setText(" ".join([lead, *notes]))
        self._recommended_btn.setEnabled(self._chosen is not None)
        self._checks.request_unknown()


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
