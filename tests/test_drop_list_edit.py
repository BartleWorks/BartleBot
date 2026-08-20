"""Tests for the Possible Drops rename helper."""

from bartlebot.gui.sidebar.sections.possible_drops_section import rename_drop


def test_renames_entry_in_place():
    assert rename_drop(["Amber", "Boots", "Cloak"], 1, "Bracers") == ["Amber", "Bracers", "Cloak"]


def test_strips_surrounding_whitespace():
    assert rename_drop(["Amber"], 0, "  Sunstone  ") == ["Sunstone"]


def test_blank_text_leaves_list_unchanged():
    items = ["Amber", "Boots"]
    assert rename_drop(items, 0, "   ") == items


def test_out_of_range_index_leaves_list_unchanged():
    items = ["Amber", "Boots"]
    assert rename_drop(items, 5, "Cloak") == items
    assert rename_drop(items, -1, "Cloak") == items
    assert rename_drop([], 0, "Cloak") == []


def test_rename_onto_existing_name_collapses_duplicates():
    assert rename_drop(["Amber", "Boots", "Cloak"], 2, "Amber") == ["Amber", "Boots"]


def test_returns_a_copy_not_the_original_list():
    items = ["Amber"]
    assert rename_drop(items, 0, "") is not items
