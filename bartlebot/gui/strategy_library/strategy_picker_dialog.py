"""Modal dialog for selecting a strategy from a searchable card list."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from bartlebot.gui.theme import THEME

_NONE_UUID = "__none__"  # sentinel for "built-in default / no assignment"


def _dcs_subtitle(s: Any) -> str:
    from bartlebot.bot.strategy_models import DuelCombatStrategy  # noqa: PLC0415

    if not isinstance(s, DuelCombatStrategy):
        return ""
    order = s.combat_order
    if not order:
        order_str = "Simultaneous"
    else:
        order_str = " → ".join(f"P{p}" for p in order)
    return f"{s.min_players}–{s.max_players} players · {order_str}"


def _bcs_subtitle(s: Any) -> str:
    from bartlebot.bot.strategy_models import BotCombatStrategy  # noqa: PLC0415

    if not isinstance(s, BotCombatStrategy):
        return ""
    n = len(s.state_assignments)
    return f"{n} state assignment{'s' if n != 1 else ''}"


def _pcs_subtitle(s: Any) -> str:
    from bartlebot.bot.strategy_models import PlayerCombatStrategy  # noqa: PLC0415

    if not isinstance(s, PlayerCombatStrategy):
        return ""
    dsl = s.dsl.strip()
    return dsl[:70] + ("…" if len(dsl) > 70 else "") if dsl else "(empty DSL)"


_SUBTITLE_FN = {
    "duel": _dcs_subtitle,
    "bot": _bcs_subtitle,
    "player": _pcs_subtitle,
}


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
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(380, 340)

        self._mode = mode
        self._strategies = strategies
        self._selected_id: str | None = current_id
        self._none_label = none_label

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.textChanged.connect(self._rebuild_list)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_accept)
        self._list.currentItemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list, 1)

        self._populate(allow_none, none_label)
        self._rebuild_list()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_style()

    def _populate(self, allow_none: bool, none_label: str) -> None:
        self._items: list[tuple[str | None, str, str]] = []  # (uuid_or_None, name, subtitle)
        if allow_none:
            self._items.append((None, none_label, "Uses BartleBot's built-in DSL"))
        subtitle_fn = _SUBTITLE_FN.get(self._mode, lambda _: "")
        for s in self._strategies:
            self._items.append((s.id, s.name, subtitle_fn(s)))

    def _rebuild_list(self) -> None:
        query = self._filter.text().strip().lower()
        self._list.clear()
        for uuid, name, subtitle in self._items:
            if query and query not in name.lower() and query not in subtitle.lower():
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, uuid)
            # Build display text
            if subtitle:
                item.setText(f"{name}\n{subtitle}")
            else:
                item.setText(name)
            self._list.addItem(item)
            if uuid == self._selected_id or (uuid is None and self._selected_id is None):
                self._list.setCurrentItem(item)

    def _on_item_changed(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self._selected_id = item.data(Qt.ItemDataRole.UserRole)

    def _on_accept(self) -> None:
        current = self._list.currentItem()
        if current is not None:
            self._selected_id = current.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def selected_id(self) -> str | None:
        return self._selected_id

    def _apply_style(self) -> None:
        self._list.setStyleSheet(
            f"QListWidget {{ border: 1px solid {THEME.border}; background: {THEME.bg}; }}"
            f"QListWidget::item {{ padding: 4px 6px; color: {THEME.text}; }}"
            f"QListWidget::item:selected {{ background: {THEME.accent}; color: {THEME.bg}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()
