"""Tests for ``spell_names.name_problem`` — whether a spell name in a DSL can find a card.

Against a synthetic snapshot, built to have the shapes the real one does: a card whose
in-game name shares no words with its internal one, a mob spell that happens to contain
that in-game name, and a card only one of whose variants is spelled differently.
``test_dsl_validation.py`` runs the real snapshot through the same rules.
"""

from __future__ import annotations

import json

import pytest

from bartlebot.game import spell_names
from bartlebot.game.spell_names import name_problem

_SNAPSHOT = {
    "Storm Lord": "Storm Lord",
    "Storm Lord TC": "Storm Lord",
    "Pet - Storm Lord": "Storm Lord",
    # Fire Elemental is the card Minion Fire; one mob's spell contains the in-game name.
    "Minion Fire": "Fire Elemental",
    "Minion Fire TC": "Fire Elemental",
    "Pet - Minion Fire": "Fire Elemental",
    "Fire Elemental - MOB": "Fire Elemental",
    "Soul Burner": "Ammut",
    "Soul Burner - T02 - A": "Ammut",
    # Only the amulet is spelled the other way round.
    "Tower Shield": "Tower Shield",
    "Tower Shield TC": "Tower Shield",
    "Shield Tower - Amulet": "Tower Shield",
    # Known to the game, but not a card any player holds.
    "Mass Feint": "Mass Feint",
    "Feint Mass - Amulet": "Mass Feint",
}


def _clear_caches() -> None:
    spell_names._load_names.cache_clear()
    spell_names._name_index.cache_clear()
    spell_names._containing.cache_clear()


@pytest.fixture
def path(tmp_path):
    snapshot = tmp_path / "spell_names.json"
    snapshot.write_text(json.dumps({"names": _SNAPSHOT}), encoding="utf-8")
    _clear_caches()
    yield snapshot
    _clear_caches()


@pytest.mark.parametrize(
    "name", ["Storm Lord", "storm lord", "Storm", "Minion Fire", "Soul Burner", "Tower Shield", "Feint Mass"]
)
def test_an_unquoted_name_some_card_contains_is_fine(path, name):
    assert name_problem(name, literal=False, path=path) is None


def test_an_in_game_name_is_reported_with_the_card_s_name(path):
    assert name_problem("Fire Elemental", literal=False, path=path) == (
        "Fire Elemental is the name shown in-game — the card is named Minion Fire."
    )
    assert name_problem("ammut", literal=False, path=path) == (
        "Ammut is the name shown in-game — the card is named Soul Burner."
    )


def test_an_in_game_name_most_of_whose_cards_match_is_fine(path):
    # Only the amulet misses; Tower Shield still finds the card and its treasure card.
    assert name_problem("Tower Shield", literal=False, path=path) is None


@pytest.mark.parametrize(("name", "literal"), [("Mass Feint", False), ("mass feint", False), ("Mass Feint", True)])
def test_mass_feint_is_reported_though_the_game_has_a_card_by_that_name(path, name, literal):
    """Players only get it as the item card, so the likeliest line in any DSL never casts."""
    assert name_problem(name, literal=literal, path=path) == (
        "You can only get Mass Feint as the item card Feint Mass - Amulet, so write Feint Mass instead."
    )


def test_a_typo_suggests_the_card(path):
    assert name_problem("Strom Lord", literal=False, path=path) == (
        "No card has Strom Lord in its name. Did you mean Storm Lord?"
    )


def test_a_typo_of_an_in_game_name_suggests_what_to_write_instead(path):
    assert name_problem("Fire Elementl", literal=False, path=path).endswith("Did you mean Minion Fire?")


def test_a_name_like_no_card_suggests_nothing(path):
    assert name_problem("Zzyzx", literal=False, path=path) == "No card has Zzyzx in its name."


def test_a_number_read_a_digit_at_a_time_says_so(path):
    assert "read a digit at a time" in name_problem("Storm Lord 2 0 0", literal=False, path=path)


def test_a_quoted_name_has_to_be_exact(path):
    assert name_problem("Storm Lord", literal=True, path=path) is None
    assert name_problem("storm lord", literal=True, path=path) == (
        '"storm lord" has to match a card\'s name exactly, capitals too: "Storm Lord".'
    )
    assert name_problem("Storm", literal=True, path=path).startswith('No card is named exactly "Storm".')


def test_a_quoted_in_game_name_is_reported_with_the_card_s_name(path):
    assert name_problem("Ammut", literal=True, path=path).endswith("the card is named Soul Burner.")


def test_no_snapshot_reports_nothing(tmp_path):
    _clear_caches()
    try:
        assert name_problem("Zzyzx", literal=False, path=tmp_path / "missing.json") is None
    finally:
        _clear_caches()
