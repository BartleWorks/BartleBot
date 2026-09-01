"""Simple picker dialog for selecting a DeckPreset from the global library."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from bartlebot.bot.strategy_models import DeckPreset
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.search_field import SearchLineEdit


class DeckPresetPickerDialog(ThemedMixin, QDialog):
    """Shows a filterable list of DeckPresets for the user to pick from."""

    def __init__(self, presets: list[DeckPreset], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load Deck from Library")
        self.setMinimumSize(340, 300)

        self._presets = presets
        self._selected_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._filter = SearchLineEdit("Filter…")
        self._filter.textChanged.connect(self._rebuild_list)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_item_changed)
        self._list.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self._list, 1)

        self._rebuild_list()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._list.setStyleSheet(
            f"QListWidget {{ border: 1px solid {THEME.border}; background: {THEME.bg}; }}"
            f"QListWidget::item {{ padding: 4px 6px; color: {THEME.text}; }}"
            f"QListWidget::item:selected {{ background: {THEME.node_fill};"
            f" border-left: 2px solid {THEME.accent}; color: {THEME.text}; padding-left: 4px; }}"
        )

    def selected_id(self) -> str | None:
        return self._selected_id

    def _rebuild_list(self) -> None:
        query = self._filter.text().strip().lower()
        self._list.clear()
        for preset in self._presets:
            if query and query not in preset.name.lower():
                continue
            total = preset.deck.total_cards()
            subtitle = f"{total} cards" if total else "empty"
            item = QListWidgetItem(f"{preset.name}  ({subtitle})")
            item.setData(Qt.ItemDataRole.UserRole, preset.id)
            self._list.addItem(item)

    def _on_item_changed(self, current: QListWidgetItem | None, _previous) -> None:
        self._ok_btn.setEnabled(current is not None)

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
