"""Right sidebar panel for the Run view: per-client drop tables with CSV export."""

import csv

from PyQt6.QtWidgets import (
    QFileDialog, QHeaderView, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt

from bartlebot.gui.sidebar_section import SidebarSection


class DropPanel(QWidget):
    """Per-client tabbed drop-tracking table with CSV export."""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        section = SidebarSection("Drops")

        body_layout = QVBoxLayout()
        body_layout.setSpacing(4)

        self._tabs = QTabWidget()
        body_layout.addWidget(self._tabs, 1)

        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export_csv)
        body_layout.addWidget(export_btn)

        section.set_content_layout(body_layout)
        outer.addWidget(section)

        self.setMinimumWidth(190)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup_clients(self, count: int) -> None:
        """Create one tab per client, replacing any existing tabs."""
        self._tabs.clear()
        for i in range(count):
            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Item", "Qty"])
            header = table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.verticalHeader().setVisible(False)
            self._tabs.addTab(table, f"Client {i + 1}")

    def add_drop(self, item: str, quantity: int, client_index: int = 0) -> None:
        table = self._tabs.widget(client_index)
        if not isinstance(table, QTableWidget):
            return
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(item))
        qty_item = QTableWidgetItem(str(quantity))
        qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, 1, qty_item)

    def clear(self) -> None:
        for i in range(self._tabs.count()):
            table = self._tabs.widget(i)
            if isinstance(table, QTableWidget):
                table.setRowCount(0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _export_csv(self) -> None:
        table = self._tabs.currentWidget()
        if not isinstance(table, QTableWidget):
            return
        tab_name = self._tabs.tabText(self._tabs.currentIndex())
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {tab_name} Drops", "", "CSV Files (*.csv)"
        )
        if not path:
            return
        if not path.endswith(".csv"):
            path += ".csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Item", "Qty"])
            for row in range(table.rowCount()):
                writer.writerow([
                    table.item(row, col).text()
                    for col in range(2)
                ])
