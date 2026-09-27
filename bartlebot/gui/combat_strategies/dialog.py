"""``CombatStrategyDialog`` — the one surface where combat strategies are edited.

WHY A DIALOG, AND WHY ONLY ONE
------------------------------

The Combat tab was permanently in an edit mode it never declared: no Edit
button, no Save button, and a 500ms autosave straight into a shipped file. Every
other surface in the app keys its editability off ``BotMode.EDIT``, but combat
is the one thing most users *will* need to change — it depends on which wizards
they own — while never touching a bot graph. So combat editing gets its own
surface with an explicit Save/Cancel boundary, and that boundary is what retires
the autosave.

There used to be two overlapping windows doing most of this job with different
vocabularies (a "Strategy Library" and a "Strategy Picker"). This is both, in
two modes:

``MANAGE``  ``[Cancel] [Save]``                     — opened from the File menu.
``PICK``    ``[Use built-in default] [Cancel] [Use this]`` — opened from a Change… button.

The detail pane is editable in *both*, which is what makes one window enough:
picking a strategy and discovering it needs a tweak does not mean closing this
and opening something else. One rule covers it — **Cancel discards every edit;
Save and Use both commit every edit**, and Use additionally returns the
selection.

WHY MASTER-DETAIL RATHER THAN A POPUP
-------------------------------------

``PickerPopup`` is 400×420 and right for a one-line payload — a tag, a fight
name. A DuelCombatStrategy's payload is seven to nine mostly-long lines: a
description, a combat order, and one row per player assignment naming a strategy
and a note. Eliding those to fit a popup would hide exactly the part the user
opened it to compare. So: a compact :class:`StrategyCard` list on the left with
a filter, the full detail on the right, unelided, with a usage count — the shape
``_AddOptionDialog`` already uses for the same reason.

WHAT IT IS A VIEW OF
--------------------

A :class:`~bartlebot.strategy_workset.StrategyWorkingSet` — deep copies, edited
freely, written only by ``commit()``. Nothing below this dialog decides when a
file is written any more. See that module for why dirtiness is derived rather
than reported.

TAGS, FILTERS AND FAVORITES (#125)
----------------------------------

Each tab has a :class:`~bartlebot.gui.combat_strategies.filter_bar.StrategyFilterBar`
across its top: facet pickers and the search box, with the selection as chips below.
:mod:`bartlebot.strategy_tags` holds the vocabulary and the matching rule.

Favorites are the one thing here that is *not* inside the Save/Cancel boundary. A
star is saved to the user's settings on the click, because it is a preference about
the library rather than a change to it. A favorite moves its card into a "★
Favorites" section at the top. Each entry is in exactly one section, never both:
selection is restored by id, so an entry drawn twice would make it ambiguous which
row is selected. The headers only appear once something is a favorite, and a section
that the filter empties hides its header.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from enum import Enum

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.combat_strategies.deck_editor import DeckPresetEditor
from bartlebot.gui.combat_strategies.duel_editor import DuelConfigEditor
from bartlebot.gui.combat_strategies.filter_bar import KIND_NOUNS, StrategyFilterBar
from bartlebot.gui.combat_strategies.player_editor import PlayerStrategyEditor
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.dialog_footer import dialog_footer
from bartlebot.gui.widgets.elided_label import apply_muted_color
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.scroll_gutter import reserve_scrollbar_gutter
from bartlebot.gui.widgets.strategy_card import StrategyCard
from bartlebot.gui.widgets.tag_picker import STRATEGY_VOCABULARY, TagPickerButton
from bartlebot.settings_manager import SettingsManager
from bartlebot.store_roots import SOURCE_LABELS
from bartlebot.strategy_store import Origin, StrategyKind
from bartlebot.strategy_summary import (
    StrategySummary,
    summarize_deck,
    summarize_duel,
    summarize_player,
)
from bartlebot.strategy_tags import (
    KIND_GROUPS,
    STORED_GROUPS,
    StrategyFacets,
    facets_match,
    normalize_strategy_tags,
)
from bartlebot.strategy_workset import StrategyWorkingSet

logger = logging.getLogger(__name__)


class DialogMode(Enum):
    """Which pair of buttons the dialog closes with — see the module docstring."""

    MANAGE = "manage"
    PICK = "pick"


#: Tab order, and the label each kind carries everywhere in this dialog. Duel first:
#: it is what a bot references, and what the Change… button opens onto.
KIND_LABELS = {
    StrategyKind.DUEL: "Duel Strategies",
    StrategyKind.PLAYER: "Player Strategies",
    StrategyKind.DECK: "Deck Presets",
}

#: What ``+ New`` names an entry before the user renames it.
NEW_NAMES = {
    StrategyKind.DUEL: "New Duel Strategy",
    StrategyKind.PLAYER: "New Player Strategy",
    StrategyKind.DECK: "New Deck Preset",
}

NONE_LABEL = "(built-in default)"

#: Where a row keeps the text its filter query is matched against, so filtering
#: never has to re-summarize the entry behind it.
_HAYSTACK_ROLE = Qt.ItemDataRole.UserRole + 1

#: Where a section header row keeps its text. Not DisplayRole: the item would paint it
#: underneath the heading widget drawn over the row.
_HEADING_ROLE = Qt.ItemDataRole.UserRole + 2

#: The two section headers, shown only once something is a favorite.
FAVORITES_HEADING = "★ Favorites"

#: Origin.SHIPPED_OVERRIDE means the shipped file reappears underneath, so the
#: honest verb is "revert" — see :class:`bartlebot.strategy_store.Origin`.
_REVERT_TEXT = "Revert to default"
_DELETE_TEXT = "Delete"
#: Why Delete is greyed out on an unedited built-in: removing only touches the user
#: root, so the entry would be back on the next open (#124).
_BUILT_IN_DELETE_TIP = "Built-in strategies can't be deleted. Duplicate one to make a copy of your own."

#: How far a tab's contents sit inside the tab pane, and how far *each* pane sits from
#: the splitter between them. Two numbers rather than one because the splitter handle is
#: already a few pixels of its own: the gap the eye sees beside a handle is the handle
#: plus this, while the gap beside the pane border is only this.
#:
#: The gutter is applied on **both** sides. Giving it to the detail pane alone left the
#: card list's filter box, its scrollbar and its button row all butted against the
#: divider — and a scrollbar pressed against a vertical line reads as part of the line.
_PAGE_INSET = 10
_SPLITTER_GUTTER = 12
#: The card list's per-row padding, in its stylesheet and in the width a row is fitted to.
_ROW_PAD = 1


def summary_haystack(summary: StrategySummary) -> str:
    """The text a filter query is matched against, lowercased.

    Description and subtitle are included deliberately: a user looking for "the
    storm one" is far more likely to have written that in the description than in
    the name.

    Built once per card and cached on its list item, because ``subtitle_line()``
    is only cheap relative to the summary it comes from — producing that summary
    parses every DSL the entry references.
    """
    source = SOURCE_LABELS[summary.source] if summary.source is not None else ""
    tags = " ".join(summary.tags)
    return f"{summary.name} {summary.description} {summary.subtitle_line()} {source} {tags}".lower()


def summary_facets(summary: StrategySummary) -> StrategyFacets:
    """What the facet filter asks of an entry: its stored tags plus its derived source."""
    tags = set(summary.tags)
    if summary.source is not None:
        tags.add(SOURCE_LABELS[summary.source])
    return StrategyFacets(tags=frozenset(tags), slot_schools=summary.slot_schools, player_range=summary.player_range)


def others_heading(kind: StrategyKind) -> str:
    """The header over the non-favorites: "Other duels", "Other strategies", "Other presets"."""
    return f"Other {KIND_NOUNS[kind][1]}"


def matches_query(haystack: str, query: str) -> bool:
    """Whether a cached haystack passes ``query``. An empty query passes everything."""
    needle = query.strip().lower()
    return not needle or needle in haystack


def matches_filter(summary: StrategySummary, query: str) -> str | None:
    """``None`` if ``summary`` should be hidden, else the haystack that matched.

    Pulled out as a plain function so the filter rule can be tested without a
    ``QApplication``, and so all three tabs cannot drift apart on what "search"
    means.
    """
    haystack = summary_haystack(summary)
    return haystack if matches_query(haystack, query) else None


class AboutSection(QWidget):
    """What a library entry *is*: its name, its description, and what depends on it.

    One widget for all three tabs. The three editors each used to draw a name field and a
    description field in an arrangement of its own — one with the label above, one with
    it beside, one with the description on the ``DuelConfig`` rather than on the entry —
    and the usage and origin lines sat above them in a fourth arrangement belonging to
    the page. That is four ways of saying the same two things on one dialog.

    Model-agnostic on purpose: every strategy kind has a ``name`` and a ``description``,
    which is all this touches, so the kind never has to be branched on. What differs
    between tabs is what sits *below* it, which is the editor's business.

    The two muted lines under the fields are things the card list has no room for and the
    old Strategy Library never said at all: how many things break if this goes, and
    whether "delete" here means delete or means revert.

    *tag_groups* are the stored tag groups this kind is tagged with — Role, on Player
    Strategies and Deck Presets — one picker row each (#125). Duels pass none.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, *, tag_groups: tuple[str, ...] = ()) -> None:
        super().__init__(parent)
        self._entry = None
        self._suppress = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(RuleHeading("About", self))

        # A two-column grid rather than labels stacked above their fields: these are two
        # short one-line values, and stacking them costs two rows of height at the top of
        # every tab for no gain in legibility.
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Name:", self), 0, 0)
        self._name_edit = QLineEdit(self)
        self._name_edit.textChanged.connect(self._on_changed)
        grid.addWidget(self._name_edit, 0, 1)
        grid.addWidget(QLabel("Description:", self), 1, 0)
        self._description_edit = QLineEdit(self)
        self._description_edit.setPlaceholderText("What this is for and why (optional)")
        self._description_edit.textChanged.connect(self._on_changed)
        grid.addWidget(self._description_edit, 1, 1)
        self._tag_pickers: list[TagPickerButton] = []
        for row, group in enumerate(tag_groups, start=2):
            grid.addWidget(QLabel(f"{group}:", self), row, 0)
            picker = TagPickerButton(
                title=group, placeholder="None", group=group, vocabulary=STRATEGY_VOCABULARY, lists_selection=True
            )
            picker.setToolTip(f"What this is for — shown on its card, and filterable by {group}")
            picker.selection_changed.connect(self._on_changed)
            # Sized to its selection at the left of the cell, not stretched across it.
            grid.addWidget(picker, row, 1, Qt.AlignmentFlag.AlignLeft)
            self._tag_pickers.append(picker)
        layout.addLayout(grid)

        self._usage_lbl = QLabel(self)
        apply_muted_color(self._usage_lbl)
        layout.addWidget(self._usage_lbl)
        self._origin_lbl = QLabel(self)
        self._origin_lbl.setWordWrap(True)
        apply_muted_color(self._origin_lbl)
        layout.addWidget(self._origin_lbl)

    def load(self, entry) -> None:
        self._entry = entry
        self._suppress = True
        self._name_edit.setText(entry.name)
        self._description_edit.setText(entry.description)
        tags = getattr(entry, "tags", [])
        for picker in self._tag_pickers:
            picker.set_selection([t for t in tags if picker.vocabulary.group_of(t) == picker.group])
        self._suppress = False

    def clear(self) -> None:
        self._entry = None
        self._suppress = True
        self._name_edit.clear()
        self._description_edit.clear()
        for picker in self._tag_pickers:
            picker.set_selection([])
        self._usage_lbl.clear()
        self._origin_lbl.clear()
        self._origin_lbl.setVisible(False)
        self._suppress = False

    def set_notes(self, usage: str, origin: str) -> None:
        """The two muted lines: what uses this entry, and where it came from."""
        self._usage_lbl.setText(usage)
        self._origin_lbl.setText(origin)
        self._origin_lbl.setVisible(bool(origin))

    @property
    def tag_pickers(self) -> list[TagPickerButton]:
        """One picker per stored tag group. Public so a test can tag an entry as a user would."""
        return list(self._tag_pickers)

    @property
    def usage_text(self) -> str:
        """What the usage line reads. Public so a test can assert what was said."""
        return self._usage_lbl.text()

    @property
    def origin_text(self) -> str:
        """What the origin line reads — empty for an entry with nothing to say."""
        return self._origin_lbl.text()

    def _on_changed(self) -> None:
        if self._suppress or self._entry is None:
            return
        # The last non-empty name stands, so an entry can never become a nameless card
        # with no way back to it.
        self._entry.name = self._name_edit.text().strip() or self._entry.name
        self._entry.description = self._description_edit.text()
        if self._tag_pickers:
            self._entry.tags = normalize_strategy_tags([t for p in self._tag_pickers for t in p.tags()])
        self.changed.emit()


class _KindPage(QWidget):
    """One tab: a filtered card list on the left, the detail editor on the right.

    Every kind gets the same page. What differs is which editor sits in the
    detail pane and how an entry is summarized, and both are parameters.
    """

    #: The list's structure changed (created, duplicated, removed) — as opposed
    #: to a field edit, which only ever redraws one card.
    structure_changed = pyqtSignal()
    promote_requested = pyqtSignal(object)
    #: The Duel tab's pencil asked to edit a Player Strategy, by id.
    open_pcs_requested = pyqtSignal(str)

    def __init__(self, kind: StrategyKind, ws: StrategyWorkingSet, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._ws = ws
        self._selected_id: str | None = None
        self._card_map: list[tuple[QListWidgetItem, StrategyCard]] = []
        #: Per entry id: what the facet filter asks of it, and which section it is in.
        self._facets: dict[str, StrategyFacets] = {}
        self._favorite_ids: set[str] = set()
        #: Section header rows, favorites first; empty when nothing is a favorite.
        self._headers: list[QListWidgetItem] = []

        layout = QVBoxLayout(self)
        # A real inset on all four sides. The page used to run edge to edge inside the tab
        # pane, so the filter box, the cards and the fields all sat directly against its
        # border — which is most of what made a screen of otherwise ordinary controls read
        # as crowded. A line with nothing beside it reads as a wall.
        layout.setContentsMargins(_PAGE_INSET, _PAGE_INSET, _PAGE_INSET, _PAGE_INSET)
        layout.setSpacing(6)

        # Across the whole tab, not over the card list alone: four pickers and a search
        # box need more width than the list has, and the chips need a row of their own.
        self._filter_bar = StrategyFilterBar(kind, self)
        # Filtering hides rows; it never rebuilds them. See _apply_filter.
        self._filter_bar.changed.connect(self._apply_filter)
        layout.addWidget(self._filter_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_master())
        splitter.addWidget(self._build_detail())
        # The detail side is where the long lines live; the card list only needs
        # enough width for a name and a subtitle.
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.rebuild_list()
        self._apply_style()

    # -- construction ---------------------------------------------------

    def _build_master(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, _SPLITTER_GUTTER, 0)
        box.setSpacing(4)

        self._list = QListWidget()
        self._list.setSpacing(2)
        # Filtering changes how many cards there are, and so whether the list scrolls.
        reserve_scrollbar_gutter(self._list)
        self._list.currentItemChanged.connect(self._on_item_changed)
        # Rows are sized for the width the column actually has; see _fit_row.
        self._list.viewport().installEventFilter(self)
        box.addWidget(self._list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self._new_btn = ActionButton("plus", "New")
        self._new_btn.clicked.connect(self._on_new)
        buttons.addWidget(self._new_btn, 1)
        self._dup_btn = ActionButton("copy", "Duplicate")
        self._dup_btn.clicked.connect(self._on_duplicate)
        buttons.addWidget(self._dup_btn, 1)
        # Caution from the start, and downgraded to tertiary only once the origin says
        # this button is a Revert — see ``_update_labels``.
        self._del_btn = ActionButton("trash", _DELETE_TEXT, style=ActionStyle.CAUTION)
        self._del_btn.clicked.connect(self._on_delete)
        buttons.addWidget(self._del_btn, 1)
        box.addLayout(buttons)
        return panel

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(_SPLITTER_GUTTER, 0, 0, 0)
        box.setSpacing(6)

        self._about = AboutSection(tag_groups=tuple(g for g in KIND_GROUPS[self._kind] if g in STORED_GROUPS))
        self._about.changed.connect(self._on_edited)
        box.addWidget(self._about)

        if self._kind is StrategyKind.DUEL:
            # No wrapper around the config editor: a DuelCombatStrategy IS-A DuelConfig,
            # and the one field the library entry added — the name — is the About
            # section's now.
            self._editor = DuelConfigEditor()
            self._editor.promote_requested.connect(self.promote_requested)
            self._editor.open_pcs_requested.connect(self.open_pcs_requested)
        elif self._kind is StrategyKind.PLAYER:
            self._editor = PlayerStrategyEditor()
        else:
            self._editor = DeckPresetEditor()
        self._editor.changed.connect(self._on_edited)

        # One outer scroll area, never a nested one: DuelConfigEditor grows its
        # rows to their full height on purpose and expects the page around them
        # to scroll.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # An assignment card folding open is what most often tips this pane into
        # scrolling, and without a reserved gutter every card narrows under the click.
        reserve_scrollbar_gutter(self._scroll)
        self._scroll.setWidget(self._editor)
        box.addWidget(self._scroll, 1)

        self._empty_lbl = QLabel("Select a strategy on the left, or press + New.")
        apply_muted_color(self._empty_lbl)
        box.addWidget(self._empty_lbl)
        return panel

    # -- reading --------------------------------------------------------

    def kind(self) -> StrategyKind:
        return self._kind

    def selected_id(self) -> str | None:
        return self._selected_id

    def summarize(self, item) -> StrategySummary:
        if self._kind is StrategyKind.DUEL:
            summary = summarize_duel(item, self._ws.players, name=item.name)
        elif self._kind is StrategyKind.PLAYER:
            summary = summarize_player(item)
        else:
            summary = summarize_deck(item)
        return replace(summary, source=self._ws.status(self._kind, item.id))

    @property
    def filter_bar(self) -> StrategyFilterBar:
        """The tab's facet pickers and search box."""
        return self._filter_bar

    def section_headings(self) -> list[str]:
        """The visible section headers, top to bottom. Empty when nothing is a favorite."""
        return [h.data(_HEADING_ROLE) for h in self._headers if not h.isHidden()]

    def visible_ids(self) -> list[str]:
        """The ids of the cards the filter leaves showing, top to bottom."""
        return [i.data(Qt.ItemDataRole.UserRole) for i, _c in self._card_map if not i.isHidden()]

    def card_for(self, uuid: str) -> StrategyCard | None:
        """The card drawn for ``uuid``, or None. Public so a test can read what a card says."""
        for item, card in self._card_map:
            if item.data(Qt.ItemDataRole.UserRole) == uuid:
                return card
        return None

    # -- the card list --------------------------------------------------

    def rebuild_list(self) -> None:
        """Redraw every card. Selection is restored by id, not by row index.

        Every entry gets a row, filtered-out ones included — the filter only
        decides which rows are *visible*. Building a ``StrategyCard`` means
        summarizing the entry, and summarizing a duel or player entry parses each
        DSL it references, so this is far too expensive to run per keystroke.
        Call it when the list's *contents* change; call ``_apply_filter`` when
        only the query does.
        """
        blocked = self._list.blockSignals(True)
        try:
            self._list.clear()
            self._card_map.clear()
            self._facets.clear()
            self._headers.clear()
            keep: QListWidgetItem | None = None
            entries = self._ws.items(self._kind)
            saved = set(SettingsManager.instance().favorite_strategies(self._kind.value))
            self._favorite_ids = {e.id for e in entries if e.id in saved}
            favorites = [e for e in entries if e.id in self._favorite_ids]
            others = [e for e in entries if e.id not in self._favorite_ids]
            sections = (
                [(FAVORITES_HEADING, favorites), (others_heading(self._kind), others)] if favorites else [("", others)]
            )
            for heading, section in sections:
                if heading:
                    self._add_header(heading)
                for entry in section:
                    summary = self.summarize(entry)
                    card = self._make_card(entry.id, summary)
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, entry.id)
                    item.setData(_HAYSTACK_ROLE, summary_haystack(summary))
                    self._fit_row(item, card)
                    self._list.addItem(item)
                    self._list.setItemWidget(item, card)
                    self._card_map.append((item, card))
                    self._facets[entry.id] = summary_facets(summary)
                    if entry.id == self._selected_id:
                        keep = item
                        card.set_selected(True)
            # Reselected inside the blocked region: every item here is a new
            # QListWidgetItem, so Qt would report a selection change that did not
            # happen, and the slot would reload the editor mid-keystroke.
            if keep is not None:
                self._list.setCurrentItem(keep)
        finally:
            self._list.blockSignals(blocked)
        self._filter_bar.set_counts(self._facets.values())
        self._apply_filter()
        if keep is None:
            # Actually gone — deleted, or never there. Detach the editor. An entry
            # that is merely filtered out of view stays loaded: typing in the
            # filter box must not empty the pane being edited.
            self._selected_id = None
            self._load_selection()

    def _apply_filter(self) -> None:
        """Show or hide each existing row against the query. No widgets are built.

        Signals stay blocked throughout: hiding the current row can make Qt move
        the current item, and that would reload the detail pane mid-keystroke —
        the defect ``tests/test_strategy_library_filter.py`` exists for. The
        selected row is restored afterwards even when the query hides it, so the
        editor keeps the entry it was given.
        """
        query = self._filter_bar.query()
        flt = self._filter_bar.selection()
        blocked = self._list.blockSignals(True)
        try:
            shown: set[str] = set()
            for item, _card in self._card_map:
                uuid = item.data(Qt.ItemDataRole.UserRole)
                facets = self._facets.get(uuid, StrategyFacets())
                visible = matches_query(item.data(_HAYSTACK_ROLE) or "", query) and facets_match(facets, flt)
                item.setHidden(not visible)
                if visible:
                    shown.add(uuid)
            if self._headers:
                # A section the filter has emptied loses its header too, or it would sit
                # over nothing — or, worse, appear to head the section below it.
                favorites, others = self._headers
                favorites.setHidden(not (shown & self._favorite_ids))
                others.setHidden(not (shown - self._favorite_ids))
            current = self._current_item()
            if current is not None and self._list.currentItem() is not current:
                self._list.setCurrentItem(current)
        finally:
            self._list.blockSignals(blocked)

    def _current_item(self) -> QListWidgetItem | None:
        """The row for ``_selected_id``, visible or not."""
        for item, _card in self._card_map:
            if item.data(Qt.ItemDataRole.UserRole) == self._selected_id:
                return item
        return None

    def clear_filter(self) -> None:
        """Drop every facet and empty the search box, so every row is visible again."""
        self._filter_bar.clear()

    # -- sections and favorites -----------------------------------------

    def _add_header(self, text: str) -> None:
        """A section header row: not selectable, not focusable, skipped by the arrow keys."""
        item = QListWidgetItem()
        item.setData(_HEADING_ROLE, text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        heading = RuleHeading(text)
        item.setSizeHint(heading.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, heading)
        self._headers.append(item)

    def _fit_row(self, item: QListWidgetItem, card: StrategyCard) -> None:
        """Size *item* to what *card* needs at the column's real width.

        Not ``card.sizeHint()``: its height wraps the name at whatever width Qt's own
        guess for a word-wrapped label picks, not at the column's. A name that fits on
        one line was still given a two-line row, and the card spread the slack between
        its lines -- so an edit that turned the source pill into the wider "Built-in
        (modified)", really wrapping the name, visibly tightened the card's spacing.

        The width asked for is only the card's minimum: a list row fills the viewport
        anyway, and asking for more is what gives the list a horizontal scrollbar.
        """
        width = self._list.viewport().width() - 2 * (self._list.spacing() + _ROW_PAD)
        height = card.heightForWidth(width)
        item.setSizeHint(QSize(card.minimumSizeHint().width(), height if height > 0 else card.sizeHint().height()))

    def _refit_rows(self) -> None:
        for item, card in self._card_map:
            self._fit_row(item, card)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._list.viewport() and event.type() == QEvent.Type.Resize:
            if event.size().width() != event.oldSize().width():
                self._refit_rows()
        return super().eventFilter(obj, event)

    def _make_card(self, uuid: str, summary: StrategySummary) -> StrategyCard:
        card = StrategyCard(summary, favorite=uuid in self._favorite_ids)
        card.favorite_toggled.connect(lambda wanted, uuid=uuid: self.set_favorite(uuid, wanted))
        return card

    def is_favorite(self, uuid: str) -> bool:
        return uuid in self._favorite_ids

    def set_favorite(self, uuid: str, wanted: bool) -> None:
        """Star or unstar ``uuid``, saving at once, and move its card to the right section.

        Saved now, not on Save: a favorite is a preference about the library, not a
        change to it, and so it is not something Cancel should take back.
        """
        settings = SettingsManager.instance()
        if settings.is_favorite_strategy(self._kind.value, uuid) != wanted:
            settings.toggle_favorite_strategy(self._kind.value, uuid)
        # Safe inside the star's own click: clearing the list hands each card to
        # deleteLater, so the button being clicked outlives this call.
        self.rebuild_list()

    def focus_editor(self) -> None:
        """Move focus into the detail pane's main field, where the editor has one.

        Only the Player tab's does — its DSL box. The other editors have no single field
        a visitor has obviously come for, so focus stays where it is.
        """
        if isinstance(self._editor, PlayerStrategyEditor):
            self._editor.focus_dsl()

    def select(self, uuid: str | None) -> None:
        """Select ``uuid`` if it is in the list, loading it into the detail pane."""
        self._selected_id = uuid
        for item, card in self._card_map:
            chosen = item.data(Qt.ItemDataRole.UserRole) == uuid
            card.set_selected(chosen)
            if chosen:
                self._list.setCurrentItem(item)
        self._load_selection()

    def _on_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        for item, card in self._card_map:
            if item is previous:
                card.set_selected(False)
            elif item is current:
                card.set_selected(True)
        self._selected_id = None if current is None else current.data(Qt.ItemDataRole.UserRole)
        self._load_selection()

    def _refresh_selected_card(self) -> None:
        """Redraw just the selected card, so a rename shows without a full rebuild.

        A rebuild on every keystroke would be wasteful and would re-sort the list
        under the user mid-word; the alphabetical position catches up the next
        time the list is rebuilt for a reason of its own.
        """
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            return
        for item, _card in list(self._card_map):
            if item.data(Qt.ItemDataRole.UserRole) != self._selected_id:
                continue
            summary = self.summarize(entry)
            card = self._make_card(entry.id, summary)
            card.set_selected(True)
            self._fit_row(item, card)
            item.setData(_HAYSTACK_ROLE, summary_haystack(summary))
            # The facets follow the edit, and so do the counts; whether the row still
            # passes the filter is left for the next filter change, as the name is.
            self._facets[entry.id] = summary_facets(summary)
            self._filter_bar.set_counts(self._facets.values())
            self._list.setItemWidget(item, card)
            self._card_map = [(i, card if i is item else c) for i, c in self._card_map]
            return

    # -- the detail pane ------------------------------------------------

    def _load_selection(self) -> None:
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            self._editor.clear()
            self._about.clear()
        else:
            self._about.load(entry)
            if self._kind is StrategyKind.DUEL:
                self._editor.load(entry, self._ws.players)
            else:
                self._editor.load(entry)
        self._about.setVisible(entry is not None)
        self._scroll.setVisible(entry is not None)
        self._empty_lbl.setVisible(entry is None)
        self._dup_btn.setEnabled(entry is not None)
        self._del_btn.setEnabled(entry is not None)
        if entry is not None:
            self._update_labels()

    def _update_labels(self) -> None:
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            return
        # The working set's status, not the file's origin: an edit made a moment ago
        # already makes a built-in revertible, before anything is written (#124).
        status = self._ws.status(self._kind, entry.id)
        revert = status is Origin.SHIPPED_OVERRIDE
        built_in = status is Origin.SHIPPED
        self._del_btn.setText(_REVERT_TEXT if revert else _DELETE_TEXT)
        self._del_btn.set_icon_name("revert" if revert else "trash")
        # A revert is not a caution: the shipped version comes back, so there is nothing
        # to lose — which is exactly the distinction the two verbs exist to draw.
        self._del_btn.set_action_style(ActionStyle.TERTIARY if revert else ActionStyle.CAUTION)
        self._del_btn.setEnabled(not built_in)
        self._del_btn.setToolTip(_BUILT_IN_DELETE_TIP if built_in else "")
        if revert:
            note = "Your copy of a strategy BartleBot ships — reverting restores the original."
        elif built_in:
            note = "Ships with BartleBot — editing it saves your own copy."
        else:
            note = ""
        self._about.set_notes(self._ws.usage(entry.id).phrase(), note)

    def _on_edited(self) -> None:
        self._refresh_selected_card()
        self._update_labels()

    # -- create / duplicate / remove ------------------------------------

    def _on_new(self) -> None:
        made = self._ws.create(self._kind, NEW_NAMES[self._kind])
        self._selected_id = made.id
        self.rebuild_list()
        self.select(made.id)
        self.structure_changed.emit()

    def _on_duplicate(self) -> None:
        if self._selected_id is None:
            return
        copy = self._ws.duplicate(self._kind, self._selected_id)
        if copy is None:
            return
        self._selected_id = copy.id
        self.rebuild_list()
        self.select(copy.id)
        self.structure_changed.emit()

    def _on_delete(self) -> None:
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            return
        status = self._ws.status(self._kind, entry.id)
        if status is Origin.SHIPPED:
            return  # nothing to delete or revert; the button is disabled for this
        revert = status is Origin.SHIPPED_OVERRIDE
        verb = "Revert" if revert else "Delete"
        if revert:
            # No usage warning: the id stays, so everything linking it keeps working.
            detail = f"“{entry.name}” will go back to the version BartleBot ships."
        else:
            detail = f"“{entry.name}” will be removed when you save."
            usage = self._ws.usage(entry.id)
            if usage.total:
                detail += f"\n\n{usage.phrase()} — those will fall back to the built-in default."
        answer = QMessageBox.question(
            self,
            f"{verb} strategy?",
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        if revert:
            # The shipped version replaces the live object here and now, so the entry
            # stays listed and selected; the editor must reload, since it holds the
            # object that was just replaced.
            self._ws.revert(self._kind, entry.id)
            self.rebuild_list()
            self.select(entry.id)
        else:
            self._ws.remove(self._kind, entry.id)
            self._selected_id = None
            self.rebuild_list()
        self.structure_changed.emit()

    # -- cross-tab refresh ----------------------------------------------

    def refresh_pcs_list(self) -> None:
        """Re-read the Player Strategies the duel editor offers. Duel tab only."""
        if self._kind is StrategyKind.DUEL:
            self._editor.set_pcs_list(self._ws.players)

    def set_player_color_keys(self, keys: list[int | None], labels: list[str] | None = None) -> None:
        """Colour the assignment cards' slot pills. Duel tab only; a no-op elsewhere."""
        if self._kind is StrategyKind.DUEL:
            self._editor.set_player_color_keys(keys, labels)

    @property
    def about(self) -> AboutSection:
        """This tab's About section. Public because the entry's name and description are
        edited there now, and a caller checking what a tab says has to be able to ask.
        """
        return self._about

    # -- theme ----------------------------------------------------------

    def _apply_style(self) -> None:
        # No border on the list itself. It holds a column of cards, each of which draws
        # its own edge when it is selected, and the splitter handle already says where
        # this pane stops — a box around a column of boxes is one line too many.
        self._list.setStyleSheet(
            f"QListWidget {{ border: none; background: {THEME.bg}; }}"
            f"QListWidget::item {{ background: transparent; border: none; padding: {_ROW_PAD}px; }}"
            f"QListWidget::item:selected {{ background: transparent; border: none; }}"
            f"QListWidget::item:hover {{ background: transparent; border: none; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()


class CombatStrategyDialog(QDialog):
    """The combat edit surface. See the module docstring for the two modes."""

    def __init__(
        self,
        ws: StrategyWorkingSet,
        kind: StrategyKind = StrategyKind.DUEL,
        mode: DialogMode = DialogMode.MANAGE,
        current_id: str | None = None,
        allow_none: bool = True,
        none_label: str = NONE_LABEL,
        player_color_keys: list[int | None] | None = None,
        player_account_labels: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._ws = ws
        self._mode = mode
        self._pick_kind = kind
        # What falling through to nothing means is a property of the binding's
        # level, not of this dialog — "(built-in default)" at the top, "(inherit
        # from default)" at a fight — so the caller names it.
        self._allow_none = allow_none
        self._none_label = none_label
        self._selected_id: str | None = current_id
        self._used_none = False
        self.setWindowTitle("Pick a Combat Strategy" if mode is DialogMode.PICK else "Combat Strategies")
        self.setMinimumSize(880, 560)
        # Wider than the minimum so the card sidebar isn't fighting the detail
        # pane for space by default — see issue #44 — and wide enough that a card
        # carrying the longest source pill, "Built-in (modified)", fits the column.
        self.resize(1200, 720)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._tabs = QTabWidget()
        self._pages: dict[StrategyKind, _KindPage] = {}
        for k, label in KIND_LABELS.items():
            page = _KindPage(k, ws)
            page.structure_changed.connect(self._on_structure_changed)
            page.promote_requested.connect(self._on_promote_requested)
            page.open_pcs_requested.connect(self._on_open_pcs_requested)
            self._pages[k] = page
            self._tabs.addTab(page, label)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        # Which account is behind each player slot, if anything is hooked. Resolved by the
        # caller rather than read here: this dialog knows nothing about clients, and the
        # Combat tab that opens it already has the ``ClientManager`` in reach. Empty is
        # the normal case — a duel is usually authored with nothing running — and the
        # pills simply go uncoloured. See gui/widgets/player_pills.py.
        self._pages[StrategyKind.DUEL].set_player_color_keys(player_color_keys or [], player_account_labels or [])

        self._tabs.setCurrentWidget(self._pages[kind])
        self._pages[kind].select(current_id)

        layout.addLayout(self._build_buttons())

    # -- buttons --------------------------------------------------------

    def _build_buttons(self) -> QHBoxLayout:
        # This footer was the worked example the shared helper was drawn from, and it is
        # built through it now so there is exactly one implementation. The weights are
        # unchanged: the commit is what this dialog exists to reach, so it is the one
        # PRIMARY on the screen; falling back to the built-in default is a real
        # alternative to it and takes the outline; Cancel is neither. See issues #47/#89.
        extras: list[ActionButton] = []
        if self._mode is DialogMode.PICK and self._allow_none:
            self._none_btn = ActionButton("revert", f"Use {self._none_label}", style=ActionStyle.SECONDARY, fill=False)
            self._none_btn.setToolTip("Fight with BartleBot's built-in DSL instead of a saved strategy")
            self._none_btn.clicked.connect(self._on_use_none)
            extras.append(self._none_btn)
        # Both spellings of the same act: commit the working set. Use also
        # returns the selection — see the module docstring.
        footer = dialog_footer(
            self,
            commit_text="Use this" if self._mode is DialogMode.PICK else "Save",
            on_commit=self._on_commit,
            extras=extras,
        )
        self._commit_btn = footer.commit
        return footer.layout

    def _on_use_none(self) -> None:
        self._used_none = True
        self._selected_id = None
        self._commit_and_accept()

    def _on_commit(self) -> None:
        page = self._tabs.currentWidget()
        if self._mode is DialogMode.PICK:
            source = self._pages[self._pick_kind]
            # Whichever tab is showing, a pick always returns the kind that was
            # asked for: a Player Strategy created while picking a duel is not
            # an answer to the question the caller asked.
            self._selected_id = source.selected_id()
            if self._selected_id is None and not self._allow_none:
                QMessageBox.information(
                    self,
                    "Nothing selected",
                    f"Choose a strategy on the {KIND_LABELS[self._pick_kind]} tab, or cancel.",
                )
                return
        elif isinstance(page, _KindPage):
            self._selected_id = page.selected_id()
        self._commit_and_accept()

    def _commit_and_accept(self) -> None:
        self._ws.commit()
        if self._ws.is_dirty:
            # commit() logs and skips a failed write, leaving the entry dirty.
            # Closing anyway would lose it silently.
            QMessageBox.warning(
                self,
                "Some changes were not saved",
                "One or more strategies could not be written to disk. "
                "The dialog is staying open so nothing is lost — see the log for details.",
            )
            return
        self.accept()

    # -- results --------------------------------------------------------

    def selected_id(self) -> str | None:
        """The picked id. ``None`` means the built-in default."""
        return self._selected_id

    def used_built_in_default(self) -> bool:
        """Whether the user chose the built-in default rather than a strategy.

        Distinguishes "no strategy, deliberately" from ``selected_id() is None``
        after a Cancel, which the caller must not act on at all.
        """
        return self._used_none

    # -- cross-tab plumbing ---------------------------------------------

    def _on_structure_changed(self) -> None:
        self._pages[StrategyKind.DUEL].refresh_pcs_list()

    def _on_tab_changed(self, _index: int) -> None:
        # A Player Strategy renamed on its own tab has to show its new name in
        # every assignment selector, and this is the cheapest honest moment.
        self._pages[StrategyKind.DUEL].refresh_pcs_list()
        self._pages[StrategyKind.DUEL].rebuild_list()

    def _on_promote_requested(self, row) -> None:
        """Turn one assignment row's custom DSL into a named Player Strategy."""
        assignment = row.assignment()
        dsl = assignment.dsl.strip()
        if not dsl:
            return
        suggested = self._ws.unique_name(StrategyKind.PLAYER, "New Player Strategy")
        name, ok = QInputDialog.getText(self, "Save as Player Strategy", "Name:", text=suggested)
        if not ok or not name.strip():
            return
        pcs = self._ws.create(StrategyKind.PLAYER, name.strip())
        pcs.dsl = assignment.dsl
        pcs.schools = list(assignment.schools)
        self._pages[StrategyKind.PLAYER].rebuild_list()
        self._pages[StrategyKind.DUEL].refresh_pcs_list()
        row.link_to(pcs.id)

    def _on_open_pcs_requested(self, pcs_id: str) -> None:
        """Show one Player Strategy on its own tab — the Duel tab's pencil.

        The filter is cleared first: ``_apply_filter`` keeps a selected row selected
        even when the query hides it, so a stale query would load the strategy into the
        editor with no visible card saying which one it is. Nothing is saved or lost on
        the way — both tabs edit the one working set.

        Focus goes to the DSL box last: switching tabs hands it to the page's first
        focusable child, the filter, and the DSL is what the pencil promised.
        """
        page = self._pages[StrategyKind.PLAYER]
        page.clear_filter()
        self._tabs.setCurrentWidget(page)
        page.select(pcs_id)
        page.focus_editor()

    # -- closing --------------------------------------------------------

    def reject(self) -> None:
        """Cancel. Confirms first if there is anything to lose.

        There is nothing to roll back — dropping the working set *is* the whole
        of Cancel. The prompt exists only because the count is the one thing the
        user cannot see from the cards.
        """
        changes = self._ws.changes()
        if changes:
            answer = QMessageBox.question(
                self,
                "Discard changes?",
                f"{changes.total} change{'s' if changes.total != 1 else ''} will be lost.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is not QMessageBox.StandardButton.Discard:
                return
        super().reject()
