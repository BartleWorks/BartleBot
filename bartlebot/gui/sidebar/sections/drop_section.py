"""Drops tab content: per-client drop tables with CSV export."""

import csv

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.search_field import SearchLineEdit


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

        self._tables: list[QTableWidget] = []
        self._data: list[dict[str, int]] = []  # backing store; one dict per client
        self._pending: set[int] = set()  # client indices that need a table rebuild
        self._flush_scheduled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._search = SearchLineEdit("Search items…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search)

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
        search = self._search.text().lower()
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
        if search:
            self._filter_table(table, search)

    def _on_search_changed(self, text: str) -> None:
        idx = self._tabs.currentIndex()
        if 0 <= idx < len(self._tables):
            self._filter_table(self._tables[idx], text)

    def _on_tab_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._tables):
            self._filter_table(self._tables[idx], self._search.text())

    def _filter_table(self, table: QTableWidget, text: str) -> None:
        text = text.lower()
        for row in range(table.rowCount()):
            item_cell = table.item(row, 0)
            visible = not text or (item_cell is not None and text in item_cell.text().lower())
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
