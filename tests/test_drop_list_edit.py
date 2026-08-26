"""Tests for the Possible Drops rename, merge, and header helpers."""

from bartlebot.bot.schema import DropModel
from bartlebot.gui.sidebar.sections.possible_drops_section import drops_header_text, merge_drops, rename_drop


def _drops(*specs) -> list[DropModel]:
    """Build entries from "Name" or ("Name", "Category")."""
    return [DropModel(name=s[0], category=s[1]) if isinstance(s, tuple) else DropModel(name=s) for s in specs]


# ---------------------------------------------------------------------------
# rename_drop
# ---------------------------------------------------------------------------


def test_renames_entry_in_place():
    result = rename_drop(_drops("Amber", "Boots", "Cloak"), 1, "Bracers")
    assert [d.name for d in result] == ["Amber", "Bracers", "Cloak"]


def test_rename_keeps_the_category():
    result = rename_drop(_drops(("Amber", "Reagents")), 0, "Ambers")
    assert result == [DropModel(name="Ambers", category="Reagents")]


def test_strips_surrounding_whitespace():
    assert rename_drop(_drops("Amber"), 0, "  Sunstone  ") == _drops("Sunstone")


def test_blank_text_leaves_list_unchanged():
    entries = _drops("Amber", "Boots")
    assert rename_drop(entries, 0, "   ") == entries


def test_out_of_range_index_leaves_list_unchanged():
    entries = _drops("Amber", "Boots")
    assert rename_drop(entries, 5, "Cloak") == entries
    assert rename_drop(entries, -1, "Cloak") == entries
    assert rename_drop([], 0, "Cloak") == []


def test_rename_onto_existing_name_collapses_duplicates():
    result = rename_drop(_drops("Amber", "Boots", "Cloak"), 2, "Amber")
    assert [d.name for d in result] == ["Amber", "Boots"]


def test_collapse_keeps_the_first_entry_and_its_category():
    result = rename_drop(_drops(("Amber", "Reagents"), ("Cloak", "Robes")), 1, "Amber")
    assert result == [DropModel(name="Amber", category="Reagents")]


def test_returns_a_copy_not_the_original_list():
    entries = _drops("Amber")
    assert rename_drop(entries, 0, "") is not entries


# ---------------------------------------------------------------------------
# merge_drops
# ---------------------------------------------------------------------------


def test_merge_appends_new_drops():
    result = merge_drops(_drops("Amber"), _drops(("Boots of Dawn", "Boots")))
    assert result == _drops("Amber", ("Boots of Dawn", "Boots"))


def test_merge_ignores_names_already_listed():
    existing = _drops(("Amber", "Reagents"))
    assert merge_drops(existing, _drops(("Amber", "Reagents"))) == existing


def test_merge_backfills_a_missing_category():
    """A re-import is the only way drops added before categories existed get one."""
    result = merge_drops(_drops("Amber"), _drops(("Amber", "Reagents")))
    assert result == [DropModel(name="Amber", category="Reagents")]


def test_merge_does_not_overwrite_an_existing_category():
    existing = _drops(("Amber", "Reagents"))
    assert merge_drops(existing, _drops(("Amber", "Jewels"))) == existing


def test_merge_does_not_mutate_its_inputs():
    existing = _drops("Amber")
    merge_drops(existing, _drops(("Amber", "Reagents")))
    assert existing[0].category is None


def test_merge_preserves_existing_order():
    result = merge_drops(_drops("Cloak", "Amber"), _drops("Boots"))
    assert [d.name for d in result] == ["Cloak", "Amber", "Boots"]


# ---------------------------------------------------------------------------
# drops_header_text
# ---------------------------------------------------------------------------


def test_header_is_bare_when_there_are_no_drops():
    assert drops_header_text([]) == "Possible Drops"


def test_header_counts_drops():
    assert drops_header_text(_drops(("Amber", "Reagents"), ("Boots", "Boots"))) == "Possible Drops (2)"


def test_header_calls_out_uncategorized_drops():
    text = drops_header_text(_drops(("Amber", "Reagents"), "Boots"))
    assert text == "Possible Drops (2 · 1 uncategorized)"


def test_header_reports_a_fully_uncategorized_bot():
    # The state every bot is in until it is re-imported — this is the whole prompt.
    assert drops_header_text(_drops("Amber", "Boots")) == "Possible Drops (2 · 2 uncategorized)"


def test_header_treats_a_blank_category_as_missing():
    assert drops_header_text([DropModel(name="Amber", category="")]) == "Possible Drops (1 · 1 uncategorized)"
