"""Setup: Quick or Custom, then a list of pages, each of which writes a real choice (epic #215).

Opened by ``AppController._after_window_shown`` while any page is unanswered, and again
from File › Set up BartleBot…. ``docs/FIRST_RUN.md`` lists the pages, what each writes,
and when each counts as answered.

WHY A QUESTION AND NOT A TOUR
-----------------------------

Tutorials get skipped and forgotten. A setup step that asks for a decision and writes it
to the config gets answered, and stays useful when it can be reached again later: OBS
Studio's auto-configuration wizard runs again from Tools. So each page asks something whose
answer is stored, and the same choice can be made again in Settings (for the combat page,
Settings › Combat).

THE PAGES
---------

A page is a :class:`FirstRunPage`: a heading, its body, and three methods. ``answered()``
says its setting already exists, so a first run passes it over. ``applies(mode)`` says it
belongs in the walk now, under Quick or Custom. ``record()`` writes its answer and says
whether that reached disk.

The dialog opens on :class:`ModePage`, Quick or Custom, which is not stored: it only
decides which pages follow. **There is no Skip.** Next records the page shown and moves on
only once that write succeeded; a failed one keeps the user on the page and says so. Back
records nothing. Escape and the close button reject and write nothing more, so the next
launch opens on the first page still unanswered: closing keeps what was answered and asks
the rest next time (Plex's Skip, which left setup to be found again by hand, is the
failure this avoids).

On a first run the walk is the pages unanswered when the dialog opened; a page answered on
the way stays in it, so Back still reaches it. A rerun walks every page that applies, each
on the current value.

What each answer stores lives in a Qt-free module, as :mod:`bartlebot.shared.first_run`
does for the combat page, so BartleBuddy asks the same question and writes the same keys.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from enum import Enum

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDialog, QGridLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from bartlebot.gui.dialogs.new_bot_dialog import StartCard
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.buttons.action_button import ActionButton
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.shared.first_run import RECOMMENDED, BossTactic, combat_question_answered, current_tactic, record_answer
from bartlebot.shared.preferences import PreferenceStore

logger = logging.getLogger(__name__)

TITLE = "Set up BartleBot"
NEXT = "Next"
FINISH = "Finish"
BACK = "Back"
#: Under the heading: where the user is in the walk.
STEP = "Step {n} of {m}"
#: Under the page, when its answer did not reach disk.
SAVE_FAILED = "Couldn't save this answer, so it will be asked again. Try Next again, or close and reopen BartleBot."
RECOMMENDED_DETAIL = "Recommended"


class SetupMode(Enum):
    """Quick asks what a first run needs; Custom adds the options Quick sets to their defaults."""

    QUICK = "quick"
    CUSTOM = "custom"


class FirstRunPage(QWidget):
    """One page of setup: a heading, a body, whether it is answered, and a way to store its answer."""

    #: Emitted when the page's answer changes what follows: the walk is recounted.
    walk_changed = pyqtSignal()

    #: The question the page asks, shown above it.
    heading = ""

    def answered(self) -> bool:
        """Whether its setting already exists, so a first run need not ask."""
        return False

    def applies(self, mode: SetupMode) -> bool:
        """Whether the page belongs in the walk now: under *mode*, and with what is set up so far."""
        return True

    def record(self) -> bool:
        """Store this page's answer. Whether it reached disk."""
        raise NotImplementedError


class _CardsPage(FirstRunPage):
    """A page answered by picking one :class:`StartCard`, the recommended one first and selected.

    A card selects itself on taking focus, so the selected one takes it on show: left to
    the focus chain, showing the dialog hands it to the first card, which changes the
    answer. With none selected the page itself takes it, for the same reason.
    """

    def __init__(self, choices: Sequence[tuple[object, str, str, bool]], parent: QWidget | None = None) -> None:
        """*choices* are (value, name, blurb, recommended), in the order shown."""
        super().__init__(parent)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(12)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.column.addLayout(grid)

        self._values = [value for value, *_ in choices]
        self._cards: list[StartCard] = []
        for index, (value, name, blurb, recommended) in enumerate(choices):
            card = StartCard(name, blurb, detail=RECOMMENDED_DETAIL if recommended else "")
            card.chosen.connect(lambda v=value: self._choose(v))
            self._cards.append(card)
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        self._value: object | None = None

    @property
    def cards(self) -> list[StartCard]:
        """The answer cards, in the order shown: the recommended one first."""
        return list(self._cards)

    def card_for(self, value: object) -> StartCard:
        return self._cards[self._values.index(value)]

    def _choose(self, value: object | None) -> None:
        changed = value != self._value
        self._value = value
        for each, card in zip(self._values, self._cards):
            card.set_selected(each == value)
        if changed:
            self.walk_changed.emit()

    def showEvent(self, event) -> None:
        # Here rather than in the constructor, where the page was not yet in the dialog
        # and the focus was lost with the reparenting.
        super().showEvent(event)
        if self._value is None:
            self.setFocus()
        else:
            self.card_for(self._value).setFocus()


