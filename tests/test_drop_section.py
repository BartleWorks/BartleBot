"""Tests for the Drops tab's per-client tables.

Real widget tests (QApplication required): what is under test is that the Qty
column sorts as a number rather than as text (#116), and that the numeric cell
still reads back as text for the CSV export.
"""

import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from bartlebot.gui.sidebar.sections.drop_section import DropSection


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def section(qapp):
    widget = DropSection()
    widget.setup_clients(1)
    yield widget
    widget.deleteLater()


def _rows(table):
    return [(table.item(r, 0).text(), table.item(r, 1).text()) for r in range(table.rowCount())]


def _fill(qapp, section, drops):
    for name, qty in drops.items():
        section.add_drop(name, qty)
    qapp.processEvents()  # the table rebuild is deferred to the event loop
    return section.table(0)


# Quantities that a string sort would order 1, 10, 2, 9.
_DROPS = {"Amber": 2, "Boots of Dawn": 10, "Mandrake": 1, "Socket Wrench": 9}


def test_qty_column_sorts_numerically(qapp, section):
    table = _fill(qapp, section, _DROPS)

    table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert [qty for _, qty in _rows(table)] == ["1", "2", "9", "10"]

    table.sortItems(1, Qt.SortOrder.DescendingOrder)
    assert [qty for _, qty in _rows(table)] == ["10", "9", "2", "1"]


def test_qty_cell_reads_back_as_text_for_export(qapp, section):
    table = _fill(qapp, section, {"Amber": 10})

    assert table.item(0, 1).text() == "10"


def test_item_column_still_sorts_by_name(qapp, section):
    table = _fill(qapp, section, _DROPS)

    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    assert [name for name, _ in _rows(table)] == sorted(_DROPS)
