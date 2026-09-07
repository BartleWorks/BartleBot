"""A combo box for authoring a school requirement: one of deck_io.PIP_SCHOOLS, or None ("Any").

Shared by the assignment row (PlayerAssignment.school) and the player strategy
editor (PlayerCombatStrategy.school) so the two never drift on which schools
exist or how "no requirement" is spelled.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox

from bartlebot.deck_io import PIP_SCHOOLS

_ANY_LABEL = "Any school"


class SchoolCombo(QComboBox):
    """Picks a school from :data:`bartlebot.deck_io.PIP_SCHOOLS`, or ``None``."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.addItem(_ANY_LABEL, None)
        for school in PIP_SCHOOLS:
            self.addItem(school, school)

    def value(self) -> str | None:
        return self.currentData()

    def set_value(self, school: str | None) -> None:
        index = self.findData(school)
        self.setCurrentIndex(index if index >= 0 else 0)
