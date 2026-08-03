"""Modal dialog for selecting a strategy from a searchable card list."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.theme import THEME

_NONE_UUID = "__none__"  # sentinel for "built-in default / no assignment"

# Fixed card heights (contentsMargins 7+7 top/bottom, name ~16px, subtitle ~14px + 2px spacing)
_CARD_H_WITH_SUB = 52
_CARD_H_NO_SUB = 36


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


class _StrategyCard(QWidget):
    """Card widget rendered inside a QListWidget item via setItemWidget."""

    def __init__(self, name: str, subtitle: str, parent=None):
        super().__init__(parent)
        self._selected = False
        self._hovered = False
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("StrategyCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        self._name_lbl = QLabel(name)
        f = self._name_lbl.font()
        f.setBold(True)
        self._name_lbl.setFont(f)
        self._name_lbl.setStyleSheet("background: transparent;")
        layout.addWidget(self._name_lbl)

        self._sub_lbl: QLabel | None = None
        if subtitle:
            self._sub_lbl = QLabel(subtitle)
            self._sub_lbl.setStyleSheet("font-size: 10px; background: transparent;")
            layout.addWidget(self._sub_lbl)

        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._apply_style()
        super().leaveEvent(event)

    def _apply_style(self) -> None:
        if self._applying_style:
            return
        self._applying_style = True
        try:
            border = THEME.accent if self._selected else THEME.border
            bg = THEME.node_fill if (self._selected or self._hovered) else THEME.surface
            self.setStyleSheet(f"#StrategyCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; }}")
            self._name_lbl.setStyleSheet(f"color: {THEME.text}; background: transparent;")
            if self._sub_lbl:
                self._sub_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; background: transparent;")
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()


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
        self._card_map: list[tuple[QListWidgetItem, _StrategyCard]] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
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
        self._items: list[tuple[str | None, str, str]] = []  # (uuid_or_None, name, subtitle)
        if allow_none:
            self._items.append((None, none_label, "Uses BartleBot's built-in DSL"))
        subtitle_fn = _SUBTITLE_FN.get(self._mode, lambda _: "")
        for s in self._strategies:
            self._items.append((s.id, s.name, subtitle_fn(s)))

    def _rebuild_list(self) -> None:
        query = self._filter.text().strip().lower()
        self._list.clear()
        self._card_map.clear()
        for uuid, name, subtitle in self._items:
            if query and query not in name.lower() and query not in subtitle.lower():
                continue
            card = _StrategyCard(name, subtitle)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, uuid)
            item.setSizeHint(QSize(0, _CARD_H_WITH_SUB if subtitle else _CARD_H_NO_SUB))
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
