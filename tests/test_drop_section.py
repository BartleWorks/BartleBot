"""Tests for the Drops tab's per-client tables.

Real widget tests (QApplication required): what is under test is that the Qty
column sorts as a number rather than as text, and that the numeric cell
still reads back as text for the CSV export.
"""

import pytest
from PyQt6.QtCore import Qt

from bartlebot.gui.sidebar.sections.drop_section import DropSection


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


# ---------------------------------------------------------------------------
# Reordering
# ---------------------------------------------------------------------------


@pytest.fixture
def four(qapp):
    """Four tabs, each holding one drop that names the client it started as."""
    widget = DropSection()
    widget.setup_clients(4)
    for i in range(4):
        widget.add_drop(f"Item {i}", i + 1, client_index=i)
    qapp.processEvents()
    yield widget
    widget.deleteLater()


def _tab_labels(section):
    return [section._tabs.tabText(i) for i in range(section._tabs.count())]


def _first_item(section, idx):
    table = section.table(idx)
    return table.item(0, 0).text() if table.rowCount() else None


def test_reorder_moves_tables_data_and_labels_together(qapp, four):
    four.reorder_clients([2, 0, 3, 1])
    qapp.processEvents()

    assert _tab_labels(four) == ["Player 3", "Player 1", "Player 4", "Player 2"]
    assert [_first_item(four, i) for i in range(4)] == ["Item 2", "Item 0", "Item 3", "Item 1"]
    assert [four._data[i] for i in range(4)] == [
        {"Item 2": 3},
        {"Item 0": 1},
        {"Item 3": 4},
        {"Item 1": 2},
    ]


def test_reorder_keeps_the_widget_under_each_tab(qapp, four):
    """The table the tab bar shows is the one the backing store thinks it is showing."""
    four.reorder_clients([3, 2, 1, 0])
    qapp.processEvents()

    for i in range(4):
        assert four._tabs.widget(i) is four.table(i)


def test_reorder_keeps_the_selected_tab_selected(qapp, four):
    four._tabs.setCurrentIndex(1)

    four.reorder_clients([2, 0, 3, 1])
    qapp.processEvents()

    assert four._tabs.currentIndex() == 3  # Player 2's tab, which the drag moved to position 3
    assert _first_item(four, four._tabs.currentIndex()) == "Item 1"


def test_reorder_refuses_a_permutation_that_does_not_describe_the_tabs(qapp, four):
    four.reorder_clients([0, 1, 2])

    assert _tab_labels(four) == ["Player 1", "Player 2", "Player 3", "Player 4"]
    assert [_first_item(four, i) for i in range(4)] == ["Item 0", "Item 1", "Item 2", "Item 3"]


def test_a_drop_after_a_reorder_lands_on_the_new_index(qapp, four):
    four.reorder_clients([1, 0, 2, 3])
    qapp.processEvents()

    four.add_drop("Amber", 5, client_index=0)
    qapp.processEvents()

    assert four._data[0] == {"Item 1": 2, "Amber": 5}
