"""``StepHeading``: what a dialog says when it is one step of a flow (#272).

Adding a duel strategy on the Combat tab is two dialogs, chained, and the first testers
could not tell they were in the middle of anything: the strategy dialog's title named no
purpose, its Save said nothing about what came after it, and the fights appeared in a
popup that a click elsewhere dismissed. So each step heads itself the same way: a small
"Step 1 of 2" over a heading that says what this step decides, and a muted line under it
saying what follows. The step's commit button says where it goes next, too, but that is
the dialog's to word.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel


@dataclass(frozen=True)
class Step:
    """One step of a flow: where it is in it, what it decides, and what follows."""

    number: int
    total: int
    heading: str
    note: str = ""

    @property
    def counter(self) -> str:
        return f"Step {self.number} of {self.total}"


class StepHeading(QWidget):
    """The counter, the heading and the note, stacked. The note hides when empty."""

    def __init__(self, step: Step, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        self._counter = ThemedTextLabel(
            step.counter, color="accent", css=f"font-size: {THEME.font_small_px}px; font-weight: 600;", parent=self
        )
        layout.addWidget(self._counter)
        self._heading = ThemedTextLabel(
            step.heading, color="text", css=f"font-size: {THEME.font_heading_px}px; font-weight: 600;", parent=self
        )
        self._heading.setWordWrap(True)
        layout.addWidget(self._heading)
        self._note = ThemedTextLabel(step.note, parent=self)
        self._note.setWordWrap(True)
        self._note.setVisible(bool(step.note))
        layout.addWidget(self._note)

    def counter_text(self) -> str:
        return self._counter.text()

    def heading_text(self) -> str:
        return self._heading.text()

    def note_text(self) -> str:
        return self._note.text()
