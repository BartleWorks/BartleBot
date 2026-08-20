"""Sidebar section: bot possible drops list."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import DropModel
from bartlebot.gui.sidebar.sidebar_section import SidebarSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_BASE_TITLE = "Possible Drops"


_UNCATEGORIZED_HINT = (
    "Drops imported before categories existed have none. Re-import this bot from the wiki to add them — "
    "categories let the quicksell keep-list picker group drops and leave out the ones it can never sell."
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


def rename_drop(entries: list[DropModel], index: int, new_text: str) -> list[DropModel]:
    """Return *entries* with the drop at *index* renamed to *new_text*.

    The drop keeps its category — renaming "Amber" to "Ambers" does not make it
    uncategorized. Blank text or an out-of-range index leaves the list unchanged.
    If the new name already appears elsewhere the two entries collapse into one,
    matching the dedup behaviour of adding a drop that is already listed; the
    first of the two in list order is the one kept, along with its category.
    """
    new_text = new_text.strip()
    if not new_text or not 0 <= index < len(entries):
        return list(entries)
    result: list[DropModel] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        candidate = DropModel(name=new_text, category=entry.category) if i == index else entry
        if candidate.name not in seen:
            seen.add(candidate.name)
            result.append(candidate)
    return result


def merge_drops(existing: list[DropModel], incoming: list[DropModel]) -> list[DropModel]:
    """Return *existing* plus any of *incoming* not already listed, by name.

    A re-import also backfills: a drop already listed without a category picks up
    one when the incoming entry has it. That is the only way drops added before
    categories existed — or added by hand — ever get categorized.
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
            result.append(DropModel(name=entry.name, category=match.category))
        else:
            result.append(entry)

    seen = {entry.name for entry in existing}
    for entry in incoming:
        if entry.name not in seen:
            seen.add(entry.name)
            result.append(entry)
    return result


class _DropsListWidget(QWidget):
    """Scrollable list of drops with edit controls.

    Rows show the item name only; each row carries its DropModel on UserRole so
    the category survives every edit here without being displayed. Showing
    categories in this section is deliberately left to the quicksell picker,
    which is where they are acted on.

    Always visible; the button row is shown only in edit mode via set_editable().
    Emits changed(list[DropModel]) whenever the list is modified.
    Emits import_requested() when the user clicks Import from Wiki…
    """

    changed = pyqtSignal(list)
    import_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._editable = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._list = QListWidget()
        self._list.setMinimumHeight(120)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
    def _make_btn(text: str) -> QPushButton:
        return QPushButton(text)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._btn_row.setVisible(editable)

    def set_items(self, entries: list[DropModel]) -> None:
        """Replace list contents, sorted alphabetically by name."""
        self._list.clear()
        for entry in sorted(entries, key=lambda e: e.name.casefold()):
            item = QListWidgetItem(entry.name)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            if entry.category:
                item.setToolTip(entry.category)
            self._list.addItem(item)
        self._update_button_state()

    def _update_button_state(self) -> None:
        selected = len(self._list.selectedItems())
        self._btn_edit.setEnabled(selected == 1)
        self._btn_remove.setEnabled(selected > 0)
        self._btn_clear.setEnabled(self._list.count() > 0)

    def get_items(self) -> list[DropModel]:
        return [self._list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._list.count())]

    def add_items(self, entries: list[DropModel]) -> None:
        """Append drops not already listed, then re-sort. Emits changed."""
        existing = self.get_items()
        merged = merge_drops(existing, entries)
        if merged == existing:
            return
        self.set_items(merged)
        self.changed.emit(self.get_items())

    def _on_add(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Drop", "Item name:")
        if ok and text.strip():
            self.add_items([DropModel(name=text.strip())])

    def _on_edit(self) -> None:
        items = self._list.selectedItems()
        if len(items) != 1:
            return
        self._edit_item(items[0])

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        if self._editable:
            self._edit_item(item)

    def _edit_item(self, item: QListWidgetItem) -> None:
        row = self._list.row(item)
        text, ok = QInputDialog.getText(self, "Edit Drop", "Item name:", text=item.text())
        if not ok:
            return
        updated = rename_drop(self.get_items(), row, text)
        if updated == self.get_items():
            return
        self.set_items(updated)
        self.changed.emit(self.get_items())

    def _on_remove(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))
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
