"""The combat strategy tag vocabulary and its filtering rule (#125), Qt-free."""

from __future__ import annotations

import pytest

from bartlebot.shared.strategy_models import DeckData, DeckPreset
from bartlebot.store_roots import SOURCE_LABELS, Origin
from bartlebot.strategy_store import StrategyKind
from bartlebot.strategy_summary import summarize_deck
from bartlebot.strategy_tags import (
    KIND_GROUPS,
    PROFILE_SOURCE,
    ROLE,
    SCHOOL_KINDS,
    SOURCE,
    SOURCE_TAGS,
    STORED_TAGS,
    TEAM_SIZE_KINDS,
    StrategyFacets,
    StrategyFilter,
    count_facets,
    facets_match,
    normalize_strategy_tags,
    strategy_tag_group,
    team_label,
    works_with_school,
)

ANY = frozenset()


def _f(*sets: set[str]) -> tuple[frozenset[str], ...]:
    return tuple(frozenset(s) for s in sets)


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_the_source_values_are_the_pill_labels():
    """A Source chip must say what the pill on the card it matches says — except Profile,
    whose pill carries the profile's own name, which a fixed vocabulary cannot (#118)."""
    assert SOURCE_TAGS == (
        PROFILE_SOURCE,
        SOURCE_LABELS[Origin.SHIPPED],
        SOURCE_LABELS[Origin.SHIPPED_OVERRIDE],
        SOURCE_LABELS[Origin.USER],
    )


def test_source_is_never_stored():
    assert not set(SOURCE_TAGS) & set(STORED_TAGS)


def test_duels_have_no_role_group():
    assert KIND_GROUPS[StrategyKind.DUEL] == (SOURCE,)
    assert ROLE in KIND_GROUPS[StrategyKind.PLAYER]
    assert ROLE in KIND_GROUPS[StrategyKind.DECK]


def test_every_kind_is_asked_about_school_and_only_duels_about_team_size():
    assert SCHOOL_KINDS == {StrategyKind.DUEL, StrategyKind.PLAYER, StrategyKind.DECK}
    assert TEAM_SIZE_KINDS == {StrategyKind.DUEL}


def test_a_decks_schools_are_its_one_slot_and_its_first_facet():
    """#174: a deck names the schools it is built for, and empty works with any."""
    storm = summarize_deck(DeckPreset(name="D", schools=["Storm", "Ice"], deck=DeckData(normal={"Storm Lord": 2})))
    anyone = summarize_deck(DeckPreset(name="D"))

    assert storm.facets == ["Storm/Ice", "2 cards"]
    assert storm.slot_schools == (frozenset({"Storm", "Ice"}),)
    assert anyone.facets == ["empty"]
    assert works_with_school(anyone.slot_schools, ["Fire"])
    assert not works_with_school(storm.slot_schools, ["Fire"])


def test_a_decks_schools_stay_out_of_its_file_while_empty_and_are_deduplicated(tmp_path):
    DeckPreset(name="D").save(tmp_path / "d.json")
    assert "schools" not in (tmp_path / "d.json").read_text(encoding="utf-8")
    assert DeckPreset(name="D", schools=["Ice", "", "Ice", "Storm"]).schools == ["Ice", "Storm"]


def test_every_tag_knows_its_group():
    assert strategy_tag_group("Healer") == ROLE
    assert strategy_tag_group("Custom") == SOURCE
    assert strategy_tag_group("Gear") == ""


def test_normalize_orders_dedupes_and_drops_unknown_and_derived_tags():
    assert normalize_strategy_tags(["Healer", "Hitter", "Healer", "Custom", "Nonsense", 3]) == ["Hitter", "Healer"]


def test_normalize_tolerates_a_non_list():
    assert normalize_strategy_tags("Hitter") == []
    assert normalize_strategy_tags(None) == []


def test_team_labels():
    assert team_label(1) == "Solo"
    assert team_label(3) == "Team of 3"