# -- Quick or Custom ------------------------------------------------------------

MODE_HEADING = "How would you like to set up BartleBot?"
#: Each mode's card: its name, and what it asks.
MODE_CARDS: dict[SetupMode, tuple[str, str]] = {
    SetupMode.QUICK: (
        "Quick setup",
        "Only the questions BartleBot needs answered before your first run. Everything else starts "
        "on its usual setting, which you can change later in Settings.",
    ),
    SetupMode.CUSTOM: (
        "Custom setup",
        "The same questions, then a page of options Quick setup leaves at their defaults, each shown "
        "with its default selected.",
    ),
}


class ModePage(_CardsPage):
    """The first page: Quick (recommended, selected) or Custom. Not stored; it decides which pages follow."""

    heading = MODE_HEADING

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            [(mode, *MODE_CARDS[mode], mode is SetupMode.QUICK) for mode in SetupMode],
            parent,
        )
        self.column.addStretch()
        self.select(SetupMode.QUICK)

    @property
    def mode(self) -> SetupMode:
        return self._value

    def select(self, mode: SetupMode) -> None:
        self._choose(mode)

    def record(self) -> bool:
        return True


# -- the combat question ------------------------------------------------------------

COMBAT_HEADING = "How should your team take on bosses?"
#: Each answer's card: its name, and what it does in a fight.
TACTIC_CARDS: dict[BossTactic, tuple[str, str]] = {
    BossTactic.ONE_HITTER: (
        "One hitter, the others support",
        "P1 attacks. While a boss is up, the others blade P1 and trap and feint the boss, and P1 "
        "casts last. In fights with no boss, everyone attacks.",
    ),
    BossTactic.EVERYONE_ATTACKS: (
        "Everyone attacks",
        "Every wizard plays its own attacks, in boss fights and every other fight.",
    ),
}
#: Where the defaults are changed later, held together so a wrap never splits it.
SETTINGS_COMBAT = "Settings › Combat"
COMBAT_NOTE = (
    "Fights with one wizard use Default Solo Duel. You can change either default later in "
    f"{SETTINGS_COMBAT}, and tailor any bot's combat on its Combat tab."
)


class CombatQuestionPage(_CardsPage):
    """How the team fights bosses: one card per :class:`BossTactic`, the recommended one first.

    It opens on the answer the stored Team default already is. On a rerun that default can
    be a duel neither answer names, chosen in Settings › Combat; then no card is selected,
    and Next with none selected keeps that duel.
    """

    heading = COMBAT_HEADING

    def __init__(self, prefs: PreferenceStore, parent: QWidget | None = None) -> None:
        tactics = sorted(BossTactic, key=lambda tactic: tactic is not RECOMMENDED)
        super().__init__([(t, *TACTIC_CARDS[t], t is RECOMMENDED) for t in tactics], parent)
        self._prefs = prefs
        self._note = ThemedTextLabel(COMBAT_NOTE, css=f"font-size: {THEME.font_small_px}px;")
        self._note.setWordWrap(True)
        self.column.addWidget(self._note)
        self.column.addStretch()
        self.select(current_tactic(prefs))

    @property
    def tactic(self) -> BossTactic | None:
        """The answer selected now; None keeps a Team default neither answer names."""
        return self._value

    @property
    def note_text(self) -> str:
        return self._note.text()

    def select(self, tactic: BossTactic | None) -> None:
        self._choose(tactic)

    def answered(self) -> bool:
        return combat_question_answered(self._prefs)

    def record(self) -> bool:
        return record_answer(self._prefs, self._value)


