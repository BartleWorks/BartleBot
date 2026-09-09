"""Modal dialog for selecting a strategy from a searchable card list."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.strategy_card import StrategyCard
from bartlebot.strategy_summary import StrategySummary, summarize_bot, summarize_duel, summarize_player


def _summarize(mode: str, s: Any, all_pcs: list) -> StrategySummary:
    from bartlebot.bot.strategy_models import (  # noqa: PLC0415
        BotCombatStrategy,
        DuelCombatStrategy,
        PlayerCombatStrategy,
    )

    if mode == "duel" and isinstance(s, DuelCombatStrategy):
        return summarize_duel(s, all_pcs, name=s.name)
    if mode == "bot" and isinstance(s, BotCombatStrategy):
        return summarize_bot(s)
    if mode == "player" and isinstance(s, PlayerCombatStrategy):
        return summarize_player(s)
    return StrategySummary(name=getattr(s, "name", ""), description="")


class StrategyPickerDialog(QDialog):
    """Searchable strategy card picker.

    Returns the selected UUID via ``selected_id()``.  None means "built-in default"
    (only available when allow_none=True).
    """

    def __init__(
        self,
        title: str,
        mode: str,
        strategies: list,
        current_id: str | None,
        allow_none: bool = True,
        none_label: str = "(built-in default)",
        all_pcs: list | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(380, 340)

        self._mode = mode
        self._strategies = strategies
        self._all_pcs = all_pcs or []
        self._selected_id: str | None = current_id
        self._none_label = none_label
        self._card_map: list[tuple[QListWidgetItem, StrategyCard]] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._filter = SearchLineEdit("Filter…")
        self._filter.textChanged.connect(self._rebuild_list)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setSpacing(2)
        self._list.itemDoubleClicked.connect(self._on_accept)
        self._list.currentItemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list, 1)

        self._populate(allow_none, none_label)
        self._rebuild_list()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_list_style()

    def _populate(self, allow_none: bool, none_label: str) -> None:
        self._items: list[tuple[str | None, StrategySummary]] = []
        if allow_none:
            self._items.append((None, StrategySummary(name=none_label, description="Uses BartleBot's built-in DSL")))
        for s in self._strategies:
            self._items.append((s.id, _summarize(self._mode, s, self._all_pcs)))

    def _rebuild_list(self) -> None:
        query = self._filter.text().strip().lower()
        self._list.clear()
        self._card_map.clear()
        for uuid, summary in self._items:
            haystack = f"{summary.name} {summary.description} {summary.subtitle_line()}".lower()
            if query and query not in haystack:
                continue
            card = StrategyCard(summary)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, uuid)
            item.setSizeHint(card.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, card)
            self._card_map.append((item, card))
            if uuid == self._selected_id or (uuid is None and self._selected_id is None):
                self._list.setCurrentItem(item)

    def _on_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        for item, card in self._card_map:
            if item is previous:
                card.set_selected(False)
            elif item is current:
                card.set_selected(True)
        if current is not None:
            self._selected_id = current.data(Qt.ItemDataRole.UserRole)

    def _on_accept(self) -> None:
        current = self._list.currentItem()
        if current is not None:
            self._selected_id = current.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_id(self) -> str | None:
        return self._selected_id

    def _apply_list_style(self) -> None:
        self._list.setStyleSheet(
            f"QListWidget {{ border: 1px solid {THEME.border}; background: {THEME.bg}; }}"
            f"QListWidget::item {{ background: transparent; border: none; padding: 1px; }}"
            f"QListWidget::item:selected {{ background: transparent; border: none; }}"
            f"QListWidget::item:hover {{ background: transparent; border: none; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_list_style()
