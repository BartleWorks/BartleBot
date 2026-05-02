"""Sidebar section: per-client drop tables with CSV export."""

import csv

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from bartlebot.gui.sidebar.sidebar_section import SidebarSection


class DropSection(SidebarSection):
    """Collapsible 'Drops' section with per-client drop tables and CSV export."""

    _expanded_v_policy = QSizePolicy.Policy.Expanding

    def __init__(self, parent=None):
        super().__init__("Drops", parent)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self._tables: list[QTableWidget] = []

        layout = QVBoxLayout()
        layout.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search items…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search)

        self._tabs = QTabWidget()
        self._tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_csv)
        layout.addWidget(export_btn)

        self.set_content_layout(layout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup_clients(self, count: int) -> None:
        """Create one tab per client, replacing any existing tabs."""
        self._tabs.clear()
        self._tables.clear()
        for i in range(count):
            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Item", "Qty"])
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.verticalHeader().setVisible(False)
            self._tables.append(table)
            self._tabs.addTab(table, f"Client {i + 1}")

    def add_drop(self, item: str, quantity: int, client_index: int = 0) -> None:
        if client_index >= len(self._tables):
            return
        table = self._tables[client_index]
        for row in range(table.rowCount()):
            item_cell = table.item(row, 0)
            if item_cell and item_cell.text() == item:
                qty_cell = table.item(row, 1)
                current = int(qty_cell.text()) if qty_cell else 0
                qty_item = QTableWidgetItem(str(current + quantity))
                qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, 1, qty_item)
                return
        name_lower = item.lower()
        row = 0
        while row < table.rowCount():
            cell = table.item(row, 0)
            if cell and cell.text().lower() > name_lower:
                break
            row += 1
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(item))
        qty_item = QTableWidgetItem(str(quantity))
        qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 1, qty_item)

    def clear_drops(self) -> None:
        for table in self._tables:
            table.setRowCount(0)
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
        # Keep _tables in sync with the new tab order
        self._tables[idx_a], self._tables[idx_b] = self._tables[idx_b], self._tables[idx_a]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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