def setup_pages(prefs: PreferenceStore) -> list[FirstRunPage]:
    """Every setup page after Quick or Custom, in the order shown (``docs/FIRST_RUN.md``)."""
    return [CombatQuestionPage(prefs)]


# -- the dialog ---------------------------------------------------------------------


class FirstRunDialog(QDialog):
    """Quick or Custom, then the pages, one at a time. Accepted means Finish on the last page.

    *pages* are the pages after Quick or Custom. On a first run the dialog walks those not
    yet answered; with *rerun* it walks every page, each on the current value. Either way a
    page is in the walk only while it ``applies`` under the mode chosen.
    """

    def __init__(self, pages: Sequence[FirstRunPage], *, rerun: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not pages:
            raise ValueError("the setup dialog needs at least one page")
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(560)
        self._mode_page = ModePage()
        self._pages = [self._mode_page, *pages]
        # Fixed here, not asked as the user goes: a page answered on the way stays in the walk.
        self._asked = [page for page in pages if rerun or not page.answered()]
        self._rerun = rerun

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)
        self._heading = QLabel()
        self._heading.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        self._heading.setWordWrap(True)
        layout.addWidget(self._heading)
        self._step = ThemedTextLabel("", css=f"font-size: {THEME.font_small_px}px;")
        layout.addWidget(self._step)
        layout.addSpacing(8)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        for page in self._pages:
            self._stack.addWidget(page)
            page.walk_changed.connect(self._refresh)
        self._error = ThemedTextLabel(SAVE_FAILED, color="error", css=f"font-size: {THEME.font_small_px}px;")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addSpacing(8)

        # Back takes the slot beside the commit that is Cancel elsewhere: there is no
        # Cancel here, and Escape or the close button is how setup is left.
        footer = dialog_footer(
            self,
            commit_text=NEXT,
            commit_icon="arrow_right",
            on_commit=self._advance,
            cancel_text=BACK,
            cancel_icon="arrow_left",
            on_cancel=self._go_back,
        )
        self._commit = footer.commit
        self._back = footer.cancel
        layout.addLayout(footer.layout)
        self._show(self._mode_page)

    @property
    def rerun(self) -> bool:
        return self._rerun

    @property
    def pages(self) -> list[FirstRunPage]:
        """Every page the dialog holds, Quick or Custom first, whether or not it is in the walk."""
        return list(self._pages)

    @property
    def mode_page(self) -> ModePage:
        return self._mode_page

    @property
    def walk(self) -> list[FirstRunPage]:
        """The pages the user steps through now, in order. Recounted as the mode, or a page's answer, changes."""
        mode = self._mode_page.mode
        return [self._mode_page, *(page for page in self._asked if page.applies(mode))]

    @property
    def current(self) -> FirstRunPage:
        return self._stack.currentWidget()

    @property
    def heading_text(self) -> str:
        return self._heading.text()

    @property
    def step_text(self) -> str:
        return self._step.text()

    @property
    def error_shown(self) -> bool:
        return not self._error.isHidden()

    @property
    def commit_button(self) -> ActionButton:
        return self._commit

    @property
    def back_button(self) -> ActionButton:
        return self._back

    def _show(self, page: FirstRunPage) -> None:
        self._stack.setCurrentWidget(page)
        self._heading.setText(page.heading)
        self._error.setVisible(False)
        self._refresh()

    def _refresh(self) -> None:
        """The step count, Back, and Next or Finish, for the page shown and the walk as it stands."""
        walk = self.walk
        index = walk.index(self.current)
        self._step.setText(STEP.format(n=index + 1, m=len(walk)))
        self._back.setVisible(index > 0)
        self._commit.setText(FINISH if index == len(walk) - 1 else NEXT)

    def _advance(self) -> None:
        """Next or Finish: record the page shown, and move on only once that reached disk."""
        page = self.current
        if not page.record():
            logger.warning("Could not save the setup answer to %r; the page stays open", page.heading)
            self._error.setVisible(True)
            return
        # Recounted after the write: an answer can change which pages follow.
        walk = self.walk
        index = walk.index(page)
        if index == len(walk) - 1:
            self.accept()
        else:
            self._show(walk[index + 1])

    def _go_back(self) -> None:
        walk = self.walk
        index = walk.index(self.current)
        if index > 0:
            self._show(walk[index - 1])
