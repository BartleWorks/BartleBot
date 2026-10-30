"""``ChooseFightsDialog``: the second step of adding a duel strategy on the Combat tab (#272).

Which fights a new card covers used to be asked in the card's own ``PickerPopup``, opened
on a card that had appeared under the pointer a moment before. Most of the first testers
clicked away from it without choosing, which dropped the card without a word and left
them unsure whether anything had been added. So the question is a dialog now, and has to
be answered: **Add** (disabled until a fight is checked), **Back** to the strategy, or
**Cancel**, which adds nothing and says so by being a button the user pressed.

The list is the popup's own, built ``embedded``, so the rows read exactly as the card's
Fights… popup does, each saying what that fight uses today: checking one *moves* it, and
the right-hand slot is what makes that visible before the click.
"""

from __future__ import annotations

from enum import Enum

from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer
from bartleby.qt.widgets.pickers.picker_popup import PickerPopup, Section
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget

from bartlebot.gui.widgets.labels.step_heading import Step, StepHeading

TITLE = "Add a duel strategy"
ADD = "Add"
BACK = "Back"
FIGHTS = "Fights"
ADD_TIP = "Check at least one fight first"


class FightsAnswer(Enum):
    ADD = "add"
    BACK = "back"
    CANCEL = "cancel"


class ChooseFightsDialog(QDialog):
    """Ask which fights use a strategy. :attr:`answer` and :meth:`selection` say how it ended."""

    def __init__(self, step: Step, sections: list[Section], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self._answer = FightsAnswer.CANCEL

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        self._heading = StepHeading(step, self)
        layout.addWidget(self._heading)
        self._picker = PickerPopup(FIGHTS, sections, parent=self, embedded=True)
        self._picker.selection_changed.connect(self._on_selection_changed)
        layout.addWidget(self._picker, 1)

        self._back = ActionButton("arrow_left", BACK)
        self._back.clicked.connect(self._on_back)
        footer = dialog_footer(self, commit_text=ADD, on_commit=self._on_add, extras=[self._back])
        self._add = footer.commit
        layout.addLayout(footer.layout)
        self._on_selection_changed(self._picker.selection())
        self._picker.focus_search()

    # -- reading --------------------------------------------------------

    @property
    def answer(self) -> FightsAnswer:
        return self._answer

    def selection(self) -> list[str]:
        """The fights checked, in the order the list shows them."""
        return self._picker.selection()

    @property
    def heading(self) -> StepHeading:
        return self._heading

    @property
    def picker(self) -> PickerPopup:
        return self._picker

    @property
    def add_button(self) -> ActionButton:
        return self._add

    @property
    def back_button(self) -> ActionButton:
        return self._back

    # -- the three ways out ---------------------------------------------

    def _on_selection_changed(self, keys: list) -> None:
        self._add.setEnabled(bool(keys))
        self._add.setToolTip("" if keys else ADD_TIP)

    def _on_add(self) -> None:
        if not self.selection():
            return
        self._answer = FightsAnswer.ADD
        self.accept()

    def _on_back(self) -> None:
        self._answer = FightsAnswer.BACK
        self.accept()

    def reject(self) -> None:
        self._answer = FightsAnswer.CANCEL
        super().reject()
