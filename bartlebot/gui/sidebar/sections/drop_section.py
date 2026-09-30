"""Drops tab content: per-client drop tables with CSV export."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot import bot_options
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.nodes.utility import quicksell

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

#: Shown on the keep-list filter when the bot has no keep list to filter to — it
#: never quicksells, or selling is switched off, so every drop is kept and the
#: filter would answer nothing. The checkbox is disabled rather than hidden: a
#: control that appears only for some bots is one nobody finds.
_NO_KEEP_LIST_HINT = "This bot has no quicksell keep list to filter to."

_KEEP_LIST_HINT = "Show only the drops this bot's quicksell keep list would keep."


def drop_row_visible(item_name: str, query: str, keywords: list[str], keep_only: bool) -> bool:
    """Whether one live drop row survives the search box and the keep-list filter.

    The two compose: a query narrows within the keep list rather than replacing it.

    *keywords* is the resolved keep list — ``quicksell.effective_keep_list``, which
    includes the keywords the sell_* bools imply — and the match is the same
    case-insensitive substring test the node itself runs, so a row shown here is a
    row quicksell would spare.
    """
    if keep_only and quicksell.matching_keyword(item_name, keywords) is None:
        return False
    query = query.strip().lower()
    return not query or query in item_name.lower()


def _qty_item(qty: int) -> QTableWidgetItem:
    """Build a Qty cell that sorts as a number.

    ``QTableWidgetItem`` compares its display data as a ``QVariant``, so a cell
    holding the *string* "10" sorts before "9". Storing the int instead makes Qt
    compare numerically; it still renders as "10", and ``text()`` still returns
    "10" for the CSV export.
    """
    item = QTableWidgetItem()
    item.setData(Qt.ItemDataRole.DisplayRole, qty)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


class DropSection(QWidget):
    """Per-client drop tables with search and CSV export.

    Added straight to RightSidebar's Drops tab rather than wrapped in a scroll
    area — the tables scroll internally.  The padding inside the tab pane is the
    page's, shared with Possible Drops above, so this layout has none of its own.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self._config: BotConfig | None = None
        # The resolved keep list, recomputed rather than watched: it is four option
        # levels deep and every level of it is edited somewhere else in the window.
        self._keep_list: list[str] = []
        self._tables: list[QTableWidget] = []
        self._data: list[dict[str, int]] = []  # backing store; one dict per client
        self._pending: set[int] = set()  # client indices that need a table rebuild
        self._flush_scheduled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)

        self._search = SearchLineEdit("Search items…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self._search, 1)

        # What a user wants from a finished run is rarely the whole list: it is
        # whether anything they said to keep came out of it.
        self._keep_only = QCheckBox("Keep list only")
        self._keep_only.checkStateChanged.connect(self._on_keep_only_changed)
        filter_row.addWidget(self._keep_only)

        layout.addLayout(filter_row)
        self._apply_keep_list_state()

        self._tabs = QTabWidget()
        self._tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        export_btn = ActionButton("upload", "Export CSV")
        export_btn.clicked.connect(self._export_csv)
        layout.addWidget(export_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        """Bind the bot whose quicksell keep list the Keep list only filter reads."""
        self._config = config
        self.refresh_keep_list()

    def refresh_keep_list(self) -> None:
        """Re-resolve the keep list and re-apply the filter if it moved.

        Called when the bot is bound and whenever the Drops tab comes forward,
        because the keep list is a bot option: the author's declaration, the user's
        override and the app setting are all edited elsewhere in the window, and
        none of them reports here.
        """
        keywords = self._resolve_keep_list()
        if keywords == self._keep_list:
            return
        self._keep_list = keywords
        self._apply_keep_list_state()
        self._on_search_changed(self._search.text())

    def setup_clients(self, count: int) -> None:
        """Create one tab per client, replacing any existing tabs."""
        self._tabs.clear()
        self._tables.clear()
        self._data.clear()
        self._pending.clear()
        self._flush_scheduled = False
        for i in range(count):
            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Item", "Qty"])
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.verticalHeader().setVisible(False)
            table.setSortingEnabled(True)
            self._tables.append(table)
            self._data.append({})
            self._tabs.addTab(table, f"Player {i + 1}")

    def table(self, client_index: int) -> QTableWidget | None:
        """The drop table for one client, or None if there is no such client."""
        if 0 <= client_index < len(self._tables):
            return self._tables[client_index]
        return None

    def add_drop(self, item: str, quantity: int, client_index: int = 0) -> None:
        if client_index >= len(self._data):
            return
        d = self._data[client_index]
        d[item] = d.get(item, 0) + quantity
        self._pending.add(client_index)
        if not self._flush_scheduled:
            self._flush_scheduled = True
            QTimer.singleShot(0, self._flush)

    def clear_drops(self) -> None:
        for d in self._data:
            d.clear()
        for table in self._tables:
            table.setRowCount(0)
        self._pending.clear()
        self._flush_scheduled = False
        self._search.clear()

    def swap_clients(self, idx_a: int, idx_b: int) -> None:
        """Swap the drop tables for two client positions."""
        n = len(self._tables)
        if idx_a == idx_b or not (0 <= idx_a < n) or not (0 <= idx_b < n):
            return
        if idx_a > idx_b:
            idx_a, idx_b = idx_b, idx_a
        # Move tabs: put idx_b's tab at idx_a, then idx_a's (now at idx_a+1) to idx_b
        bar = self._tabs.tabBar()
        bar.moveTab(idx_b, idx_a)
        bar.moveTab(idx_a + 1, idx_b)
        # Keep _tables and _data in sync with the new tab order
        self._tables[idx_a], self._tables[idx_b] = self._tables[idx_b], self._tables[idx_a]
        self._data[idx_a], self._data[idx_b] = self._data[idx_b], self._data[idx_a]

    def reorder_clients(self, order: list[int]) -> None:
        """Rearrange the drop tabs into *order*, where ``order[i]`` is the old index now at *i*.

        The two-``moveTab`` trick ``swap_clients`` uses does not generalise to an arbitrary
        permutation, so the tab bar is walked once selection-sort style: for each target
        position, find where the tab that belongs there currently sits and move it. Tables
        and backing data are permuted in the same breath, since a table separated from its
        dict is a client reading another client's drops.

        Refused outright when *order* does not describe the current tab list -- the same
        bargain ``card_columns.apply_order`` makes, and for the same reason: the column was
        built from a client list read some moments earlier.

        The selected tab is restored afterwards. The gesture behind this was about player
        order, not about which drop table the user was reading.
        """
        n = len(self._tables)
        if sorted(order) != list(range(n)):
            return
        if order == list(range(n)):
            return

        selected = self._tabs.currentIndex()
        bar = self._tabs.tabBar()
        # positions[j] is the old index of the tab currently sitting at position j.
        positions = list(range(n))
        for target, wanted in enumerate(order):
            source = positions.index(wanted)
            if source == target:
                continue
            bar.moveTab(source, target)
            positions.insert(target, positions.pop(source))

        self._tables = [self._tables[i] for i in order]
        self._data = [self._data[i] for i in order]
        # Pending rebuilds are keyed by client index, which every one of them just changed.
        self._pending = {order.index(i) for i in self._pending if i in order}
        if 0 <= selected < n:
            self._tabs.setCurrentIndex(order.index(selected))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        self._flush_scheduled = False
        search = self._search.text()
        for idx in self._pending:
            self._rebuild_table(idx, search)
        self._pending.clear()

    def _rebuild_table(self, idx: int, search: str) -> None:
        table = self._tables[idx]
        table.setUpdatesEnabled(False)
        table.setSortingEnabled(False)  # suppress per-row re-sorts during bulk insert
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            for item, qty in self._data[idx].items():
                row = table.rowCount()
                table.insertRow(row)
                table.setItem(row, 0, QTableWidgetItem(item))
                table.setItem(row, 1, _qty_item(qty))
        finally:
            table.blockSignals(False)
            table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
            table.setSortingEnabled(True)  # Qt sorts once in C++ when re-enabled
            table.setUpdatesEnabled(True)
        if search or self._keep_only.isChecked():
            self._filter_table(table, search)

    def _resolve_keep_list(self) -> list[str]:
        """The keywords quicksell would spare items by, for the bound bot.

        Empty for a bot that never sells — one with no quicksell node, and one whose
        Enable Quicksell is off. Both keep every drop, so filtering to "what was
        kept" would hide nothing and mean nothing; an empty list is what turns the
        checkbox off rather than showing a filter that cannot answer.

        Not the keep list as typed: the implied keywords are in it, so a socket
        wrench a user chose not to sell shows up alongside what they listed.
        """
        if self._config is None:
            return []
        if quicksell.OPTION_KEEP_LIST not in bot_options.required_option_specs(self._config):
            return []
        options = quicksell.resolve_options_for(self._config)
        if not options["enabled"]:
            return []
        return quicksell.effective_keep_list(options)

    def _apply_keep_list_state(self) -> None:
        """Enable the filter only when there is a keep list, and say so either way.

        Ticked with nothing to filter to, the box would hide every row, which reads
        as a broken table rather than as an answer — so it goes gray, its tooltip
        says why, and a tick left over from the previous bot is dropped.
        """
        has_keep_list = bool(self._keep_list)
        self._keep_only.setEnabled(has_keep_list)
        self._keep_only.setToolTip(_KEEP_LIST_HINT if has_keep_list else _NO_KEEP_LIST_HINT)
        if not has_keep_list and self._keep_only.isChecked():
            self._keep_only.setChecked(False)

    def _on_keep_only_changed(self, _state) -> None:
        self._on_search_changed(self._search.text())

    def _on_search_changed(self, text: str) -> None:
        idx = self._tabs.currentIndex()
        if 0 <= idx < len(self._tables):
            self._filter_table(self._tables[idx], text)

    def _on_tab_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._tables):
            self._filter_table(self._tables[idx], self._search.text())

    def _filter_table(self, table: QTableWidget, text: str) -> None:
        keep_only = self._keep_only.isChecked()
        for row in range(table.rowCount()):
            item_cell = table.item(row, 0)
            visible = item_cell is not None and drop_row_visible(item_cell.text(), text, self._keep_list, keep_only)
            table.setRowHidden(row, not visible)

    def _export_csv(self) -> None:
        idx = self._tabs.currentIndex()
        if idx < 0 or idx >= len(self._tables):
            return
        table = self._tables[idx]
        tab_name = self._tabs.tabText(idx)
        path, _ = QFileDialog.getSaveFileName(self, f"Export {tab_name} Drops", "", "CSV Files (*.csv)")
        if not path:
            return
        if not path.endswith(".csv"):
            path += ".csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Item", "Qty"])
            for row in range(table.rowCount()):
                writer.writerow([table.item(row, col).text() for col in range(2)])
