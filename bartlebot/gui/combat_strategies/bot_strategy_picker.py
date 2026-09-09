"""Modal picker for a Bot Combat Strategy — the one pick that is not a library pick.

Everything else this dialog used to pick (duel strategies, player strategies,
deck presets) now goes through :class:`~bartlebot.gui.combat_strategies.dialog.
CombatStrategyDialog` in pick mode, which can also *edit* what it is showing.
A BotCombatStrategy is different in kind: it is bot-scoped, it is authored on
the Combat tab itself, and there is no library tab for it to be edited on. So
this stays, narrowed to that one job, with the ``mode`` switch and the
``all_pcs`` plumbing the general version needed taken out.

Not folded into a ``PickerPopup`` either: that widget is multi-select only, and
teaching it a single-select mode would change a widget that fight membership and
tag picking also depend on, for one caller.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from bartlebot.bot.strategy_models import BotCombatStrategy
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.strategy_card import StrategyCard
from bartlebot.strategy_summary import StrategySummary, summarize_bot

NONE_LABEL = "(built-in default)"


class BotStrategyPicker(QDialog):
    """Searchable card picker over BotCombatStrategies.

    Returns the selected UUID via :meth:`selected_id`. ``None`` means the
    built-in default, and is only offered when ``allow_none``.
    """

    def __init__(
        self,
        strategies: list[BotCombatStrategy],
        current_id: str | None,
        allow_none: bool = True,
        title: str = "Pick Bot Combat Strategy",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(380, 340)

        self._selected_id: str | None = current_id
        self._card_map: list[tuple[QListWidgetItem, StrategyCard]] = []
        self._items: list[tuple[str | None, StrategySummary]] = []
        if allow_none:
            self._items.append((None, StrategySummary(name=NONE_LABEL, description="Uses BartleBot's built-in DSL")))
        self._items.extend((s.id, summarize_bot(s)) for s in strategies)

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

        self._rebuild_list()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_list_style()

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
            if uuid == self._selected_id:
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
            # Borderless, as in the combat dialog: the cards draw their own selected edge
            # and a box around a column of boxes is one line too many.
            f"QListWidget {{ border: none; background: {THEME.bg}; }}"
            f"QListWidget::item {{ background: transparent; border: none; padding: 1px; }}"
            f"QListWidget::item:selected {{ background: transparent; border: none; }}"
            f"QListWidget::item:hover {{ background: transparent; border: none; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_list_style()
