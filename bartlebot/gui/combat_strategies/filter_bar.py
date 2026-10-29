"""``StrategyFilterBar`` — the facet pickers and search box across the top of one tab (#125).

Shaped like the bot library's top bar, and for the same reasons: filters first, then
the search box filling what they leave, and the *selected* values underneath as
removable chips, with the vocabulary itself kept behind the pickers. Which pickers a
tab gets is :mod:`bartlebot.combat.strategy_tags`' business — Source everywhere, Role on
Player and Deck, School on Duel and Player, Team size on Duel alone.

The bar reads nothing and filters nothing. It holds a selection, says when it has
changed, and hands it over as a :class:`~bartlebot.combat.strategy_tags.StrategyFilter`; the
page applies it. Nothing here persists: every opening of the dialog starts
unfiltered, because a remembered filter hiding entries is exactly the "where did my
strategy go" a Pick-mode visitor cannot diagnose.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from bartleby.qt.widgets.buttons.action_button import ActionButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from bartlebot.combat.deck_io import PIP_SCHOOLS
from bartlebot.combat.strategy_store import StrategyKind
from bartlebot.combat.strategy_tags import (
    KIND_GROUPS,
    SCHOOL_KINDS,
    SOURCE,
    TEAM_SIZE_KINDS,
    TEAM_SIZES,
    THIS_BOT_SOURCE,
    StrategyFacets,
    StrategyFilter,
    count_facets,
    team_label,
)
from bartlebot.gui.widgets.inputs.search_field import SearchLineEdit
from bartlebot.gui.widgets.layout.discard import discard_widget
from bartlebot.gui.widgets.pickers.picker_popup import PickerButton, PickerRow
from bartlebot.gui.widgets.pickers.school_picker import SchoolPickerButton
from bartlebot.gui.widgets.pickers.tag_picker import STRATEGY_VOCABULARY, TagPickerButton, TagVocabulary, count_label
from bartlebot.gui.widgets.pills.tag_pill import NeutralChip, TagChip

#: What each tab's counts count.
KIND_NOUNS: dict[StrategyKind, tuple[str, str]] = {
    StrategyKind.DUEL: ("duel", "duels"),
    StrategyKind.PLAYER: ("strategy", "strategies"),
    StrategyKind.DECK: ("preset", "presets"),
}

TEAM_SIZE_CAPTION = "Team size"


def _team_rows(counts: dict[int, int] | None, noun: tuple[str, str]) -> list[PickerRow]:
    return [
        PickerRow(
            key=str(n),
            label=team_label(n),
            meta="" if counts is None else count_label(counts.get(n, 0), noun),
            dim=counts is not None and counts.get(n, 0) == 0,
        )
        for n in TEAM_SIZES
    ]


class StrategyFilterBar(QWidget):
    """The pickers, the search box and the chip row for one kind's card list."""

    #: The selection or the query changed.
    changed = pyqtSignal()

    def __init__(self, kind: StrategyKind, parent: QWidget | None = None, *, this_bot: bool = False) -> None:
        """*this_bot* offers the Source value for the active Bot Combat Strategy's own entries,
        on a tab that shows them (#118); elsewhere there would never be one to find."""
        super().__init__(parent)
        self._kind = kind
        self._noun = KIND_NOUNS[kind]
        groups = dict(STRATEGY_VOCABULARY.groups)
        if not this_bot:
            groups[SOURCE] = tuple(t for t in groups[SOURCE] if t != THIS_BOT_SOURCE)
        self._vocabulary: TagVocabulary = replace(STRATEGY_VOCABULARY, groups=groups, noun=self._noun)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        self._tag_pickers: dict[str, TagPickerButton] = {}
        for group in KIND_GROUPS[kind]:
            picker = TagPickerButton(title=group, placeholder=group, group=group, vocabulary=self._vocabulary)
            picker.setToolTip(f"Filter by {group.lower()}")
            picker.selection_changed.connect(self._on_selection_changed)
            self._tag_pickers[group] = picker
            top.addWidget(picker, 0)

        self._school_picker: SchoolPickerButton | None = None
        if kind in SCHOOL_KINDS:
            self._school_picker = SchoolPickerButton(caption="School")
            self._school_picker.setToolTip("Show what works with a wizard of these schools")
            self._school_picker.selection_changed.connect(self._on_selection_changed)
            top.addWidget(self._school_picker, 0)

        self._team_picker: PickerButton | None = None
        if kind in TEAM_SIZE_KINDS:
            # Single-select: a team is one size, and "2 or 3" is not a question anyone asks.
            self._team_picker = PickerButton(TEAM_SIZE_CAPTION, placeholder=TEAM_SIZE_CAPTION, single_select=True)
            self._team_picker.set_sections([(TEAM_SIZE_CAPTION, _team_rows(None, self._noun))])
            self._team_picker.setToolTip("Show duels that a team of this size can run")
            self._team_picker.selection_changed.connect(self._on_selection_changed)
            top.addWidget(self._team_picker, 0)

        # Stretch 1, as in the bot library: the search box takes whatever the pickers leave.
        self._search = SearchLineEdit("Filter…")
        self._search.textChanged.connect(self.changed)
        top.addWidget(self._search, 1)
        root.addLayout(top)

        self._chip_bar = QWidget(self)
        self._chip_layout = QHBoxLayout(self._chip_bar)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(4)
        self._chip_layout.addStretch()
        self._chip_bar.setVisible(False)
        root.addWidget(self._chip_bar)

    # -- reading --------------------------------------------------------

    @property
    def search(self) -> SearchLineEdit:
        """The free-text box. Public so a caller or a test can type into it."""
        return self._search

    @property
    def tag_pickers(self) -> dict[str, TagPickerButton]:
        """One picker per tag group this tab offers, by group name."""
        return dict(self._tag_pickers)

    @property
    def school_picker(self) -> SchoolPickerButton | None:
        return self._school_picker

    @property
    def team_picker(self) -> PickerButton | None:
        return self._team_picker

    def query(self) -> str:
        return self._search.text()

    def selection(self) -> StrategyFilter:
        tags = frozenset(t for picker in self._tag_pickers.values() for t in picker.selection())
        schools = frozenset(self._school_picker.value()) if self._school_picker is not None else frozenset()
        team = self._team_picker.selection() if self._team_picker is not None else []
        return StrategyFilter(tags=tags, schools=schools, team_size=int(team[0]) if team else None)

    def chip_texts(self) -> list[str]:
        """What the chip row says, in order. Public so a test can read it."""
        texts = []
        for i in range(self._chip_layout.count()):
            w = self._chip_layout.itemAt(i).widget()
            if isinstance(w, TagChip):
                texts.append(w.tag)
            elif isinstance(w, NeutralChip):
                texts.append(w.value)
        return texts

    # -- writing --------------------------------------------------------

    def set_counts(self, all_facets: Iterable[StrategyFacets]) -> None:
        """Refresh every picker's per-value counts from the entries now in the list."""
        counts = count_facets(all_facets, PIP_SCHOOLS)
        for picker in self._tag_pickers.values():
            picker.set_counts(counts.tags)
        if self._school_picker is not None:
            self._school_picker.set_counts(counts.schools, self._noun)
        if self._team_picker is not None:
            self._team_picker.set_sections([(TEAM_SIZE_CAPTION, _team_rows(counts.team_sizes, self._noun))])

    def clear(self) -> None:
        """Drop every facet and the query, so the whole list shows again."""
        self._set_selection(StrategyFilter())
        self._search.clear()

    def clear_facets(self) -> None:
        self._set_selection(StrategyFilter())

    def set_team_size(self, team_size: int | None) -> None:
        """Select one Team size, or none, leaving the other facets as they are.

        How a way in that already knows the team opens on the duels it can run: Settings ›
        Combat's Team card presets two. It is an ordinary selection, shown as its chip, so
        the user can take it off like any other.
        """
        if self._team_picker is None:
            return
        self._set_selection(replace(self.selection(), team_size=team_size))

    def _set_selection(self, flt: StrategyFilter) -> None:
        """Change the selection without the pickers emitting, then say so once."""
        for group, picker in self._tag_pickers.items():
            picker.set_selection([t for t in self._vocabulary.groups[group] if t in flt.tags])
        if self._school_picker is not None:
            self._school_picker.set_value(sorted(flt.schools))
        if self._team_picker is not None:
            self._team_picker.set_selection([] if flt.team_size is None else [str(flt.team_size)])
        self._on_selection_changed()

    def _on_selection_changed(self, *_args) -> None:
        self._rebuild_chips()
        self.changed.emit()

    # -- chips ----------------------------------------------------------

    def _rebuild_chips(self) -> None:
        while self._chip_layout.count() > 1:
            item = self._chip_layout.takeAt(0)
            if w := item.widget():
                # Deferred, never now: a chip's own click is what brings us here, and
                # deleting it inside its handler would crash.
                discard_widget(w)

        flt = self.selection()
        chips: list[QWidget] = []
        for tag in self._vocabulary.tags:
            if tag in flt.tags:
                chip = TagChip(tag, tooltip=f"Remove the {tag} filter", vocabulary=self._vocabulary)
                chip.removed.connect(self._remove_tag)
                chips.append(chip)
        for school in PIP_SCHOOLS:
            if school in flt.schools:
                chip = NeutralChip(school, tooltip=f"Stop filtering by {school}")
                chip.removed.connect(self._remove_school)
                chips.append(chip)
        if flt.team_size is not None:
            chip = NeutralChip(team_label(flt.team_size), tooltip="Stop filtering by team size")
            chip.removed.connect(self._remove_team)
            chips.append(chip)
        if chips:
            clear = ActionButton("dismiss", "Clear", fill=False)
            clear.clicked.connect(self.clear_facets)
            chips.append(clear)
        for chip in chips:
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self._chip_bar.setVisible(bool(chips))

    def _remove_tag(self, tag: str) -> None:
        flt = self.selection()
        self._set_selection(replace(flt, tags=flt.tags - {tag}))

    def _remove_school(self, school: str) -> None:
        flt = self.selection()
        self._set_selection(replace(flt, schools=flt.schools - {school}))

    def _remove_team(self, _text: str) -> None:
        self._set_selection(replace(self.selection(), team_size=None))
