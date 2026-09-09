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
"""

from __future__ import annotations

import logging
from enum import Enum

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
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
from bartlebot.gui.combat_strategies.player_editor import PlayerStrategyEditor
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.elided_label import apply_muted_color
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.strategy_card import StrategyCard
from bartlebot.strategy_store import Origin, StrategyKind
from bartlebot.strategy_summary import (
    StrategySummary,
    summarize_deck,
    summarize_duel,
    summarize_player,
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

#: Origin.SHIPPED_OVERRIDE means the shipped file reappears underneath, so the
#: honest verb is "revert" — see :class:`bartlebot.strategy_store.Origin`.
_REVERT_TEXT = "Revert to default"
_DELETE_TEXT = "Delete"

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


def summary_haystack(summary: StrategySummary) -> str:
    """The text a filter query is matched against, lowercased.

    Description and subtitle are included deliberately: a user looking for "the
    storm one" is far more likely to have written that in the description than in
    the name.

    Built once per card and cached on its list item, because ``subtitle_line()``
    is only cheap relative to the summary it comes from — producing that summary
    parses every DSL the entry references.
    """
    return f"{summary.name} {summary.description} {summary.subtitle_line()}".lower()


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
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
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
        self._suppress = False

    def clear(self) -> None:
        self._entry = None
        self._suppress = True
        self._name_edit.clear()
        self._description_edit.clear()
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

    def __init__(self, kind: StrategyKind, ws: StrategyWorkingSet, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._ws = ws
        self._selected_id: str | None = None
        self._card_map: list[tuple[QListWidgetItem, StrategyCard]] = []

        layout = QVBoxLayout(self)
        # A real inset on all four sides. The page used to run edge to edge inside the tab
        # pane, so the filter box, the cards and the fields all sat directly against its
        # border — which is most of what made a screen of otherwise ordinary controls read
        # as crowded. A line with nothing beside it reads as a wall.
        layout.setContentsMargins(_PAGE_INSET, _PAGE_INSET, _PAGE_INSET, _PAGE_INSET)
        layout.setSpacing(6)

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

        self._filter = SearchLineEdit("Filter…")
        # Filtering hides rows; it never rebuilds them. See _apply_filter.
        self._filter.textChanged.connect(self._apply_filter)
        box.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setSpacing(2)
        self._list.currentItemChanged.connect(self._on_item_changed)
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

        self._about = AboutSection()
        self._about.changed.connect(self._on_edited)
        box.addWidget(self._about)

        if self._kind is StrategyKind.DUEL:
            # No wrapper around the config editor: a DuelCombatStrategy IS-A DuelConfig,
            # and the one field the library entry added — the name — is the About
            # section's now.
            self._editor = DuelConfigEditor()
            self._editor.promote_requested.connect(self.promote_requested)
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
            return summarize_duel(item, self._ws.players, name=item.name)
        if self._kind is StrategyKind.PLAYER:
            return summarize_player(item)
        return summarize_deck(item)

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
            keep: QListWidgetItem | None = None
            for entry in self._ws.items(self._kind):
                summary = self.summarize(entry)
                card = StrategyCard(summary)
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, entry.id)
                item.setData(_HAYSTACK_ROLE, summary_haystack(summary))
                item.setSizeHint(card.sizeHint())
                self._list.addItem(item)
                self._list.setItemWidget(item, card)
                self._card_map.append((item, card))
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
        query = self._filter.text()
        blocked = self._list.blockSignals(True)
        try:
            for item, _card in self._card_map:
                item.setHidden(not matches_query(item.data(_HAYSTACK_ROLE) or "", query))
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
            card = StrategyCard(summary)
            card.set_selected(True)
            item.setSizeHint(card.sizeHint())
            item.setData(_HAYSTACK_ROLE, summary_haystack(summary))
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
        origin = self._ws.origin(self._kind, entry.id)
        revert = origin is Origin.SHIPPED_OVERRIDE
        self._del_btn.setText(_REVERT_TEXT if revert else _DELETE_TEXT)
        self._del_btn.set_icon_name("revert" if revert else "trash")
        # A revert is not a caution: the shipped file reappears underneath, so there is
        # nothing to lose — which is exactly the distinction the two verbs exist to draw.
        self._del_btn.set_action_style(ActionStyle.TERTIARY if revert else ActionStyle.CAUTION)
        if revert:
            note = "Your copy of a strategy BartleBot ships — reverting restores the original."
        elif origin is Origin.SHIPPED:
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
        revert = self._ws.origin(self._kind, entry.id) is Origin.SHIPPED_OVERRIDE
        usage = self._ws.usage(entry.id)
        verb = "Revert" if revert else "Delete"
        detail = (
            f"“{entry.name}” will go back to the version BartleBot ships."
            if revert
            else f"“{entry.name}” will be removed when you save."
        )
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
            f"QListWidget::item {{ background: transparent; border: none; padding: 1px; }}"
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
        # pane for space by default — see issue #44.
        self.resize(1040, 640)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._tabs = QTabWidget()
        self._pages: dict[StrategyKind, _KindPage] = {}
        for k, label in KIND_LABELS.items():
            page = _KindPage(k, ws)
            page.structure_changed.connect(self._on_structure_changed)
            page.promote_requested.connect(self._on_promote_requested)
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
        row = QHBoxLayout()
        row.setSpacing(6)
        # ``ActionButton``s rather than Fusion's default chrome, and weighted: the commit
        # is what this dialog exists to reach, so it is the one PRIMARY on the screen;
        # falling back to the built-in default is a real alternative to it and takes the
        # outline; Cancel is neither. See issue #47 and ``ActionStyle``.
        if self._mode is DialogMode.PICK and self._allow_none:
            self._none_btn = ActionButton("revert", f"Use {self._none_label}", style=ActionStyle.SECONDARY, fill=False)
            self._none_btn.setToolTip("Fight with BartleBot's built-in DSL instead of a saved strategy")
            self._none_btn.clicked.connect(self._on_use_none)
            row.addWidget(self._none_btn)
        row.addStretch()
        cancel = ActionButton("dismiss", "Cancel", fill=False)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        # Both spellings of the same act: commit the working set. Use also
        # returns the selection — see the module docstring.
        self._commit_btn = ActionButton(
            "checkmark",
            "Use this" if self._mode is DialogMode.PICK else "Save",
            style=ActionStyle.PRIMARY,
            fill=False,
        )
        self._commit_btn.setDefault(True)
        self._commit_btn.clicked.connect(self._on_commit)
        row.addWidget(self._commit_btn)
        return row

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
        pcs.school = assignment.school
        self._pages[StrategyKind.PLAYER].rebuild_list()
        self._pages[StrategyKind.DUEL].refresh_pcs_list()
        row.link_to(pcs.id)

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
