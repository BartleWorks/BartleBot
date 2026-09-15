"""Tests for bartlebot.spell_schools — a card's school, and the game's own order.

Most of these drive a snapshot written into tmp_path, so they assert the rules rather
than the current contents of ``data/spell_schools.json``. The few that read the real
snapshot say so, and check cards whose school is a fact about the game rather than
about any particular patch.
"""

from __future__ import annotations

import json

import pytest

from bartlebot import spell_schools
from bartlebot.spell_schools import (
    SCHOOL_ORDER,
    UNKNOWN_SCHOOL_INDEX,
    deck_sort_key,
    school_index,
    school_of,
)


@pytest.fixture
def snapshot(tmp_path):
    """Write a snapshot and hand back its path, for the `path` argument."""

    def write(schools: dict[str, str]):
        path = tmp_path / "spell_schools.json"
        path.write_text(json.dumps({"schools": schools}), encoding="utf-8")
        return path

    return write


def test_the_order_is_the_games_and_not_alphabetical():
    # Reproduced verbatim from the deck window; sorting it would be the bug.
    assert SCHOOL_ORDER == (
        "Ice",
        "Sun",
        "Life",
        "Fire",
        "Myth",
        "Moon",
        "Star",
        "Death",
        "Storm",
        "Balance",
        "Shadow",
    )
    assert list(SCHOOL_ORDER) != sorted(SCHOOL_ORDER)


def test_a_known_card_reports_its_school(snapshot):
    path = snapshot({"Tempest": "Storm"})

    assert school_of("Tempest", path) == "Storm"
    assert school_index("Tempest", path) == SCHOOL_ORDER.index("Storm")


def test_a_card_the_snapshot_does_not_name_has_no_school(snapshot):
    path = snapshot({"Tempest": "Storm"})

    assert school_of("Some Card Added Last Patch", path) is None
    assert school_index("Some Card Added Last Patch", path) == UNKNOWN_SCHOOL_INDEX


def test_a_non_combat_school_is_treated_as_no_school(snapshot):
    # CastleMagic, Gardening, Fishing and friends cannot be in a combat deck, so they
    # get no place in the order and sort with the unknowns.
    path = snapshot({"Some Castle Spell": "CastleMagic", "Some Plant": "Gardening"})

    assert school_of("Some Castle Spell", path) is None
    assert school_index("Some Plant", path) == UNKNOWN_SCHOOL_INDEX


def test_an_unreadable_snapshot_leaves_every_card_unknown(tmp_path):
    path = tmp_path / "spell_schools.json"
    path.write_text("{not json", encoding="utf-8")

    assert school_of("Tempest", path) is None


def test_cards_sort_by_school_before_name(snapshot):
    # Names that disagree with the school order, so it is clear which one decides:
    # alphabetically "Aaa Storm" leads, but Ice comes before Storm in the game's order.
    path = snapshot({"Aaa Storm": "Storm", "Zzz Ice": "Ice"})
    keys = sorted(["Aaa Storm", "Zzz Ice"], key=lambda t: deck_sort_key(t, t, path))

    assert keys == ["Zzz Ice", "Aaa Storm"]


def test_cards_of_one_school_sort_alphabetically(snapshot):
    path = snapshot({"B Card": "Fire", "A Card": "Fire", "C Card": "Fire"})

    names = ["C Card", "A Card", "B Card"]
    assert sorted(names, key=lambda t: deck_sort_key(t, t, path)) == ["A Card", "B Card", "C Card"]


def test_sorting_is_by_the_name_on_screen_not_the_stored_one(snapshot):
    # "Soul Burner" displays as "Ammut": ordering by the template name would file it
    # under S, where the reader has no way to know to look for it.
    path = snapshot({"Soul Burner": "Death", "Banshee": "Death"})

    entries = [("Soul Burner", "Ammut"), ("Banshee", "Banshee")]
    ordered = sorted(entries, key=lambda e: deck_sort_key(e[0], e[1], path))

    assert [display for _, display in ordered] == ["Ammut", "Banshee"]


def test_unknown_cards_sort_after_every_school(snapshot):
    path = snapshot({"Shadow Card": "Shadow"})

    names = ["Unknown Card", "Shadow Card"]
    assert sorted(names, key=lambda t: deck_sort_key(t, t, path)) == ["Shadow Card", "Unknown Card"]


# -- against the checked-in snapshot -----------------------------------------


def test_the_shipped_snapshot_loads_and_knows_the_schools():
    spell_schools._load_schools.cache_clear()

    assert school_of("Tempest") == "Storm"
    assert school_of("Tower Shield") == "Ice"
    assert school_of("Epic") == "Sun"


def test_a_name_the_game_files_twice_keeps_its_real_school():
    """41 template names exist under both CastleMagic and a real school.

    A deck stores the bare name, so the snapshot has to pick one, and a card in a
    combat deck is never the castle-magic copy.
    """
    assert school_of("Banshee") == "Death"
    assert school_of("Blizzard") == "Ice"