# ---------------------------------------------------------------------------
# Works with school X
# ---------------------------------------------------------------------------


def test_an_unrestricted_slot_works_with_every_school():
    assert works_with_school((ANY,), {"Storm"})


def test_a_restricted_slot_works_only_with_its_own_schools():
    assert works_with_school(_f({"Storm", "Fire"}), {"Fire"})
    assert not works_with_school(_f({"Storm", "Fire"}), {"Life"})


def test_one_accepting_slot_is_enough():
    assert works_with_school(_f({"Storm"}, {"Life"}), {"Life"})


def test_several_schools_are_ored():
    assert works_with_school(_f({"Storm"}), {"Life", "Storm"})


def test_no_slots_works_with_nothing():
    assert not works_with_school((), {"Storm"})


# ---------------------------------------------------------------------------
# facets_match
# ---------------------------------------------------------------------------


def test_an_empty_filter_passes_everything():
    assert StrategyFilter().is_empty
    assert facets_match(StrategyFacets(), StrategyFilter())


def test_tags_or_within_a_group():
    facets = StrategyFacets(tags=frozenset({"Hitter", "Custom"}))
    assert facets_match(facets, StrategyFilter(tags=frozenset({"Hitter", "Healer"})))


def test_tags_and_across_groups():
    facets = StrategyFacets(tags=frozenset({"Hitter", "Custom"}))
    assert facets_match(facets, StrategyFilter(tags=frozenset({"Hitter", "Custom"})))
    assert not facets_match(facets, StrategyFilter(tags=frozenset({"Hitter", "Official"})))


def test_an_entry_with_no_role_fails_a_role_filter():
    assert not facets_match(StrategyFacets(tags=frozenset({"Custom"})), StrategyFilter(tags=frozenset({"Hitter"})))


@pytest.mark.parametrize(
    ("team", "passes"),
    [(1, False), (2, True), (3, True), (4, False)],
)
def test_team_size_must_fall_in_the_duels_range(team, passes):
    facets = StrategyFacets(slot_schools=_f(set(), set(), set()), player_range=(2, 3))
    assert facets_match(facets, StrategyFilter(team_size=team)) is passes


def test_team_size_ignores_an_entry_with_no_range():
    """A Player Strategy has no range; the filter is not offered there, and must not hide it."""
    assert facets_match(StrategyFacets(slot_schools=(ANY,)), StrategyFilter(team_size=2))


def test_team_size_limits_the_school_check_to_the_slots_the_team_fills():
    # P1 must be Storm; P2 is anyone. A solo Life wizard only ever fills P1.
    facets = StrategyFacets(slot_schools=_f({"Storm"}, set()), player_range=(1, 2))
    assert facets_match(facets, StrategyFilter(schools=frozenset({"Life"})))
    assert not facets_match(facets, StrategyFilter(schools=frozenset({"Life"}), team_size=1))


def test_school_and_tags_combine():
    facets = StrategyFacets(tags=frozenset({"Support"}), slot_schools=_f({"Ice"}))
    assert facets_match(facets, StrategyFilter(tags=frozenset({"Support"}), schools=frozenset({"Ice"})))
    assert not facets_match(facets, StrategyFilter(tags=frozenset({"Support"}), schools=frozenset({"Fire"})))


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_counts_answer_what_each_value_alone_would_show():
    entries = [
        StrategyFacets(tags=frozenset({"Hitter", "Official"}), slot_schools=_f({"Storm"}), player_range=(1, 2)),
        StrategyFacets(tags=frozenset({"Custom"}), slot_schools=(ANY,), player_range=(2, 4)),
        StrategyFacets(tags=frozenset({"Custom"})),  # a deck: no slots, no range
    ]
    counts = count_facets(entries, ("Storm", "Life"))

    assert counts.tags == {"Hitter": 1, "Official": 1, "Custom": 2}
    assert counts.schools == {"Storm": 2, "Life": 1}
    assert counts.team_sizes == {1: 1, 2: 2, 3: 1, 4: 1}
