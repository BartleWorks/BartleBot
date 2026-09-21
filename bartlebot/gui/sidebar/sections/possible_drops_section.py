"""Sidebar section: bot possible drops list."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import DropModel
from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.dialog_footer import dialog_footer
from bartlebot.gui.widgets.drop_tree import (
    DROP_ICON_PX,
    UNCATEGORIZED_DISPLAY,
    CategoryThenNameTreeItem,
    drop_icon,
    show_drop_icons,
)
from bartlebot.nodes.utility.quicksell import DROP_CATEGORIES
from bartlebot.wiki_drops import match_name_for

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_BASE_TITLE = "Possible Drops"


_UNCATEGORIZED_HINT = (
    "Drops imported before categories existed have none. Re-import this bot from the wiki to add them, or set "
    "one by hand with Edit — categories let the quicksell keep-list picker group drops and leave out the ones "
    "it can never sell."
)


def drops_header_text(entries: list[DropModel]) -> str:
    """Section title with the drop count, calling out drops that have no category.

    The uncategorized count is the only prompt to re-import: nothing else tells a
    user that a bot predates categories, and without one they never appear.
    """
    if not entries:
        return _BASE_TITLE
    uncategorized = sum(1 for entry in entries if not entry.category)
    if not uncategorized:
        return f"{_BASE_TITLE} ({len(entries)})"
    return f"{_BASE_TITLE} ({len(entries)} · {uncategorized} uncategorized)"


def edit_drop(entries: list[DropModel], index: int, new_text: str, category: str | None) -> list[DropModel]:
    """Return *entries* with the drop at *index* renamed to *new_text* under *category*.

    The one edit both the name and the category go through, because the two are not
    independent: what the game calls an item depends on which category it is in (the
    school suffix on a jewel is only strippable once we know it is a jewel), so
    ``match_name`` is re-derived from the pair rather than carried over from either.
    *category* is None for uncategorized, which is a value a user can choose and not
    merely the absence of one.

    Blank text or an out-of-range index leaves the list unchanged. If the new name
    already appears elsewhere the two entries collapse into one, matching the dedup
    behaviour of adding a drop that is already listed; the first of the two in list
    order is the one kept, along with its category.
    """
    new_text = new_text.strip()
    if not new_text or not 0 <= index < len(entries):
        return list(entries)
    result: list[DropModel] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        candidate = (
            DropModel(
                name=new_text,
                category=category,
                match_name=match_name_for(new_text, category),
            )
            if i == index
            else entry
        )
        if candidate.name not in seen:
            seen.add(candidate.name)
            result.append(candidate)
    return result


def merge_drops(existing: list[DropModel], incoming: list[DropModel]) -> list[DropModel]:
    """Return *existing* plus any of *incoming* not already listed, by name.

    A re-import also backfills: a drop already listed without a category picks up
    one when the incoming entry has it. That is the only way drops added before
    categories existed — or added by hand — ever get categorized. Gaining a
    category can also settle what the game calls the item (the school suffix on a
    jewel is only strippable once we know it is a jewel), so the in-game name is
    re-derived at the same time.
    """
    incoming_by_name: dict[str, DropModel] = {}
    for entry in incoming:
        incoming_by_name.setdefault(entry.name, entry)

    # Rebuild rather than mutate: callers compare the result against the original
    # to decide whether anything changed, which in-place edits would hide.
    result: list[DropModel] = []
    for entry in existing:
        match = incoming_by_name.get(entry.name)
        if entry.category is None and match is not None and match.category is not None:
            result.append(
                DropModel(
                    name=entry.name,
                    category=match.category,
                    match_name=match_name_for(entry.name, match.category),
                )
            )
        else:
            result.append(entry)

    seen = {entry.name for entry in existing}
    for entry in incoming:
        if entry.name not in seen:
            seen.add(entry.name)
            result.append(entry)
    return result


class _DropEditDialog(QDialog):
    """Name and category for one drop, added or edited by hand.

    The category is a closed dropdown rather than a text field. Its vocabulary is
    the wiki import's own (``quicksell.DROP_CATEGORIES``), and a category outside
    it is one no icon, no sellability classification and no keep-list filter
    knows — a typo here would read as a broken row rather than as a new word. The
    one exception is a category the bot file already carries that the list does
    not: that is offered too, so editing such a drop's *name* cannot silently
    strip its category.

    Uncategorized is an entry, not the absence of one, so a drop the wiki got
    wrong can be put back to having none.
    """

    #: The uncategorized entry's label: the em dash the tables show, said in words
    #: as well, since a dash alone in a dropdown reads as a separator.
    NO_CATEGORY_LABEL = f"{UNCATEGORIZED_DISPLAY}  (none)"

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Edit Drop",
        name: str = "",
        category: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)

        self._name = QLineEdit(name)
        self._name.setPlaceholderText("Item name, as the wiki spells it")

        self._category = QComboBox()
        self._category.setIconSize(QSize(DROP_ICON_PX, DROP_ICON_PX))
        self._category.addItem(self.NO_CATEGORY_LABEL, None)
        for value in self._offered_categories(category):
            icon = drop_icon(value)
            if icon is None:
                self._category.addItem(value, value)
            else:
                self._category.addItem(icon, value, value)
        selected = self._category.findData(category)
        self._category.setCurrentIndex(selected if selected >= 0 else 0)

        form = QFormLayout()
        form.addRow("Item", self._name)
        form.addRow("Category", self._category)

        footer = dialog_footer(self, commit_text="Save")
        commit = footer.commit
        commit.setEnabled(bool(name.strip()))
        self._name.textChanged.connect(lambda text: commit.setEnabled(bool(text.strip())))

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(footer.layout)
        self.resize(360, self.sizeHint().height())

    @staticmethod
    def _offered_categories(current: str | None) -> list[str]:
        """The vocabulary, plus *current* when the bot file carries one nobody classified."""
        if current and current not in DROP_CATEGORIES:
            return [*DROP_CATEGORIES, current]
        return list(DROP_CATEGORIES)

    @property
    def drop_name(self) -> str:
        return self._name.text().strip()

    @property
    def category(self) -> str | None:
        return self._category.currentData()


class _DropsListWidget(QWidget):
    """Sortable Item / Category table of a bot's drops, with edit controls.

    Sorting by category is the point of the second column: it groups a 600-entry
    drop table into something you can curate, and it puts the uncategorized rows
    together so it is obvious which bots still need a wiki re-import.

    The view's sort order never reaches storage — get_items() always returns
    entries in name order, so clicking a header does not rewrite the bot file.

    Always visible; the button row is shown only in edit mode via set_editable().
    Emits changed(list[DropModel]) whenever the list is modified.
    Emits import_requested() when the user clicks Import from Wiki…
    """

    changed = pyqtSignal(list)
    import_requested = pyqtSignal()

    # Public: the sort tests and any caller of sort_by_column() name these columns.
    COL_NAME = 0
    COL_CATEGORY = 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._editable = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._list = QTreeWidget()
        self._list.setHeaderLabels(["Item", "Category"])
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        self._list.setSortingEnabled(True)
        # Category first: the table opens as the grouping the second column exists for,
        # with names ordered inside each group. Clicking Item still sorts by name.
        self._list.sortByColumn(self.COL_CATEGORY, Qt.SortOrder.AscendingOrder)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setMinimumHeight(120)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self._list.header()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_CATEGORY, QHeaderView.ResizeMode.Fixed)
        self._list.setColumnWidth(self.COL_CATEGORY, 92)
        show_drop_icons(self._list)
        layout.addWidget(self._list, 1)

        # Button row — shown only in edit mode
        self._btn_row = QWidget()
        btn_layout = QHBoxLayout(self._btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(3)

        self._btn_add = self._make_btn("Add")
        self._btn_edit = self._make_btn("Edit")
        self._btn_remove = self._make_btn("Remove")
        self._btn_clear = self._make_btn("Clear")
        self._btn_import = self._make_btn("Import from Wiki…")

        btn_layout.addWidget(self._btn_add)
        btn_layout.addWidget(self._btn_edit)
        btn_layout.addWidget(self._btn_remove)
        btn_layout.addWidget(self._btn_clear)
        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_import)

        layout.addWidget(self._btn_row)
        self._btn_row.setVisible(False)

        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_remove.clicked.connect(self._on_remove)
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_import.clicked.connect(self.import_requested)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.itemSelectionChanged.connect(self._update_button_state)
        self._update_button_state()

    @staticmethod
    def _make_btn(text: str) -> ActionButton:
        """One weight for the whole row, no glyphs, and sized to its own text.

        No ``PRIMARY``: five verbs on a selection, none of which the section expects more
        than the others. No icons either, and that is the sidebar's doing rather than a
        preference — this row is five buttons wide inside a 300px column, and a 16px glyph
        on each is 80px this row does not have.
        """
        return ActionButton("", text, fill=False)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._btn_row.setVisible(editable)

    def set_items(self, entries: list[DropModel]) -> None:
        """Replace table contents. The view keeps whichever column the user sorted by."""
        self._list.clear()
        for entry in entries:
            item = CategoryThenNameTreeItem([entry.name, entry.category or UNCATEGORIZED_DISPLAY])
            item.setData(self.COL_NAME, Qt.ItemDataRole.UserRole, entry)
            if (icon := drop_icon(entry.category, entry.name)) is not None:
                item.setIcon(self.COL_CATEGORY, icon)
            self._list.addTopLevelItem(item)
        self._update_button_state()

    def _update_button_state(self) -> None:
        selected = len(self._list.selectedItems())
        self._btn_edit.setEnabled(selected == 1)
        self._btn_remove.setEnabled(selected > 0)
        self._btn_clear.setEnabled(self._list.topLevelItemCount() > 0)

    def get_items(self) -> list[DropModel]:
        """Entries in name order, whatever the view is sorted by.

        Sorting is a view concern: letting it reach here would rewrite the bot
        file's drop order every time someone clicked a column header.
        """
        entries = [
            self._list.topLevelItem(i).data(self.COL_NAME, Qt.ItemDataRole.UserRole)
            for i in range(self._list.topLevelItemCount())
        ]
        return sorted(entries, key=lambda e: e.name.casefold())

    def displayed_items(self) -> list[DropModel]:
        """Entries in the order the table currently shows them.

        The counterpart to get_items(): that one is storage order and is what the
        bot file gets, this one is the view order the user actually reads.
        """
        return [
            self._list.topLevelItem(i).data(self.COL_NAME, Qt.ItemDataRole.UserRole)
            for i in range(self._list.topLevelItemCount())
        ]

    def sort_column(self) -> int:
        """Which column the table is sorted by. COL_CATEGORY until the user clicks a header."""
        return self._list.sortColumn()

    def sort_by_column(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """Re-sort as a header click would."""
        self._list.sortByColumn(column, order)

    def add_items(self, entries: list[DropModel]) -> None:
        """Append drops not already listed, then re-sort. Emits changed."""
        existing = self.get_items()
        merged = merge_drops(existing, entries)
        if merged == existing:
            return
        self.set_items(merged)
        self.changed.emit(self.get_items())

    def _on_add(self) -> None:
        dialog = _DropEditDialog(self.window(), title="Add Drop")
        if not dialog.exec():
            return
        name = dialog.drop_name
        if not name:
            return
        category = dialog.category
        self.add_items([DropModel(name=name, category=category, match_name=match_name_for(name, category))])

    def _on_edit(self) -> None:
        items = self._list.selectedItems()
        if len(items) != 1:
            return
        self._edit_item(items[0])

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        if self._editable:
            self._edit_item(item)

    def _edit_item(self, item: QTreeWidgetItem) -> None:
        entries = self.get_items()
        name = item.text(self.COL_NAME)
        # Index into get_items(), not the view: the two differ whenever the user
        # has sorted by category. Names are unique, so this is well defined.
        row = next((i for i, entry in enumerate(entries) if entry.name == name), -1)
        if row < 0:
            return
        dialog = _DropEditDialog(self.window(), name=name, category=entries[row].category)
        if not dialog.exec():
            return
        updated = edit_drop(entries, row, dialog.drop_name, dialog.category)
        if updated == entries:
            return
        self.set_items(updated)
        self.changed.emit(self.get_items())

    def _on_remove(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeTopLevelItem(self._list.indexOfTopLevelItem(item))
        self._update_button_state()
        self.changed.emit(self.get_items())

    def _on_clear(self) -> None:
        self._list.clear()
        self._update_button_state()
        self.changed.emit([])


class PossibleDropsSection(SidebarSection):
    """Collapsible 'Possible Drops' section: the items this bot can farm."""

    drops_changed = pyqtSignal()
    _expanded_v_policy = QSizePolicy.Policy.Expanding

    def __init__(self, parent=None):
        super().__init__(_BASE_TITLE, parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self._config: BotConfig | None = None

        self._drops_widget = _DropsListWidget()

        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(self._drops_widget, 1)
        self.set_content_layout(vbox)

        self._drops_widget.changed.connect(self._on_drops_changed)
        self._drops_widget.import_requested.connect(self._on_import_requested)

    @property
    def config(self) -> BotConfig | None:
        """The BotConfig this list writes its edits into."""
        return self._config

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._drops_widget.set_items(config.drops if config is not None else [])
        self._update_header(config.drops if config is not None else [])

    def set_editable(self, editable: bool) -> None:
        self._drops_widget.set_editable(editable)

    def add_drops(self, entries: list[DropModel]) -> None:
        """Append *entries* to the drops list, deduplicating. Triggers save via drops_changed."""
        if self._config is None:
            return
        self._drops_widget.add_items(entries)

    def _on_drops_changed(self, entries: list[DropModel]) -> None:
        if self._config is None:
            return
        self._config.drops = entries
        self._update_header(entries)
        self.drops_changed.emit()

    def _on_import_requested(self) -> None:
        from bartlebot.gui.wiki_import_dialog import WikiImportDialog

        dialog = WikiImportDialog(parent=self.window())
        if dialog.exec():
            self._drops_widget.add_items(dialog.selected_entries())

    def _update_header(self, entries: list[DropModel]) -> None:
        self._header.label.setText(drops_header_text(entries))
        self._header.label.setToolTip(_UNCATEGORIZED_HINT if any(not e.category for e in entries) else "")
