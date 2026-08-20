"""Sidebar section: bot possible drops list."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
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

from bartlebot.gui.sidebar.sidebar_section import SidebarSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_BASE_TITLE = "Possible Drops"


def rename_drop(items: list[str], index: int, new_text: str) -> list[str]:
    """Return *items* with the entry at *index* replaced by *new_text*.

    Blank text or an out-of-range index leaves the list unchanged. If the new name
    already appears elsewhere the two entries collapse into one, matching the
    dedup behaviour of adding a drop that is already listed.
    """
    new_text = new_text.strip()
    if not new_text or not 0 <= index < len(items):
        return list(items)
    result: list[str] = []
    for i, item in enumerate(items):
        candidate = new_text if i == index else item
        if candidate not in result:
            result.append(candidate)
    return result


class _DropsListWidget(QWidget):
    """Scrollable list of drop item strings with edit controls.

    Always visible; the button row is shown only in edit mode via set_editable().
    Emits changed(list[str]) whenever the list is modified.
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

    def set_items(self, items: list[str]) -> None:
        """Replace list contents, sorted alphabetically."""
        self._list.clear()
        for item in sorted(items, key=str.casefold):
            self._list.addItem(item)
        self._update_button_state()

    def _update_button_state(self) -> None:
        selected = len(self._list.selectedItems())
        self._btn_edit.setEnabled(selected == 1)
        self._btn_remove.setEnabled(selected > 0)
        self._btn_clear.setEnabled(self._list.count() > 0)

    def get_items(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def add_items(self, items: list[str]) -> None:
        """Append items not already in the list, then re-sort. Emits changed."""
        existing = set(self.get_items())
        new_items = [x for x in items if x not in existing]
        if not new_items:
            return
        self.set_items(list(existing) + new_items)
        self.changed.emit(self.get_items())

    def _on_add(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Drop", "Item name:")
        if ok and text.strip():
            self.add_items([text.strip()])

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

    def add_drops(self, items: list[str]) -> None:
        """Append *items* to the drops list, deduplicating. Triggers save via drops_changed."""
        if self._config is None:
            return
        self._drops_widget.add_items(items)

    def _on_drops_changed(self, items: list[str]) -> None:
        if self._config is None:
            return
        self._config.drops = items
        self._update_header(items)
        self.drops_changed.emit()

    def _on_import_requested(self) -> None:
        from bartlebot.gui.wiki_import_dialog import WikiImportDialog

        dialog = WikiImportDialog(parent=self.window())
        if dialog.exec():
            self._drops_widget.add_items(dialog.selected_items())

    def _update_header(self, items: list[str]) -> None:
        n = len(items)
        title = f"{_BASE_TITLE} ({n})" if n else _BASE_TITLE
        self._header.label.setText(title)
