"""A multi-select picker of schools: authoring a school set, or filtering by one (#125).

Replaces the single-school ``SchoolCombo``. A strategy that works for Storm and Fire
alike could only say one of them, or say "any" and so claim Life as well; a set says
exactly what the author means. Empty is "any school", which is why the placeholder
says so rather than "—".

Shared by the assignment card (``PlayerAssignment.schools``), the Player Strategy
editor (``PlayerCombatStrategy.schools``) and the strategy dialog's filter bar, so
none of them can drift on which schools exist or how "no requirement" is spelled.
The filter passes ``caption``, and gets the facet button's shape: its name always
legible, with a count once anything is selected, as ``TagPickerButton`` does.
"""

from __future__ import annotations

from bartleby.qt.art_signal import follow_art
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget

from bartlebot.combat.deck_io import PIP_SCHOOLS
from bartlebot.game import card_art
from bartlebot.gui.widgets.cards.card_art_tile import art_icon
from bartlebot.gui.widgets.pickers.picker_popup import PickerButton, PickerRow, Section, summarize
from bartlebot.gui.widgets.pickers.tag_picker import count_label

ANY_SCHOOL = "Any school"


def school_icon(school: str) -> QIcon | None:
    """The school's extracted medallion, or None — the ordinary case on most installs."""
    path = card_art.icon_path(card_art.school_icon_name(school))
    return None if path is None else art_icon(path)


def school_sections(
    counts: dict[str, int] | None = None, noun: tuple[str, str] = ("entry", "entries")
) -> list[Section]:
    """One section of every pip school, with a count per school when *counts* is given."""
    rows = [
        PickerRow(
            key=school,
            label=school,
            meta="" if counts is None else count_label(counts.get(school, 0), noun),
            dim=counts is not None and counts.get(school, 0) == 0,
            icon=school_icon(school),
        )
        for school in PIP_SCHOOLS
    ]
    return [("Schools", rows)]


class SchoolPickerButton(PickerButton):
    """Picks any number of :data:`bartlebot.combat.deck_io.PIP_SCHOOLS`; none selected means any school."""

    #: Class-level default so ``PickerButton.__init__`` can call ``format_text`` first.
    _caption: str | None = None

    def __init__(self, *, caption: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(
            "Schools",
            placeholder=ANY_SCHOOL,
            search_placeholder="Filter schools…",
            parent=parent,
        )
        self._caption = caption
        self.set_counts(None)
        # The rows hold their medallions, so new art means new rows.
        follow_art(self, lambda: self.set_counts(*self._counts))

    def format_text(self, labels: list[str]) -> str:
        if self._caption is not None:
            return self._caption if not labels else f"{self._caption} ({len(labels)})"
        return summarize(labels, self._placeholder)

    def set_counts(self, counts: dict[str, int] | None, noun: tuple[str, str] = ("entry", "entries")) -> None:
        """Rebuild the rows with how many entries work with each school."""
        self._counts = (counts, noun)
        self.set_sections(school_sections(counts, noun))

    def value(self) -> list[str]:
        """The selected schools in ``PIP_SCHOOLS`` order; empty for any school."""
        chosen = set(self.selection())
        return [s for s in PIP_SCHOOLS if s in chosen]

    def set_value(self, schools: list[str]) -> None:
        """Show *schools* as selected, without emitting. A name that is not a pip school is dropped."""
        self.set_selection([s for s in PIP_SCHOOLS if s in set(schools)])
