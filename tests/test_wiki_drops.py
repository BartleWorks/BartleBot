"""Wiki item name -> in-game item name.

The rules are a lookup table over trailing "(...)" suffixes, and the failure that
matters is the silent one: a suffix nobody classified survives into a keep-list
entry, matches nothing the game shows, and the item sells. So these tests pin both
halves — what gets stripped, and just as importantly what does not.
"""

from __future__ import annotations

import pytest

from bartlebot.wiki_drops import (
    AMBIGUOUS_JEWEL_SHAPES,
    KIND_JEWEL_SHAPE,
    KIND_KNOWN_KEPT,
    KIND_LEVEL,
    KIND_LEVEL_AND_SCHOOL,
    KIND_LEVEL_REQUIREMENT,
    KIND_NONE,
    KIND_SCHOOL,
    KIND_TIER,
    KIND_UNCLASSIFIED,
    SCHOOLS,
    UNAMBIGUOUS_JEWEL_SHAPES,
    classify_suffix,
    in_game_name,
    match_name_for,
    suffix_of,
)

# ---------------------------------------------------------------------------
# Suffix extraction
# ---------------------------------------------------------------------------


def test_no_suffix_reads_as_none():
    assert suffix_of("Amber") is None


def test_suffix_is_the_trailing_parenthetical():
    assert suffix_of("Balance Crushing Pin (60, Death)") == "60, Death"


def test_only_a_trailing_parenthetical_counts():
    # A parenthetical in the middle of a name is part of the name.
    assert suffix_of("Pixie Queen 29 (No Card) Deck") is None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "kind"),
    [
        (None, KIND_NONE),
        ("60, Death", KIND_LEVEL_AND_SCHOOL),
        ("140", KIND_LEVEL),
        ("Level 90+", KIND_LEVEL_REQUIREMENT),
        ("Tier 2b", KIND_TIER),
        ("2 Pip", KIND_TIER),
        ("Triangle", KIND_JEWEL_SHAPE),
        ("Ice", KIND_SCHOOL),
        ("Star", KIND_SCHOOL),
        ("Seed", KIND_KNOWN_KEPT),
        ("Something New", KIND_UNCLASSIFIED),
    ],
)
def test_classify_suffix(suffix, kind):
    assert classify_suffix(suffix) == kind


def test_the_five_jewel_shapes_are_accounted_for():
    assert UNAMBIGUOUS_JEWEL_SHAPES | AMBIGUOUS_JEWEL_SHAPES == {"Tear", "Circle", "Square", "Triangle", "Star"}


def test_only_star_collides_with_a_school():
    assert AMBIGUOUS_JEWEL_SHAPES <= SCHOOLS
    assert not UNAMBIGUOUS_JEWEL_SHAPES & SCHOOLS


# ---------------------------------------------------------------------------
# Stripped: the game does not show these
# ---------------------------------------------------------------------------


def test_pin_keeps_its_level_and_loses_its_school():
    assert in_game_name("Balance Crushing Pin (60, Death)") == "Balance Crushing Pin (60)"


def test_every_school_of_one_pin_collapses_to_one_name():
    names = {in_game_name(f"Balance Crushing Pin (140, {school})") for school in ("Fire", "Ice", "Storm", "Myth")}
    assert names == {"Balance Crushing Pin (140)"}


def test_pin_school_is_stripped_without_a_category():
    # Only pins take this form, so the rule does not need the category — which
    # matters, because most of the library is uncategorized.
    assert in_game_name("Balance Blocking Pin (150, Ice)", None) == "Balance Blocking Pin (150)"


def test_level_requirement_is_stripped():
    assert in_game_name("Ring of Gorgon Imprisonment (Level 90+)", "Rings") == "Ring of Gorgon Imprisonment"


def test_unambiguous_jewel_shape_is_stripped_without_a_category():
    assert in_game_name("Fire Trap Ruby (Triangle)") == "Fire Trap Ruby"


def test_jewel_school_is_stripped_for_a_jewel():
    assert in_game_name("Bright Critical Amethyst +44 (Ice)", "Jewels") == "Bright Critical Amethyst +44"


# ---------------------------------------------------------------------------
# Left alone: shown in-game, or unverified
# ---------------------------------------------------------------------------


def test_a_pins_level_alone_is_shown_in_game():
    assert in_game_name("Balance Accurate Pin (30)") == "Balance Accurate Pin (30)"


def test_school_on_a_non_jewel_is_left_alone():
    # A treasure card. Never quicksold, and the in-game form is unverified, so the
    # conservative answer is to change nothing.
    assert in_game_name("Mutate Snowman (Fire)") == "Mutate Snowman (Fire)"


def test_school_without_a_category_is_left_alone():
    assert in_game_name("Bright Critical Amethyst +44 (Ice)", None) == "Bright Critical Amethyst +44 (Ice)"


def test_star_on_a_jewel_is_stripped_but_only_with_the_category():
    assert in_game_name("Some Opal (Star)", "Jewels") == "Some Opal"
    assert in_game_name("Some Card (Star)", None) == "Some Card (Star)"


def test_tier_is_left_alone():
    assert in_game_name("Evil Snowman (Tier 2b)") == "Evil Snowman (Tier 2b)"


def test_an_unclassified_suffix_is_left_alone():
    assert in_game_name("Widget (Brand New Thing)") == "Widget (Brand New Thing)"


def test_a_name_with_no_suffix_is_returned_unchanged():
    assert in_game_name("No Pain, No Gain Onyx") == "No Pain, No Gain Onyx"


def test_normalising_is_idempotent():
    once = in_game_name("Balance Crushing Pin (60, Death)")
    assert in_game_name(once) == once


# ---------------------------------------------------------------------------
# match_name_for
# ---------------------------------------------------------------------------


def test_match_name_is_none_when_the_names_agree():
    assert match_name_for("Amber", "Reagents") is None


def test_match_name_is_the_in_game_form_when_they_differ():
    assert match_name_for("Balance Crushing Pin (60, Death)") == "Balance Crushing Pin (60)"


def test_pins_of_different_levels_stay_distinct():
    # The level *is* shown in-game, so it must survive normalisation: nine shipped
    # bots drop the same pin at two levels, and collapsing them would mean keeping
    # one level silently kept the other.
    names = {in_game_name(f"Fire Conversion Pin ({level})") for level in (140, 150, 160, 170)}
    assert len(names) == 4


def test_the_level_survives_when_the_school_is_stripped():
    # The rewrite of a "(level, school)" suffix keeps the level for the same reason.
    assert {in_game_name(f"Balance Accurate Pin ({lvl}, Fire)") for lvl in (30, 60)} == {
        "Balance Accurate Pin (30)",
        "Balance Accurate Pin (60)",
    }
