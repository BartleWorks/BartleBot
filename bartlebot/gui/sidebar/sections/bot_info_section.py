"""Sidebar section: bot metadata fields (name, version, description, …)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.sidebar.sidebar_section import (
    SidebarField,
    SidebarSection,
    value_edit,
    value_edit_multi,
    value_label,
)
from bartlebot.gui.theme import THEME

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


def _swappable(label_w: QWidget, edit_w: QWidget) -> QWidget:
    """Container that shows either *label_w* or *edit_w* (not both).

    The label is shown by default.  Call ``setVisible`` on each child to
    switch modes; the layout collapses the hidden widget to zero height.
    """
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(label_w)
    lay.addWidget(edit_w)
    edit_w.setVisible(False)
    return w


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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._list = QListWidget()
        self._list.setFixedHeight(100)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._list)

        # Button row — shown only in edit mode
        self._btn_row = QWidget()
        btn_layout = QHBoxLayout(self._btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(3)

        self._btn_add = self._make_btn("Add")
        self._btn_remove = self._make_btn("Remove")
        self._btn_clear = self._make_btn("Clear")
        self._btn_import = self._make_btn("Import from Wiki…")

        btn_layout.addWidget(self._btn_add)
        btn_layout.addWidget(self._btn_remove)
        btn_layout.addWidget(self._btn_clear)
        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_import)

        layout.addWidget(self._btn_row)
        self._btn_row.setVisible(False)

        self._btn_add.clicked.connect(self._on_add)
        self._btn_remove.clicked.connect(self._on_remove)
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_import.clicked.connect(self.import_requested)

    @staticmethod
    def _make_btn(text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFlat(True)
        btn.setStyleSheet(
            f"QPushButton {{ color: {THEME.text_muted}; font-size: 10px; padding: 2px 4px; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
        )
        return btn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_editable(self, editable: bool) -> None:
        self._btn_row.setVisible(editable)

    def set_items(self, items: list[str]) -> None:
        """Replace list contents, sorted alphabetically."""
        self._list.clear()
        for item in sorted(items, key=str.casefold):
            self._list.addItem(item)

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

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Drop", "Item name:")
        if ok and text.strip():
            self.add_items([text.strip()])

    def _on_remove(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))
        self.changed.emit(self.get_items())

    def _on_clear(self) -> None:
        self._list.clear()
        self.changed.emit([])


class BotInfoSection(SidebarSection):
    """Collapsible 'Bot Info' section: file, name, version, description, etc."""

    metadata_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Bot Info", parent)
        self._config: BotConfig | None = None
        self._suppress: bool = False

        # File field — always a label, never editable
        self._file_lbl = value_label(wrap=True)

        # Switchable fields: each has a display label and an edit widget.
        # Labels are shown in Run mode (or when no config is loaded).
        # Edit widgets are shown in Edit mode.
        self._name_lbl = value_label()
        self._name_edit = value_edit()
        self._name_edit.setReadOnly(False)

        self._version_lbl = value_label()
        self._version_edit = value_edit()
        self._version_edit.setReadOnly(False)

        self._desc_lbl = value_label(wrap=True)
        self._desc_edit = value_edit_multi(lines=3)
        self._desc_edit.setReadOnly(False)

        self._start_lbl = value_label()
        self._start_edit = value_edit()
        self._start_edit.setReadOnly(False)

        self._authors_lbl = value_label()
        self._authors_edit = value_edit()
        self._authors_edit.setReadOnly(False)

        # Drops: always-visible list widget; button row shown only in edit mode.
        self._drops_widget = _DropsListWidget()

        self._initial_lbl = value_label()
        self._initial_combo = QComboBox()

        self._recovery_lbl = value_label()
        self._recovery_combo = QComboBox()

        # Ordered list of (label, edit) pairs for bulk visibility switching.
        # Drops are excluded — managed separately via _drops_widget.set_editable().
        self._switchable_pairs: list[tuple[QWidget, QWidget]] = [
            (self._name_lbl, self._name_edit),
            (self._version_lbl, self._version_edit),
            (self._desc_lbl, self._desc_edit),
            (self._start_lbl, self._start_edit),
            (self._authors_lbl, self._authors_edit),
            (self._initial_lbl, self._initial_combo),
            (self._recovery_lbl, self._recovery_combo),
        ]

        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        vbox.addWidget(SidebarField("File", self._file_lbl))
        vbox.addWidget(SidebarField("Name", _swappable(self._name_lbl, self._name_edit)))
        vbox.addWidget(SidebarField("Version", _swappable(self._version_lbl, self._version_edit)))
        vbox.addWidget(SidebarField("Description", _swappable(self._desc_lbl, self._desc_edit)))
        vbox.addWidget(SidebarField("Start", _swappable(self._start_lbl, self._start_edit)))
        vbox.addWidget(SidebarField("Author(s)", _swappable(self._authors_lbl, self._authors_edit)))
        vbox.addWidget(SidebarField("Drops", self._drops_widget))
        vbox.addWidget(SidebarField("Initial State", _swappable(self._initial_lbl, self._initial_combo)))
        vbox.addWidget(SidebarField("Recovery State", _swappable(self._recovery_lbl, self._recovery_combo)))

        self.set_content_layout(vbox)

        self._name_edit.textChanged.connect(self._on_name_changed)
        self._version_edit.textChanged.connect(self._on_version_changed)
        self._desc_edit.textChanged.connect(self._on_desc_changed)
        self._start_edit.textChanged.connect(self._on_start_changed)
        self._authors_edit.textChanged.connect(self._on_authors_changed)
        self._drops_widget.changed.connect(self._on_drops_changed)
        self._drops_widget.import_requested.connect(self._on_import_requested)
        self._initial_combo.currentTextChanged.connect(self._on_initial_changed)
        self._recovery_combo.currentTextChanged.connect(self._on_recovery_changed)

        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_file(self, filename: str | None) -> None:
        self._file_lbl.setText(filename or _PLACEHOLDER)

    def add_drops(self, items: list[str]) -> None:
        """Append *items* to the drops list, deduplicating. Triggers save via metadata_changed."""
        if self._config is None:
            return
        self._drops_widget.add_items(items)

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        if config is None:
            for lbl in (
                self._name_lbl,
                self._version_lbl,
                self._desc_lbl,
                self._start_lbl,
                self._authors_lbl,
                self._initial_lbl,
            ):
                lbl.setText(_PLACEHOLDER)
            for w in (
                self._name_edit,
                self._version_edit,
                self._start_edit,
                self._authors_edit,
            ):
                w.setText("")
            self._desc_edit.setPlainText("")
            self._drops_widget.set_items([])
            self._initial_combo.clear()
            self._recovery_combo.clear()
            self._recovery_lbl.setText(_PLACEHOLDER)
            self._set_label_mode(True)
        else:
            self._name_lbl.setText(config.name or _PLACEHOLDER)
            self._version_lbl.setText(config.version or _PLACEHOLDER)
            self._desc_lbl.setText(config.description or _PLACEHOLDER)
            self._start_lbl.setText(config.start_location or _PLACEHOLDER)
            self._authors_lbl.setText(config.authors or _PLACEHOLDER)
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._desc_edit.setPlainText(config.description or "")
            self._start_edit.setText(config.start_location or "")
            self._authors_edit.setText(config.authors or "")
            self._drops_widget.set_items(config.drops)
            self._populate_initial_combo(config.states, config.initial)
            self._populate_recovery_combo(config.states, config.recovery_state)
        self._suppress = False

    def refresh_states(self, states: list) -> None:
        if self._config is None:
            return
        self._populate_initial_combo(states, self._config.initial)
        self._populate_recovery_combo(states, self._config.recovery_state)

    def set_editable(self, editable: bool) -> None:
        if self._config is None:
            return
        self._set_label_mode(not editable)
        self._drops_widget.set_editable(editable)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_name_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.name = text
        self._name_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_version_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.version = text
        self._version_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_desc_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        text = self._desc_edit.toPlainText()
        self._config.description = text or None
        self._desc_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_start_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.start_location = text or None
        self._start_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_authors_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.authors = text or None
        self._authors_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_drops_changed(self, items: list[str]) -> None:
        if self._suppress or self._config is None:
            return
        self._config.drops = items
        self.metadata_changed.emit()

    def _on_import_requested(self) -> None:
        from bartlebot.gui.wiki_import_dialog import WikiImportDialog

        dialog = WikiImportDialog(parent=self.window())
        if dialog.exec():
            self._drops_widget.add_items(dialog.selected_items())

    def _on_initial_changed(self, text: str) -> None:
        if self._suppress or self._config is None or not text:
            return
        self._config.initial = text
        self.metadata_changed.emit()

    def _on_recovery_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.recovery_state = text if text and text != _PLACEHOLDER else None
        self._recovery_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_label_mode(self, label_mode: bool) -> None:
        """Switch all switchable fields between display labels and edit widgets."""
        for lbl, edit in self._switchable_pairs:
            lbl.setVisible(label_mode)
            edit.setVisible(not label_mode)

    def _populate_initial_combo(self, states: list, current_initial: str) -> None:
        self._suppress = True
        self._initial_combo.clear()
        for state in states:
            self._initial_combo.addItem(state.id)
        idx = self._initial_combo.findText(current_initial)
        if idx >= 0:
            self._initial_combo.setCurrentIndex(idx)
        self._initial_lbl.setText(current_initial or _PLACEHOLDER)
        self._suppress = False

    def _populate_recovery_combo(self, states: list, current_recovery: str | None) -> None:
        self._suppress = True
        self._recovery_combo.clear()
        self._recovery_combo.addItem(_PLACEHOLDER)  # "(none)" option
        for state in states:
            self._recovery_combo.addItem(state.id)
        target = current_recovery or _PLACEHOLDER
        idx = self._recovery_combo.findText(target)
        if idx >= 0:
            self._recovery_combo.setCurrentIndex(idx)
        self._recovery_lbl.setText(current_recovery or _PLACEHOLDER)
        self._suppress = False
