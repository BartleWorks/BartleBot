"""Tests for the Drops tab's per-client tables.

Real widget tests (QApplication required): what is under test is that the Qty
column sorts as a number rather than as text, that the numeric cell still reads
back as text for the CSV export, and that the keep-list filter shows exactly the
rows quicksell would spare.
"""

import pytest
from PyQt6.QtCore import Qt

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.sidebar.sections.drop_section import DropSection, drop_row_visible
from bartlebot.nodes.utility import quicksell
from bartlebot.settings_manager import SettingsManager


@pytest.fixture(autouse=True)
def _quicksell_on():
    """Quicksell is off by default (#262); these are about a bot that sells."""
    SettingsManager.instance().set_general("default_quicksell_enabled", True)


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


# ---------------------------------------------------------------------------
# Keep-list filter (#72)
# ---------------------------------------------------------------------------


def _keep_list_bot(*keywords: str, node_type: str = "quicksell") -> BotConfig:
    """A one-node quicksell bot whose author declares *keywords* as the keep list.

    *node_type* is there for the bot that never sells: a keep list belongs to a
    quicksell node, and a graph without one has none however its options read.
    """
    return BotConfig.model_validate(
        {
            "id": "drop-section-bot",
            "name": "Drop Section Bot",
            "initial": "s0",
            "recovery_state": "s0",
            "states": [{"id": "s0", "node_type": node_type}],
            "transitions": [],
            "options": [{"name": quicksell.OPTION_KEEP_LIST, "type": "drop_keep_list", "default": list(keywords)}],
        }
    )


def _visible(table):
    return [table.item(r, 0).text() for r in range(table.rowCount()) if not table.isRowHidden(r)]


# -- the pure rule ----------------------------------------------------------


def test_row_is_visible_when_nothing_filters_it():
    assert drop_row_visible("Amber", "", [], keep_only=False)


def test_keep_only_hides_a_row_no_keyword_matches():
    assert not drop_row_visible("Mandrake", "", ["amber"], keep_only=True)


def test_keep_only_matches_the_way_quicksell_does():
    """Case-insensitive substring, so the rows shown are the ones the node spares."""
    assert drop_row_visible("Amber Ring", "", ["amber"], keep_only=True)


def test_an_empty_keep_list_hides_every_row_when_ticked():
    assert not drop_row_visible("Amber", "", [], keep_only=True)


def test_search_and_keep_only_compose_rather_than_override():
    keywords = ["amber", "onyx"]
    assert drop_row_visible("Amber", "amb", keywords, keep_only=True)
    assert not drop_row_visible("Onyx", "amb", keywords, keep_only=True)
    assert not drop_row_visible("Mandrake", "man", keywords, keep_only=True)


# -- the widget -------------------------------------------------------------


def test_ticking_keep_list_only_hides_the_drops_that_would_be_sold(qapp, section):
    section.set_config(_keep_list_bot("Amber"))
    table = _fill(qapp, section, {"Amber": 1, "Amber Ring": 1, "Mandrake": 3})

    section._keep_only.setChecked(True)

    assert sorted(_visible(table)) == ["Amber", "Amber Ring"]


def test_unticking_brings_the_rest_back(qapp, section):
    section.set_config(_keep_list_bot("Amber"))
    table = _fill(qapp, section, {"Amber": 1, "Mandrake": 3})

    section._keep_only.setChecked(True)
    section._keep_only.setChecked(False)

    assert sorted(_visible(table)) == ["Amber", "Mandrake"]


def test_a_drop_arriving_while_the_filter_is_on_is_filtered_too(qapp, section):
    section.set_config(_keep_list_bot("Amber"))
    _fill(qapp, section, {"Amber": 1})
    section._keep_only.setChecked(True)

    table = _fill(qapp, section, {"Mandrake": 1})

    assert _visible(table) == ["Amber"]


def test_the_filter_is_disabled_for_a_bot_that_never_quicksells(qapp, section):
    section.set_config(_keep_list_bot("Amber", node_type="wait"))

    assert not section._keep_only.isEnabled()


def test_the_filter_is_disabled_when_selling_is_switched_off(qapp, section):
    config = _keep_list_bot("Amber")
    SettingsManager.instance().set_option_override(config.id, quicksell.OPTION_ENABLED, False)

    section.set_config(config)

    assert not section._keep_only.isEnabled()


def test_an_implied_keyword_keeps_its_rows_shown(qapp, section):
    """Sell Socket Wrenches off is a keep-list entry the user never typed."""
    config = _keep_list_bot("Amber")
    SettingsManager.instance().set_option_override(
        config.id, quicksell.SELL_FLAG_OPTIONS["sell_socket_wrenches"], False
    )
    section.set_config(config)
    table = _fill(qapp, section, {"Amber": 1, "Socket Wrench": 2, "Mandrake": 3})

    section._keep_only.setChecked(True)

    assert sorted(_visible(table)) == ["Amber", "Socket Wrench"]


def test_the_filter_is_enabled_once_the_bot_has_a_keep_list(qapp, section):
    section.set_config(_keep_list_bot("Amber"))

    assert section._keep_only.isEnabled()


def test_binding_a_bot_with_no_keep_list_unticks_the_filter(qapp, section):
    """A ticked filter with nothing to filter to hides every row, which reads as a
    broken table rather than as an answer."""
    section.set_config(_keep_list_bot("Amber"))
    table = _fill(qapp, section, {"Amber": 1, "Mandrake": 1})
    section._keep_only.setChecked(True)

    section.set_config(_keep_list_bot("Amber", node_type="wait"))

    assert not section._keep_only.isChecked()
    assert sorted(_visible(table)) == ["Amber", "Mandrake"]


def test_an_unbound_section_offers_no_keep_list_filter(qapp, section):
    assert not section._keep_only.isEnabled()
