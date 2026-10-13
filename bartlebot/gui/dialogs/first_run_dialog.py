"""First run: a short list of questions, each of which writes a real choice (epic #201, Stage 6).

Opened once, after the main window shows, by ``AppController._after_window_shown``. Today
the list holds one page, :class:`CombatQuestionPage`: how the team fights bosses. Accounts
and the game path can join it later as pages of their own.

WHY A QUESTION AND NOT A TOUR
-----------------------------

Tutorials get skipped and forgotten. A setup step that asks for a decision and writes it
to the config gets answered, and stays useful when it can be reached again later: OBS
Studio's auto-configuration wizard runs again from Tools. So each page asks something whose
answer is stored, and the same choice can be made again in Settings (for this page,
Settings › Combat).

THE PAGES
---------

A page is a :class:`FirstRunPage`: a heading, its body, and :meth:`FirstRunPage.record`,
which writes the page's answer, or for a skipped page whatever records that it was asked.
The dialog walks the pages with Back and Next and ends on Continue. **Skip ends the whole
dialog**, and every page then records itself as skipped, so nothing is asked again. Escape
and the window's close button are Skip: the footer's reject slot.

What each answer stores is :mod:`bartlebot.shared.first_run`'s, which has no Qt, so
BartleBuddy asks the same question and writes the same keys.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtWidgets import QDialog, QGridLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from bartlebot.gui.dialogs.new_bot_dialog import StartCard
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.buttons.action_button import ActionButton
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.shared.first_run import RECOMMENDED, BossTactic, record_answer
from bartlebot.shared.preferences import PreferenceStore

TITLE = "Set up BartleBot"
CONTINUE = "Continue"
NEXT = "Next"
SKIP = "Skip"

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
RECOMMENDED_DETAIL = "Recommended"
#: Where the defaults are changed later, held together so a wrap never splits it.
SETTINGS_COMBAT = "Settings › Combat"
COMBAT_NOTE = (
    "Fights with one wizard use Default Solo Duel. You can change either default later in "
    f"{SETTINGS_COMBAT}, and tailor any bot's combat on its Combat tab."
)


class FirstRunPage(QWidget):
    """One page of the first-run dialog: a heading, a body, and a way to store its answer."""

    #: The question the page asks, shown above it.
    heading = ""

    def record(self, prefs: PreferenceStore, *, skipped: bool) -> bool:
        """Store this page's answer, or for *skipped* only that it was asked. Whether it reached disk."""
        raise NotImplementedError


class CombatQuestionPage(FirstRunPage):
    """How the team fights bosses: one card per :class:`BossTactic`, the recommended one first."""

    heading = COMBAT_HEADING

    def __init__(self, initial: BossTactic | None = None, parent: QWidget | None = None) -> None:
        """*initial* is the answer the stored Team default already is; None selects the recommended one."""
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        column.addLayout(grid)

        self._tactics = sorted(BossTactic, key=lambda tactic: tactic is not RECOMMENDED)
        self._cards: list[StartCard] = []
        for index, tactic in enumerate(self._tactics):
            name, blurb = TACTIC_CARDS[tactic]
            card = StartCard(name, blurb, detail=RECOMMENDED_DETAIL if tactic is RECOMMENDED else "")
            card.chosen.connect(lambda t=tactic: self.select(t))
            self._cards.append(card)
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)

        self._note = ThemedTextLabel(COMBAT_NOTE, css=f"font-size: {THEME.font_small_px}px;")
        self._note.setWordWrap(True)
        column.addWidget(self._note)
        column.addStretch()

        self._tactic = initial or RECOMMENDED
        self.select(self._tactic)

    @property
    def tactic(self) -> BossTactic:
        """The answer selected now."""
        return self._tactic

    @property
    def cards(self) -> list[StartCard]:
        """The answer cards, in the order shown: the recommended one first."""
        return list(self._cards)

    @property
    def note_text(self) -> str:
        return self._note.text()

    def card_for(self, tactic: BossTactic) -> StartCard:
        return self._cards[self._tactics.index(tactic)]

    def select(self, tactic: BossTactic) -> None:
        self._tactic = tactic
        for each, card in zip(self._tactics, self._cards):
            card.set_selected(each is tactic)

    def record(self, prefs: PreferenceStore, *, skipped: bool) -> bool:
        return record_answer(prefs, None if skipped else self._tactic)

    def showEvent(self, event) -> None:
        # A card selects itself on taking focus, so the selected one takes it first: left to
        # the focus chain, showing the dialog hands it to the first card, which changes the
        # answer. Here rather than in the constructor, where the page was not yet in the
        # dialog and the focus was lost with the reparenting.
        super().showEvent(event)
        self.card_for(self._tactic).setFocus()


class FirstRunDialog(QDialog):
    """The first-run pages, one at a time. Accepted means Continue on the last; anything else is Skip."""

    def __init__(self, pages: Sequence[FirstRunPage], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not pages:
            raise ValueError("the first-run dialog needs at least one page")
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(560)
        self._pages = list(pages)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self._heading = QLabel()
        self._heading.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        self._heading.setWordWrap(True)
        layout.addWidget(self._heading)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        for page in self._pages:
            self._stack.addWidget(page)

        self._back = ActionButton("arrow_left", "Back", fill=False)
        self._back.clicked.connect(lambda: self._show(self._stack.currentIndex() - 1))
        footer = dialog_footer(
            self,
            commit_text=CONTINUE,
            commit_icon="arrow_right",
            on_commit=self._advance,
            cancel_text=SKIP,
            extras=[self._back],
        )
        self._commit = footer.commit
        self._skip = footer.cancel
        layout.addLayout(footer.layout)
        self._show(0)

    @property
    def pages(self) -> list[FirstRunPage]:
        return list(self._pages)

    @property
    def page(self) -> int:
        """The index of the page shown."""
        return self._stack.currentIndex()

    @property
    def heading_text(self) -> str:
        return self._heading.text()

    @property
    def commit_button(self) -> ActionButton:
        return self._commit

    @property
    def skip_button(self) -> ActionButton:
        return self._skip

    @property
    def back_button(self) -> ActionButton:
        return self._back

    def record(self, prefs: PreferenceStore) -> bool:
        """Have every page store its answer, or record itself skipped unless the dialog was accepted.

        Every page is asked even when one fails, so a failed write costs that page alone.
        Returns whether all of them reached disk.
        """
        skipped = self.result() != QDialog.DialogCode.Accepted
        results = [page.record(prefs, skipped=skipped) for page in self._pages]
        return all(results)

    def _show(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._heading.setText(self._pages[index].heading)
        last = index == len(self._pages) - 1
        self._back.setVisible(index > 0)
        self._commit.setText(CONTINUE if last else NEXT)

    def _advance(self) -> None:
        if self._stack.currentIndex() < len(self._pages) - 1:
            self._show(self._stack.currentIndex() + 1)
        else:
            self.accept()
