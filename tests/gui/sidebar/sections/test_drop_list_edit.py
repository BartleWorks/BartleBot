"""Tests for the Possible Drops edit, merge, and header helpers."""

from bartlebot.bot.schema import DropModel
from bartlebot.gui.sidebar.sections.possible_drops_section import (
    _DropEditDialog,
    drops_header_text,
    edit_drop,
    merge_drops,
)
from bartlebot.gui.widgets.buttons.action_button import ActionButton
from bartlebot.nodes.utility.quicksell import DROP_CATEGORIES


def _drops(*specs) -> list[DropModel]:
    """Build entries from "Name" or ("Name", "Category")."""
    return [DropModel(name=s[0], category=s[1]) if isinstance(s, tuple) else DropModel(name=s) for s in specs]


# ---------------------------------------------------------------------------
# edit_drop
# ---------------------------------------------------------------------------


def test_renames_entry_in_place():
    result = edit_drop(_drops("Amber", "Boots", "Cloak"), 1, "Bracers", None)
    assert [d.name for d in result] == ["Amber", "Bracers", "Cloak"]


def test_passing_the_entrys_own_category_keeps_it():
    result = edit_drop(_drops(("Amber", "Reagents")), 0, "Ambers", "Reagents")
    assert result == [DropModel(name="Ambers", category="Reagents")]


def test_sets_a_category_on_an_uncategorized_drop():
    result = edit_drop(_drops("Amber"), 0, "Amber", "Reagents")
    assert result == [DropModel(name="Amber", category="Reagents")]


def test_clears_a_category_back_to_none():
    result = edit_drop(_drops(("Amber", "Reagents")), 0, "Amber", None)
    assert result == [DropModel(name="Amber", category=None)]


def test_gaining_a_category_re_derives_the_in_game_name():
    # The school suffix on a jewel is only strippable once we know it is a jewel.
    result = edit_drop(_drops("Fire Trap Opal (Star)"), 0, "Fire Trap Opal (Star)", "Jewels")
    assert result[0].match_name == "Fire Trap Opal"


def test_losing_a_category_re_derives_the_in_game_name():
    entries = [DropModel(name="Fire Trap Opal (Star)", category="Jewels", match_name="Fire Trap Opal")]
    result = edit_drop(entries, 0, "Fire Trap Opal (Star)", None)
    assert result[0].match_name is None


def test_strips_surrounding_whitespace():
    assert edit_drop(_drops("Amber"), 0, "  Sunstone  ", None) == _drops("Sunstone")


def test_blank_text_leaves_list_unchanged():
    entries = _drops("Amber", "Boots")
    assert edit_drop(entries, 0, "   ", "Reagents") == entries


def test_out_of_range_index_leaves_list_unchanged():
    entries = _drops("Amber", "Boots")
    assert edit_drop(entries, 5, "Cloak", None) == entries
    assert edit_drop(entries, -1, "Cloak", None) == entries
    assert edit_drop([], 0, "Cloak", None) == []


def test_rename_onto_existing_name_collapses_duplicates():
    result = edit_drop(_drops("Amber", "Boots", "Cloak"), 2, "Amber", None)
    assert [d.name for d in result] == ["Amber", "Boots"]


def test_collapse_keeps_the_first_entry_and_its_category():
    result = edit_drop(_drops(("Amber", "Reagents"), ("Cloak", "Robes")), 1, "Amber", "Robes")
    assert result == [DropModel(name="Amber", category="Reagents")]


def test_returns_a_copy_not_the_original_list():
    entries = _drops("Amber")
    assert edit_drop(entries, 0, "", None) is not entries


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


# ---------------------------------------------------------------------------
# The Add / Edit dialog's category dropdown
#
# Real widget tests: what is under test is the vocabulary the dropdown offers and
# which entry it opens on, which is the whole of #61 that logic cannot cover.
# ---------------------------------------------------------------------------


def _entries(dialog):
    return [dialog._category.itemData(i) for i in range(dialog._category.count())]


def test_the_dropdown_offers_every_category_plus_uncategorized(qapp):
    dialog = _DropEditDialog()

    assert _entries(dialog) == [None, *DROP_CATEGORIES]


def test_it_opens_on_the_drops_own_category(qapp):
    dialog = _DropEditDialog(name="Amber", category="Reagents")

    assert dialog.category == "Reagents"


def test_an_uncategorized_drop_opens_on_the_uncategorized_entry(qapp):
    dialog = _DropEditDialog(name="Amber")

    assert dialog.category is None


def test_a_category_the_vocabulary_does_not_know_is_still_offered(qapp):
    """A wiki import can produce a heading nobody has classified yet. Editing such a
    drop's name must not silently strip the category off it."""
    dialog = _DropEditDialog(name="Curio", category="Curios")

    assert _entries(dialog) == [None, *DROP_CATEGORIES, "Curios"]
    assert dialog.category == "Curios"


def test_the_name_is_stripped(qapp):
    dialog = _DropEditDialog(name="  Amber  ")

    assert dialog.drop_name == "Amber"


def test_save_is_off_until_the_item_has_a_name(qapp):
    dialog = _DropEditDialog()
    save = next(b for b in dialog.findChildren(ActionButton) if b.text() == "Save")

    assert not save.isEnabled()

    dialog._name.setText("Amber")

    assert save.isEnabled()
