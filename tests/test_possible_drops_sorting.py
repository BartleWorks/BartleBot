"""Tests for the Possible Drops table's default sort (#144).

Real widget tests (QApplication required): what is under test is that the table
opens grouped by Category with names ordered inside each group, which is a
property of Qt's sort calling CategoryThenNameTreeItem.__lt__ — there is nothing
to assert about it short of building the tree and reading the rows back.
"""

import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.schema import DropModel
from bartlebot.gui.sidebar.sections.possible_drops_section import _DropsListWidget
from bartlebot.gui.widgets.drop_tree import UNCATEGORIZED_DISPLAY


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def table(qapp):
    widget = _DropsListWidget()
    yield widget
    widget.deleteLater()


def _drops(*specs) -> list[DropModel]:
    """Build entries from "Name" or ("Name", "Category")."""
    return [DropModel(name=s[0], category=s[1]) if isinstance(s, tuple) else DropModel(name=s) for s in specs]


# Deliberately shuffled, and with a lowercase name in each category so a raw
# string sort would file it after every capitalized one.
_DROPS = _drops(
    ("Zafaria Boots", "Gear"),
    ("amber", "Reagents"),
    ("Wand of Fire", "Gear"),
    ("Sunstone", "Reagents"),
    "Mystery Item",
    ("boots of Haste", "Gear"),
    "Another Mystery",
)


def _names(table) -> list[str]:
    return [d.name for d in table.displayed_items()]


def _categories(table) -> list[str]:
    return [d.category or UNCATEGORIZED_DISPLAY for d in table.displayed_items()]


# ---------------------------------------------------------------------------
# Default sort
# ---------------------------------------------------------------------------


def test_table_opens_sorted_by_category(table):
    assert table.sort_column() == table.COL_CATEGORY


def test_default_sort_groups_by_category_then_name(table):
    table.set_items(_DROPS)

    assert _names(table) == [
        # Gear, by name, case-insensitively
        "boots of Haste",
        "Wand of Fire",
        "Zafaria Boots",
        # Reagents, likewise
        "amber",
        "Sunstone",
        # Uncategorized last
        "Another Mystery",
        "Mystery Item",
    ]


def test_uncategorized_rows_stay_together_at_the_end(table):
    table.set_items(_DROPS)

    categories = _categories(table)
    uncategorized = [i for i, c in enumerate(categories) if c == UNCATEGORIZED_DISPLAY]
    assert uncategorized == [len(categories) - 2, len(categories) - 1]


def test_descending_category_sort_reverses_the_whole_key(table):
    table.set_items(_DROPS)
    table.sort_by_column(table.COL_CATEGORY, Qt.SortOrder.DescendingOrder)

    assert _names(table) == [
        "Mystery Item",
        "Another Mystery",
        "Sunstone",
        "amber",
        "Zafaria Boots",
        "Wand of Fire",
        "boots of Haste",
    ]


# ---------------------------------------------------------------------------
# The Item column is untouched
# ---------------------------------------------------------------------------


def test_sorting_by_item_still_ignores_category(table):
    table.set_items(_DROPS)
    table.sort_by_column(table.COL_NAME, Qt.SortOrder.AscendingOrder)

    assert _names(table) == sorted((d.name for d in _DROPS), key=str.casefold)


def test_storage_order_is_unaffected_by_the_view(table):
    """get_items() is name order whatever the table is showing — the bot file's order."""
    table.set_items(_DROPS)

    assert [d.name for d in table.get_items()] == sorted((d.name for d in _DROPS), key=str.casefold)
