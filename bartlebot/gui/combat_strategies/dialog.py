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
three modes, one per way in (#118, #123):

``LIBRARY``  ``[Cancel] [Save]`` — **File → Combat Strategies**. The Duel, Player
             and Deck tabs, library entries only. No profile section, even with a
             bot loaded.
``PROFILE``  ``[Cancel] [Save]`` — the ✏ on a Combat tab card. The Duel and Player
             tabs, with the active combat profile's own strategies in a section at
             the top. New and Duplicate create *in the profile*; library entries stay
             editable too, and the Save confirmation says where else an edit reaches.
``PICK``     ``[Use built-in default] [Cancel] [Use this]`` — 🔍 on a card, a fight's
             pill, the add-card's "Select existing duel strategy…", and a deck slot's
             "Load from library". Only the tab asked for, and **select-only**: no New,
             Duplicate or Delete, and the detail pane is read-only. Before #123 Pick
             edited everything, which made 🔍 and ✏ the same button.

One rule covers the edge: **Cancel discards every edit; Save and Use both commit
every edit**, and Use additionally returns the selection. In Pick there is nothing
to commit but the selection.

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

TWO PLACES A STRATEGY CAN LIVE (#118)
-------------------------------------

A strategy belongs to the library, which every bot and profile sees, or to one
combat profile. Users who don't realise there are two places struggle, so the
difference is drawn everywhere it can be: the profile's section is headed with its
full name, each of its cards wears a pill with that name in its own color (not the
error red), an empty section says what it is for, and the About pane says "In
profile 'Solo' only" beside **Add to library**. Creating locally and publishing
separately is the shape design tools settled on — Figma's components, Illustrator's
swatches — and it keeps the one-off from ever cluttering the shared list.

TAGS, FILTERS AND FAVORITES (#125)
----------------------------------

Each tab has a :class:`~bartlebot.gui.combat_strategies.filter_bar.StrategyFilterBar`
across its top: facet pickers and the search box, with the selection as chips below.
:mod:`bartlebot.strategy_tags` holds the vocabulary and the matching rule.

Favorites are the one thing here that is *not* inside the Save/Cancel boundary. A
star is saved to the user's settings on the click, because it is a preference about
the library rather than a change to it. A favorite moves its card into a "★
Favorites" section. Each entry is in exactly one section, never two: selection is
restored by id, so an entry drawn twice would make it ambiguous which row is
selected. Favorites are library-only, so a profile entry has no star. The headers
only appear once there is more than one section, and a section that the filter
empties hides its header.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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

from bartlebot.combat_profile import profile_players_of
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
    name_list,
    summarize_deck,
    summarize_duel,
    summarize_player,
)
from bartlebot.strategy_tags import (
    KIND_GROUPS,
    PROFILE_SOURCE,
    STORED_GROUPS,
    StrategyFacets,
    facets_match,
    normalize_strategy_tags,
)
from bartlebot.strategy_workset import PROFILE_KINDS, Conflict, SharedUse, StrategyWorkingSet

logger = logging.getLogger(__name__)


class DialogMode(Enum):
    """Which way in the dialog was opened by — see the module docstring for each."""

    LIBRARY = "library"
    PROFILE = "profile"
    PICK = "pick"


#: Tab order, and the label each kind carries everywhere in this dialog. Duel first:
#: it is what a bot references, and what the Change… button opens onto.
KIND_LABELS = {
    StrategyKind.DUEL: "Duel Strategies",
    StrategyKind.PLAYER: "Player Strategies",
    StrategyKind.DECK: "Deck Presets",
}


def tabs_for(mode: DialogMode, kind: StrategyKind) -> tuple[StrategyKind, ...]:
    """The tabs a way in shows, in order — and no tab its row in the table doesn't list.

    The library view has all three. The profile editor has Duel and Player, the two
    kinds a profile can hold. Pick has only the kind it was asked for: a Player Strategy
    tab while picking a duel is a place to edit things, and Pick edits nothing.
    """
    if mode is DialogMode.PICK:
        return (kind,)
    if mode is DialogMode.PROFILE:
        return (StrategyKind.DUEL, StrategyKind.PLAYER)
    return tuple(KIND_LABELS)


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

#: The section headers. Favorites and the library's are shown only once there is more
#: than one section; the profile's is its full name.
FAVORITES_HEADING = "★ Favorites"
LIBRARY_HEADING = "Library"

#: The one muted line an empty profile section shows (#118).
PROFILE_EMPTY_NOTE = "Strategies made here belong to this profile. Use Add to library to share one."
#: What the profile section says for a loaded bot that has no combat profile yet.
NO_PROFILE_NOTE = "This bot has no combat profile yet."
CREATE_PROFILE_TEXT = "Create one"
#: The About pane's verb for a profile entry.
ADD_TO_LIBRARY_TEXT = "Add to library"

#: Origin.SHIPPED_OVERRIDE means the shipped file reappears underneath, so the
#: honest verb is "revert" — see :class:`bartlebot.strategy_store.Origin`.
_REVERT_TEXT = "Revert to default"
_DELETE_TEXT = "Delete"
#: Why Delete is grayed out on an unedited Official one: removing only touches the user
#: root, so the entry would be back on the next open (#124).
_BUILT_IN_DELETE_TIP = "Official strategies can't be deleted. Duplicate one to make a copy of your own."
#: The About pane's note on an override whose Official strategy has changed since (#169),
#: beside the Revert button it points at.
OFFICIAL_UPDATED_NOTE = (
    "The Official version has changed since you made your copy. "
    "Revert to default to use the new one; your changes are discarded."
)
#: Why New and Duplicate are grayed out in the profile editor before a profile exists.
_NO_PROFILE_TIP = "Create a combat profile for this bot first"
#: What an entry from a file in a newer format says, and why it can't be edited or deleted.
NEWER_FORMAT_NOTE = "Saved by a newer version of BartleBot or BartleBuddy. Update BartleBot to edit it."

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
    if summary.profile_name is not None:
        source = f"{PROFILE_SOURCE} {summary.profile_name}"
    else:
        source = SOURCE_LABELS[summary.source] if summary.source is not None else ""
    tags = " ".join(summary.tags)
    return f"{summary.name} {summary.description} {summary.subtitle_line()} {source} {tags}".lower()


def summary_facets(summary: StrategySummary) -> StrategyFacets:
    """What the facet filter asks of an entry: its stored tags plus its derived source."""
    tags = set(summary.tags)
    if summary.profile_name is not None:
        tags.add(PROFILE_SOURCE)
    elif summary.source is not None:
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


def move_confirmation(entry_name: str, moving: list[str]) -> str:
    """What Add to library says before moving a duel that takes player strategies with it.

    Numerals throughout, never "2" beside "three" (#118): *moving* are the names of the
    profile's player strategies the duel uses.
    """
    count = len(moving)
    noun = "player strategy" if count == 1 else "player strategies"
    names = name_list([f"'{n}'" for n in moving], limit=len(moving))
    return (
        f"'{entry_name}' uses {count} {noun} from this profile: {names}. All {count + 1} will be moved to the library."
    )


def shared_edit_message(uses: list[SharedUse], *, other: bool) -> str:
    """The Save confirmation's text: every shared use, then what saving means for them."""
    return "\n\n".join(use.sentence(other=other) for use in uses) + "\n\nYour changes will apply there too."


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
    old Strategy Library never said at all: what breaks if this goes, and where the entry
    lives — whether "delete" here means delete or means revert, or that it belongs to one
    combat profile, with **Add to library** beside that line (#118).

    *tag_groups* are the stored tag groups this kind is tagged with — Role, on Player
    Strategies and Deck Presets — one picker row each (#125). Duels pass none.
    """

    changed = pyqtSignal()
    add_to_library_requested = pyqtSignal()

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
        self._usage_lbl.setWordWrap(True)
        apply_muted_color(self._usage_lbl)
        layout.addWidget(self._usage_lbl)

        origin_row = QHBoxLayout()
        origin_row.setContentsMargins(0, 0, 0, 0)
        origin_row.setSpacing(6)
        self._origin_lbl = QLabel(self)
        self._origin_lbl.setWordWrap(True)
        apply_muted_color(self._origin_lbl)
        origin_row.addWidget(self._origin_lbl, 1)
        # Beside the line that says the entry lives in one profile, because that is the
        # sentence it answers.
        self._add_btn = ActionButton("upload", ADD_TO_LIBRARY_TEXT, style=ActionStyle.SECONDARY, fill=False)
        self._add_btn.setToolTip("Move this into the library, where every bot and profile can use it")
        self._add_btn.clicked.connect(self.add_to_library_requested)
        self._add_btn.setVisible(False)
        origin_row.addWidget(self._add_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(origin_row)

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
        self._add_btn.setVisible(False)
        self._suppress = False

    def set_editable(self, editable: bool) -> None:
        """Read-only in Pick, which selects and edits nothing (#123)."""
        self._name_edit.setReadOnly(not editable)
        self._description_edit.setReadOnly(not editable)
        for picker in self._tag_pickers:
            picker.setEnabled(editable)

    def set_notes(self, usage: str, origin: str, *, can_add_to_library: bool = False) -> None:
        """The two muted lines — what uses this entry, and where it lives — and whether
        **Add to library** sits beside the second."""
        self._usage_lbl.setText(usage)
        self._origin_lbl.setText(origin)
        self._origin_lbl.setVisible(bool(origin))
        self._add_btn.setVisible(can_add_to_library)

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

    @property
    def add_to_library_button(self) -> ActionButton:
        """Add to library. Public so a test can press it and see whether it is offered."""
        return self._add_btn

    @property
    def name_edit(self) -> QLineEdit:
        """The name field. Public so a test can check it is read-only in Pick."""
        return self._name_edit

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

    *editable* is False in Pick: the button row is gone and the detail pane is
    read-only (#123). *show_profile* draws the active profile's own entries in a
    section at the top, and makes New and Duplicate create there (#118).
    """

    #: The list's structure changed (created, duplicated, removed, moved) — as opposed
    #: to a field edit, which only ever redraws one card.
    structure_changed = pyqtSignal()
    promote_requested = pyqtSignal(object)
    #: The Duel tab's pencil asked to edit a Player Strategy, by id.
    open_pcs_requested = pyqtSignal(str)
    #: The profile section's "Create one", on a bot with no combat profile yet.
    create_profile_requested = pyqtSignal()

    def __init__(
        self,
        kind: StrategyKind,
        ws: StrategyWorkingSet,
        parent=None,
        *,
        editable: bool = True,
        show_profile: bool = False,
    ):
        super().__init__(parent)
        self._kind = kind
        self._ws = ws
        self._editable = editable
        self._show_profile = show_profile and kind in PROFILE_KINDS
        self._selected_id: str | None = None
        self._card_map: list[tuple[QListWidgetItem, StrategyCard]] = []
        #: Per entry id: what the facet filter asks of it, and which section it is in.
        self._facets: dict[str, StrategyFacets] = {}
        self._section_of: dict[str, str] = {}
        self._favorite_ids: set[str] = set()
        #: Section header rows, top to bottom, each with the section it heads.
        self._headers: list[tuple[QListWidgetItem, str]] = []
        #: The profile section's note row — empty section, or no profile yet — if drawn.
        self._note_item: QListWidgetItem | None = None
        self._note_widget: QWidget | None = None

        layout = QVBoxLayout(self)
        # A real inset on all four sides. The page used to run edge to edge inside the tab
        # pane, so the filter box, the cards and the fields all sat directly against its
        # border — which is most of what made a screen of otherwise ordinary controls read
        # as crowded. A line with nothing beside it reads as a wall.
        layout.setContentsMargins(_PAGE_INSET, _PAGE_INSET, _PAGE_INSET, _PAGE_INSET)
        layout.setSpacing(6)

        # Across the whole tab, not over the card list alone: four pickers and a search
        # box need more width than the list has, and the chips need a row of their own.
        self._filter_bar = StrategyFilterBar(kind, self, profile=self._show_profile)
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
        # A column of cards scrolls one way. _fit_row keeps every row inside the
        # viewport, so this only says so; it never hides content that had a bar.
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.currentItemChanged.connect(self._on_item_changed)
        # Rows are sized for the width the column actually has; see _fit_row.
        self._list.viewport().installEventFilter(self)
        box.addWidget(self._list, 1)

        # The same three buttons in the library view and the profile editor; what differs
        # is where New and Duplicate put their entry. None at all in Pick (#123).
        self._button_row = QWidget(panel)
        buttons = QHBoxLayout(self._button_row)
        buttons.setContentsMargins(0, 0, 0, 0)
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
        box.addWidget(self._button_row)
        self._button_row.setVisible(self._editable)
        return panel

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(_SPLITTER_GUTTER, 0, 0, 0)
        box.setSpacing(6)

        self._about = AboutSection(tag_groups=tuple(g for g in KIND_GROUPS[self._kind] if g in STORED_GROUPS))
        self._about.changed.connect(self._on_edited)
        self._about.add_to_library_requested.connect(self._on_add_to_library)
        self._about.set_editable(self._editable)
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
        self._editor.set_editable(self._editable)

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

        self._empty_lbl = QLabel(
            "Select a strategy on the left, or press + New." if self._editable else "Select a strategy on the left."
        )
        apply_muted_color(self._empty_lbl)
        box.addWidget(self._empty_lbl)
        return panel

    # -- reading --------------------------------------------------------

    def kind(self) -> StrategyKind:
        return self._kind

    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def editable(self) -> bool:
        """False in Pick, which selects and edits nothing."""
        return self._editable

    @property
    def creates_in_profile(self) -> bool:
        """Whether New and Duplicate put their entry in the profile rather than the library."""
        return self._show_profile

    @property
    def new_button(self) -> ActionButton:
        return self._new_btn

    @property
    def duplicate_button(self) -> ActionButton:
        return self._dup_btn

    @property
    def delete_button(self) -> ActionButton:
        return self._del_btn

    @property
    def button_row(self) -> QWidget:
        """New, Duplicate and Delete. Public so a test can see Pick has none of them."""
        return self._button_row

    @property
    def editor(self):
        """The detail pane's editor. Public so a test can check it is read-only in Pick."""
        return self._editor

    def summarize(self, item) -> StrategySummary:
        if self._kind is StrategyKind.DUEL:
            # Resolved the way a run resolves them, profile first — so a profile duel
            # names its own player strategies, and a library duel a profile one only when
            # a hand-edited file broke the rule, which it then shows rather than hides.
            summary = summarize_duel(item, self._ws.lookup().players, name=item.name)
        elif self._kind is StrategyKind.PLAYER:
            summary = summarize_player(item)
        else:
            summary = summarize_deck(item)
        if self._ws.is_profile_entry(self._kind, item.id):
            return replace(summary, profile_name=self._ws.profile.name)
        return replace(
            summary,
            source=self._ws.status(self._kind, item.id),
            official_updated=self._ws.official_updated(self._kind, item.id),
        )

    @property
    def filter_bar(self) -> StrategyFilterBar:
        """The tab's facet pickers and search box."""
        return self._filter_bar

    def section_headings(self) -> list[str]:
        """The visible section headers, top to bottom. Empty when there is one section."""
        return [h.data(_HEADING_ROLE) for h, _key in self._headers if not h.isHidden()]

    def visible_ids(self) -> list[str]:
        """The ids of the cards the filter leaves showing, top to bottom."""
        return [i.data(Qt.ItemDataRole.UserRole) for i, _c in self._card_map if not i.isHidden()]

    def ids_in_section(self, section: str) -> list[str]:
        """The ids drawn in *section* ("profile", "favorites" or "library"), top to bottom."""
        return [
            i.data(Qt.ItemDataRole.UserRole)
            for i, _c in self._card_map
            if self._section_of.get(i.data(Qt.ItemDataRole.UserRole)) == section
        ]

    def note_text(self) -> str:
        """What the profile section's note row says, or empty when it has none."""
        if self._note_widget is None or self._note_item is None or self._note_item.isHidden():
            return ""
        label = self._note_widget.findChild(QLabel)
        return label.text() if label is not None else ""

    def card_for(self, uuid: str) -> StrategyCard | None:
        """The card drawn for ``uuid``, or None. Public so a test can read what a card says."""
        for item, card in self._card_map:
            if item.data(Qt.ItemDataRole.UserRole) == uuid:
                return card
        return None

    # -- the card list --------------------------------------------------

    def _sections(self) -> list[tuple[str, str, list]]:
        """``(key, heading, entries)`` per section, top to bottom; a heading may be empty.

        The profile first, then ★ Favorites, then the library (#118). Favorites are
        library-only, and a library entry the profile shadows is not drawn twice: the
        profile's copy is the one every lookup finds.
        """
        sections: list[tuple[str, str, list]] = []
        own = self._ws.profile_items(self._kind) if self._show_profile else []
        if self._show_profile:
            heading = self._ws.profile.name if self._ws.profile is not None else "Combat profile"
            sections.append(("profile", heading, own))
        shadowed = {e.id for e in own}
        library = [e for e in self._ws.items(self._kind) if e.id not in shadowed]
        saved = set(SettingsManager.instance().favorite_strategies(self._kind.value))
        self._favorite_ids = {e.id for e in library if e.id in saved}
        favorites = [e for e in library if e.id in self._favorite_ids]
        others = [e for e in library if e.id not in self._favorite_ids]
        if favorites:
            sections.append(("favorites", FAVORITES_HEADING, favorites))
            sections.append(("library", others_heading(self._kind), others))
        else:
            sections.append(("library", LIBRARY_HEADING if self._show_profile else "", others))
        return sections

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
            self._section_of.clear()
            self._headers.clear()
            self._note_item = None
            self._note_widget = None
            keep: QListWidgetItem | None = None
            for key, heading, section in self._sections():
                if heading:
                    self._add_header(heading, key)
                if key == "profile" and not section:
                    self._add_profile_note()
                for entry in section:
                    summary = self.summarize(entry)
                    card = self._make_card(entry.id, summary, favorable=key != "profile")
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, entry.id)
                    item.setData(_HAYSTACK_ROLE, summary_haystack(summary))
                    self._fit_row(item, card)
                    self._list.addItem(item)
                    self._list.setItemWidget(item, card)
                    self._card_map.append((item, card))
                    self._facets[entry.id] = summary_facets(summary)
                    self._section_of[entry.id] = key
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
        self._update_create_buttons()
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
            shown_sections = {self._section_of[uuid] for uuid in shown}
            for header, key in self._headers:
                # A section the filter has emptied loses its header too, or it would sit
                # over nothing — or, worse, appear to head the section below it. The
                # profile's stays when it is empty to begin with: its note row is under it.
                keep_header = key in shown_sections or (key == "profile" and self._note_item is not None)
                header.setHidden(not keep_header)
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

    def _add_header(self, text: str, key: str) -> None:
        """A section header row: not selectable, not focusable, skipped by the arrow keys."""
        item = QListWidgetItem()
        item.setData(_HEADING_ROLE, text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        heading = RuleHeading(text)
        item.setSizeHint(heading.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, heading)
        self._headers.append((item, key))

    def _add_profile_note(self) -> None:
        """The profile section's one row when it holds nothing: what it is for, or an offer.

        With a profile, one muted line saying what the section is for. With none yet, the
        same place says so, and — in the profile editor — offers to make one. Pick shows
        the sentence without the button: it edits nothing.
        """
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(4, 2, 4, 2)
        box.setSpacing(6)
        no_profile = self._ws.profile is None
        label = QLabel(NO_PROFILE_NOTE if no_profile else PROFILE_EMPTY_NOTE, row)
        label.setWordWrap(True)
        apply_muted_color(label)
        box.addWidget(label, 1)
        if no_profile and self._editable:
            button = ActionButton("plus", CREATE_PROFILE_TEXT, style=ActionStyle.SECONDARY, fill=False)
            button.clicked.connect(self.create_profile_requested)
            box.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._fit_row(item, row)
        self._list.addItem(item)
        self._list.setItemWidget(item, row)
        self._note_item = item
        self._note_widget = row

    def _fit_row(self, item: QListWidgetItem, card: QWidget) -> None:
        """Size *item* to what *card* needs at the column's real width.

        Not ``card.sizeHint()``: its height wraps the name at whatever width Qt's own
        guess for a word-wrapped label picks, not at the column's. A name that fits on
        one line was still given a two-line row, and the card spread the slack between
        its lines -- so an edit that turned the source pill into the wider "Official
        (modified)", really wrapping the name, visibly tightened the card's spacing.

        The width asked for is only the card's minimum: a list row fills the viewport
        anyway, and asking for more is what gives the list a horizontal scrollbar.

        And never more than the column, even when the minimum is wider. A list's rows
        are all as wide as its widest, so one card that could not fit -- a long profile
        name in its pill did it -- widened every row past the edge and clipped the stars
        off cards that fit perfectly well. Capped, a card that still cannot fit is
        clipped on its own. The card elides its text so that should not happen; this is
        what keeps it one card's problem if it does.

        The profile section's note row is fitted the same way: its sentence wraps too,
        and a row left at the height it had when the column was wider cuts it off.
        """
        width = self._list.viewport().width() - 2 * (self._list.spacing() + _ROW_PAD)
        height = card.heightForWidth(width)
        item.setSizeHint(
            QSize(min(card.minimumSizeHint().width(), width), height if height > 0 else card.sizeHint().height())
        )

    def _refit_rows(self) -> None:
        for item, card in self._card_map:
            self._fit_row(item, card)
        if self._note_item is not None and self._note_widget is not None:
            self._fit_row(self._note_item, self._note_widget)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._list.viewport() and event.type() == QEvent.Type.Resize:
            if event.size().width() != event.oldSize().width():
                self._refit_rows()
        return super().eventFilter(obj, event)

    def _make_card(self, uuid: str, summary: StrategySummary, *, favorable: bool = True) -> StrategyCard:
        # No star on a profile entry: favorites are a preference about the library.
        card = StrategyCard(summary, favorite=(uuid in self._favorite_ids) if favorable else None)
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
            card = self._make_card(entry.id, summary, favorable=self._section_of.get(entry.id) != "profile")
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
                # What an assignment may link depends on where the duel lives: a library
                # duel may not use a profile's own player strategies (#118).
                self._editor.load(entry, self._ws.players_for_duel(entry.id))
            else:
                self._editor.load(entry)
        # Per entry, not per page: one saved by a newer version is read-only wherever it is.
        editable = self._editable and not self._ws.is_read_only(self._kind, self._selected_id)
        self._about.set_editable(editable)
        self._editor.set_editable(editable)
        self._about.setVisible(entry is not None)
        self._scroll.setVisible(entry is not None)
        self._empty_lbl.setVisible(entry is None)
        self._update_create_buttons()
        self._del_btn.setEnabled(entry is not None)
        if entry is not None:
            self._update_labels()

    def _update_create_buttons(self) -> None:
        """New needs somewhere to put its entry; Duplicate also needs something to copy."""
        room = not self._show_profile or self._ws.profile is not None
        self._new_btn.setEnabled(room)
        # Not one saved by a newer version: the copy would be in that format too, and refused at Save.
        copyable = self._ws.get(self._kind, self._selected_id) is not None
        self._dup_btn.setEnabled(room and copyable and not self._ws.is_read_only(self._kind, self._selected_id))
        tip = "" if room else _NO_PROFILE_TIP
        self._new_btn.setToolTip(tip)
        self._dup_btn.setToolTip(tip)

    def _update_labels(self) -> None:
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None:
            return
        usage = self._ws.usage(entry.id).phrase()
        if self._ws.is_profile_entry(self._kind, entry.id):
            # Never built-in and never reverted: a profile entry's Delete is a plain one.
            self._set_delete_button(revert=False, built_in=False)
            self._about.set_notes(
                usage, f"In profile '{self._ws.profile.name}' only", can_add_to_library=self._editable
            )
            return
        # The working set's status, not the file's origin: an edit made a moment ago
        # already makes a built-in revertible, before anything is written (#124).
        status = self._ws.status(self._kind, entry.id)
        revert = status is Origin.SHIPPED_OVERRIDE
        built_in = status is Origin.SHIPPED
        self._set_delete_button(revert=revert, built_in=built_in)
        if self._ws.is_read_only(self._kind, entry.id):
            self._del_btn.setEnabled(False)
            self._del_btn.setToolTip(NEWER_FORMAT_NOTE)
            self._about.set_notes(usage, NEWER_FORMAT_NOTE)
            return
        if revert and self._ws.official_updated(self._kind, entry.id):
            note = OFFICIAL_UPDATED_NOTE
        elif revert:
            note = "Your copy of a strategy BartleBot ships — reverting restores the original."
        elif built_in:
            note = "Ships with BartleBot — editing it saves your own copy."
        else:
            note = ""
        self._about.set_notes(usage, note)

    def _set_delete_button(self, *, revert: bool, built_in: bool) -> None:
        self._del_btn.setText(_REVERT_TEXT if revert else _DELETE_TEXT)
        self._del_btn.set_icon_name("revert" if revert else "trash")
        # A revert is not a caution: the shipped version comes back, so there is nothing
        # to lose — which is exactly the distinction the two verbs exist to draw.
        self._del_btn.set_action_style(ActionStyle.TERTIARY if revert else ActionStyle.CAUTION)
        self._del_btn.setEnabled(not built_in)
        self._del_btn.setToolTip(_BUILT_IN_DELETE_TIP if built_in else "")

    def _on_edited(self) -> None:
        self._refresh_selected_card()
        self._update_labels()

    # -- create / duplicate / remove / move -----------------------------

    def _on_new(self) -> None:
        made = self._ws.create(self._kind, NEW_NAMES[self._kind], in_profile=self.creates_in_profile)
        self._selected_id = made.id
        self.rebuild_list()
        self.select(made.id)
        self.structure_changed.emit()

    def _on_duplicate(self) -> None:
        if self._selected_id is None:
            return
        # In the profile editor a copy always lands in the profile, a library entry's
        # included: that is how one fight gets something slightly different (#118).
        copy = self._ws.duplicate(self._kind, self._selected_id, in_profile=self.creates_in_profile)
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

    def _on_add_to_library(self) -> None:
        """Move the selected profile entry into the library, keeping its id (#118).

        A duel takes the profile's own player strategies it uses with it — a library duel
        may not link profile content — and says so first, naming them. It is an edit like
        any other: it reaches disk at Save, and Cancel undoes it.
        """
        entry = self._ws.get(self._kind, self._selected_id)
        if entry is None or not self._ws.is_profile_entry(self._kind, entry.id):
            return
        moving = profile_players_of(entry, self._ws.profile) if self._kind is StrategyKind.DUEL else []
        if moving:
            answer = QMessageBox.question(
                self,
                "Add to library?",
                move_confirmation(entry.name, [p.name for p in moving]),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        self._ws.move_to_library(self._kind, entry.id)
        self.rebuild_list()
        self.select(entry.id)
        self.structure_changed.emit()

    # -- cross-tab refresh ----------------------------------------------

    def refresh_pcs_list(self) -> None:
        """Re-read the Player Strategies the duel editor offers. Duel tab only."""
        if self._kind is StrategyKind.DUEL:
            self._editor.set_pcs_list(self._ws.players_for_duel(self._selected_id))

    def set_player_color_keys(self, keys: list[int | None], labels: list[str] | None = None) -> None:
        """Color the assignment cards' slot pills. Duel tab only; a no-op elsewhere."""
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
    """The combat edit surface. See the module docstring for the three modes.

    *bot_name* turns a bot id into its name for the Save confirmation, so another
    profile reads as ``'Solo' (Atlantea)``; without it the profile's name stands alone.
    """

    def __init__(
        self,
        ws: StrategyWorkingSet,
        kind: StrategyKind = StrategyKind.DUEL,
        mode: DialogMode = DialogMode.LIBRARY,
        current_id: str | None = None,
        allow_none: bool = True,
        none_label: str = NONE_LABEL,
        player_color_keys: list[int | None] | None = None,
        player_account_labels: list[str] | None = None,
        bot_name: Callable[[str], str | None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._ws = ws
        self._mode = mode
        self._pick_kind = kind
        self._bot_name = bot_name
        # What falling through to nothing means is a property of the binding's
        # level, not of this dialog — "(built-in default)" at the top, "(inherit
        # from default)" at a fight — so the caller names it.
        self._allow_none = allow_none
        self._none_label = none_label
        self._selected_id: str | None = current_id
        self._selected_was_created = False
        self._used_none = False
        self.setWindowTitle("Pick a Combat Strategy" if mode is DialogMode.PICK else "Combat Strategies")
        self.setMinimumSize(880, 560)
        # Wider than the minimum so the card sidebar isn't fighting the detail
        # pane for space by default — see issue #44 — and wide enough that a card
        # carrying the longest source pill, "Official (modified)", fits the column.
        self.resize(1200, 720)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # The profile section is for the ways in from the Combat tab, and only when a bot
        # is loaded. The library view never shows it (#118).
        show_profile = mode is not DialogMode.LIBRARY and ws.bot_id is not None
        self._tabs = QTabWidget()
        self._pages: dict[StrategyKind, _KindPage] = {}
        for k in tabs_for(mode, kind):
            page = _KindPage(k, ws, editable=mode is not DialogMode.PICK, show_profile=show_profile)
            page.structure_changed.connect(self._on_structure_changed)
            page.promote_requested.connect(self._on_promote_requested)
            page.open_pcs_requested.connect(self._on_open_pcs_requested)
            page.create_profile_requested.connect(self._on_create_profile)
            self._pages[k] = page
            self._tabs.addTab(page, KIND_LABELS[k])
        # The Duel tab's pencil opens a Player Strategy on the Player tab; without one
        # (Pick) it has nowhere to go, so it is grayed rather than a dead button.
        duel = self._pages.get(StrategyKind.DUEL)
        if duel is not None:
            duel.editor.set_can_open_pcs(StrategyKind.PLAYER in self._pages)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        # Which account is behind each player slot, if anything is hooked. Resolved by the
        # caller rather than read here: this dialog knows nothing about clients, and the
        # Combat tab that opens it already has the ``ClientManager`` in reach. Empty is
        # the normal case — a duel is usually authored with nothing running — and the
        # pills simply go uncolored. See gui/widgets/player_pills.py.
        if StrategyKind.DUEL in self._pages:
            self._pages[StrategyKind.DUEL].set_player_color_keys(player_color_keys or [], player_account_labels or [])

        start = self._pages.get(kind) or next(iter(self._pages.values()))
        self._tabs.setCurrentWidget(start)
        start.select(current_id)

        layout.addLayout(self._build_buttons())

    # -- reading --------------------------------------------------------

    @property
    def mode(self) -> DialogMode:
        return self._mode

    def tab_kinds(self) -> list[StrategyKind]:
        """The kinds this dialog has a tab for, in tab order."""
        return list(self._pages)

    def page(self, kind: StrategyKind) -> _KindPage | None:
        """The tab for *kind*, or None when this way in does not show it."""
        return self._pages.get(kind)

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
        if self._mode is DialogMode.PICK:
            source = self._pages[self._pick_kind]
            self._selected_id = source.selected_id()
            if self._selected_id is None and not self._allow_none:
                QMessageBox.information(
                    self,
                    "Nothing selected",
                    f"Choose a strategy on the {KIND_LABELS[self._pick_kind]} tab, or cancel.",
                )
                return
        elif self._mode is DialogMode.PROFILE and self._pick_kind in self._pages:
            # The kind the caller asked about, whichever tab is showing: the Combat tab
            # links the card that opened this to the duel selected here, if it is new.
            self._selected_id = self._pages[self._pick_kind].selected_id()
        else:
            page = self._tabs.currentWidget()
            if isinstance(page, _KindPage):
                self._selected_id = page.selected_id()
        self._commit_and_accept()

    def _commit_and_accept(self) -> None:
        if self._mode is not DialogMode.PICK:
            uses = self._ws.shared_uses(self._bot_name)
            if uses and not self._confirm_shared_edits(uses):
                return
        # Asked before the commit, which baselines everything and so forgets what was new.
        self._selected_was_created = self._ws.was_created(self._pick_kind, self._selected_id)
        self._ws.commit()
        if self._ws.conflicts:
            self._resolve_conflicts(self._ws.conflicts)
            # Writes the copies the user chose to keep; a reloaded entry has nothing to write.
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

    def _resolve_conflicts(self, conflicts: list[Conflict]) -> None:
        """Ask about each write Save refused, then redraw every tab on what was chosen.

        Another program (BartleBuddy, which shares this library) changed the file since
        this dialog loaded it, so writing would have lost its change. The user keeps
        theirs as a copy, the default because nothing is lost, or reloads the other
        program's. A file in a newer format, or a refused delete, can only be reloaded.
        """
        for conflict in conflicts:
            name = f"“{conflict.name}”"
            if conflict.newer_format:
                QMessageBox.information(
                    self,
                    "Saved by a newer version",
                    f"{name} was saved by a newer version of BartleBot or BartleBuddy, so your changes to it "
                    "can't be saved. Update BartleBot to edit it.",
                )
                self._ws.reload(conflict.kind, conflict.uuid)
                continue
            if conflict.deleted:
                QMessageBox.information(
                    self,
                    "Changed by another program",
                    f"{name} was changed by another program while this window was open, so it was not "
                    "removed. Its new version is shown now.",
                )
                self._ws.reload(conflict.kind, conflict.uuid)
                continue
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Changed by another program")
            box.setText(
                f"{name} was changed by another program while this window was open, so your changes to it "
                "were not saved."
            )
            copy = box.addButton("Save mine as a copy", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Use theirs", QMessageBox.ButtonRole.DestructiveRole)
            box.setDefaultButton(copy)
            box.setEscapeButton(copy)
            box.exec()
            if box.clickedButton() is copy:
                self._ws.keep_as_copy(conflict.kind, conflict.uuid)
            else:
                self._ws.reload(conflict.kind, conflict.uuid)
        for page in self._pages.values():
            page.rebuild_list()
            # The editor may hold an object reload just replaced.
            page.select(page.selected_id())

    def _confirm_shared_edits(self, uses: list[SharedUse]) -> bool:
        """Ask once, at Save, before an edit reaches places the user can't see (#118).

        At Save rather than on the first keystroke: typing into a shared entry is not yet
        a decision, and a prompt per field would be one nobody reads.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Save changes to shared strategies?")
        other = self._mode is not DialogMode.LIBRARY and self._ws.profile is not None
        box.setText(shared_edit_message(uses, other=other))
        save = box.addButton("Save anyway", QMessageBox.ButtonRole.AcceptRole)
        back = box.addButton("Go back", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(back)
        box.exec()
        return box.clickedButton() is save

    # -- results --------------------------------------------------------

    def selected_id(self) -> str | None:
        """The picked id. ``None`` means the built-in default."""
        return self._selected_id

    def selected_was_created(self) -> bool:
        """Whether the returned selection was made on this visit — by New, Duplicate or a
        promotion. What lets the Combat tab link the card that opened the profile editor to
        a strategy made for it, and leave the card alone otherwise (#118)."""
        return self._selected_was_created

    def used_built_in_default(self) -> bool:
        """Whether the user chose the built-in default rather than a strategy.

        Distinguishes "no strategy, deliberately" from ``selected_id() is None``
        after a Cancel, which the caller must not act on at all.
        """
        return self._used_none

    # -- cross-tab plumbing ---------------------------------------------

    def _on_structure_changed(self) -> None:
        # Another tab's list can change too: moving a duel into the library takes its
        # player strategies with it, and a new profile gives every tab a section to fill.
        sender = self.sender()
        for page in self._pages.values():
            if page is not sender and page.kind() in PROFILE_KINDS:
                page.rebuild_list()
        if StrategyKind.DUEL in self._pages:
            self._pages[StrategyKind.DUEL].refresh_pcs_list()

    def _on_tab_changed(self, _index: int) -> None:
        # A Player Strategy renamed on its own tab has to show its new name in
        # every assignment selector, and this is the cheapest honest moment.
        duel = self._pages.get(StrategyKind.DUEL)
        if duel is not None:
            duel.refresh_pcs_list()
            duel.rebuild_list()

    def _on_promote_requested(self, row) -> None:
        """Turn one assignment row's custom DSL into a named Player Strategy.

        It lands where the duel lives: in the profile for a profile duel, in the library
        for a library duel, which may only link library entries (#118).
        """
        assignment = row.assignment()
        dsl = assignment.dsl.strip()
        if not dsl:
            return
        duel = self._pages.get(StrategyKind.DUEL)
        in_profile = duel is not None and self._ws.is_profile_entry(StrategyKind.DUEL, duel.selected_id())
        suggested = self._ws.unique_name(StrategyKind.PLAYER, "New Player Strategy")
        name, ok = QInputDialog.getText(self, "Save as Player Strategy", "Name:", text=suggested)
        if not ok or not name.strip():
            return
        pcs = self._ws.create(StrategyKind.PLAYER, name.strip(), in_profile=in_profile)
        pcs.dsl = assignment.dsl
        pcs.schools = list(assignment.schools)
        if StrategyKind.PLAYER in self._pages:
            self._pages[StrategyKind.PLAYER].rebuild_list()
        if duel is not None:
            duel.refresh_pcs_list()
        row.link_to(pcs.id)

    def _on_open_pcs_requested(self, pcs_id: str) -> None:
        """Show one Player Strategy on its own tab — the Duel tab's pencil.

        The filter is cleared first: ``_apply_filter`` keeps a selected row selected
        even when the query hides it, so a stale query would load the strategy into the
        editor with no visible card saying which one it is. Nothing is saved or lost on
        the way — both tabs edit the one working set. Pick has no Player tab, so there
        the pencil has nowhere to go.

        Focus goes to the DSL box last: switching tabs hands it to the page's first
        focusable child, the filter, and the DSL is what the pencil promised.
        """
        page = self._pages.get(StrategyKind.PLAYER)
        if page is None:
            return
        page.clear_filter()
        self._tabs.setCurrentWidget(page)
        page.select(pcs_id)
        page.focus_editor()

    def _on_create_profile(self) -> None:
        """Make a combat profile for a bot that has none — inside the working set, so
        Cancel discards it like any other edit."""
        if self._ws.bot_id is None or self._ws.profile is not None:
            return
        name, ok = QInputDialog.getText(self, "New combat profile", "Name:", text="Default")
        if not ok or not name.strip():
            return
        self._ws.create_profile(name.strip())
        for page in self._pages.values():
            page.rebuild_list()

    # -- closing --------------------------------------------------------

    def reject(self) -> None:
        """Cancel. Confirms first if there is anything to lose.

        There is nothing to roll back — dropping the working set *is* the whole
        of Cancel. The prompt exists only because the count is the one thing the
        user cannot see from the cards.
        """
        if self._ws.is_dirty:
            # A profile made here with nothing in it yet is still one change to lose.
            total = self._ws.changes().total or 1
            answer = QMessageBox.question(
                self,
                "Discard changes?",
                f"{total} change{'s' if total != 1 else ''} will be lost.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is not QMessageBox.StandardButton.Discard:
                return
        super().reject()
