"""Tests for bartlebot.bot_summary: the description a bot's graph implies.

The names come from the checked-in snapshots (zone_names.json, entity_names.json,
world_names.json), so each case reads as the sentence a user would actually see.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel
from bartlebot.bot_summary import BotFacts, Entrance, describe, describe_data, phrase, run_order

HAUNTED_CAVE = "WizardCity/WC_Streets/WC_HauntedCave"
NIGHTSHADES_TOWER = "WizardCity/WC_Streets/Interiors/WC_HauntedCave_T1"  # has its own name
TRITON = "WizardCity/WC_Streets/WC_Triton"
TRITON_T1 = "WizardCity/WC_Streets/Interiors/WC_Triton_T1"  # shares Triton Avenue's name
SISYPHUS = "Aquila/Interiors/AQ_Z01_TempleOfSisyphus*"  # a glob: no name at all
AERIEL_SHORE = "Empyrea/EM_Z00_Aeriel_HUB"
AERIEL_JUNGLE = "Empyrea/EM_Z01_Jungle"
NIGHTSHADE = "Wraith-WCBoss-L05"


def _dungeon(from_zone=HAUNTED_CAVE, dungeon_zone=NIGHTSHADES_TOWER, **params) -> tuple[str, dict]:
    return ("enter_dungeon", {"from_zone": from_zone, "dungeon_zone": dungeon_zone, **params})


def _fight(enemy="", **params) -> tuple[str, dict]:
    return ("handle_combat", {"enemy": enemy, **params})


def _reagent(zone) -> tuple[str, dict]:
    return ("collect_reagent", {"zone": zone, "entity_names": ["Ore"]})


def _chain(*nodes: tuple[str, dict]) -> BotConfig:
    """A bot running *nodes* in order on success, looping back to the first."""
    states = [StateModel(id=f"s{i}", node_type=t, params=p) for i, (t, p) in enumerate(nodes)]
    ids = [s.id for s in states]
    transitions = [
        TransitionModel(trigger="advance", source=a, dest=b, when="SUCCESS") for a, b in zip(ids, ids[1:] + ids[:1])
    ]
    return BotConfig(id="b", name="B", initial=ids[0], recovery_state=ids[0], states=states, transitions=transitions)


# ---------------------------------------------------------------------------
# One fight: the boss, the dungeon, the street, the world
# ---------------------------------------------------------------------------


def test_one_named_fight_names_the_boss_and_where_it_is():
    bot = _chain(_dungeon(), _fight(NIGHTSHADE))

    assert describe(bot) == "Defeats Lord Nightshade in Nightshade's Tower, Haunted Cave, Wizard City."


def test_the_recorded_dungeon_name_wins_over_the_zones():
    bot = _chain(_dungeon(dungeon_name="Some Other Tower"), _fight(NIGHTSHADE))

    assert describe(bot) == "Defeats Lord Nightshade in Some Other Tower, Haunted Cave, Wizard City."


def test_an_interior_named_after_its_street_is_not_called_a_dungeon():
    """Galvanost Tower's zone reads "Triton Avenue": saying it twice would say nothing."""
    bot = _chain(_dungeon(TRITON, TRITON_T1), _fight(NIGHTSHADE))

    assert describe(bot) == "Defeats Lord Nightshade in Triton Avenue, Wizard City."


def test_the_recorded_name_names_an_interior_its_zone_cannot():
    bot = _chain(_dungeon(TRITON, TRITON_T1, dungeon_name="Galvanost Tower"), _fight(NIGHTSHADE))

    assert describe(bot) == "Defeats Lord Nightshade in Galvanost Tower, Triton Avenue, Wizard City."


def test_a_fight_with_no_entrance_names_only_the_boss():
    assert describe(_chain(_fight(NIGHTSHADE))) == "Defeats Lord Nightshade."


def test_the_wiki_page_names_an_enemy_the_game_does_not():
    bot = _chain(_dungeon(), _fight("*Unknown_Mob*", wiki_page="Creature:Sisyphus Stone Thrower (Challenge)"))

    assert describe(bot) == "Defeats Sisyphus Stone Thrower in Nightshade's Tower, Haunted Cave, Wizard City."


def test_one_unnamed_fight_describes_the_dungeon_instead():
    """A raw template id is worse than no boss at all."""
    bot = _chain(_dungeon(), _fight(""))

    assert describe(bot) == "Completes Nightshade's Tower, Wizard City."


# ---------------------------------------------------------------------------
# Several fights, or none: the dungeon
# ---------------------------------------------------------------------------


def test_several_fights_describe_the_dungeon_not_its_bosses():
    bot = _chain(_dungeon(), _fight(NIGHTSHADE), _fight(""), _fight(NIGHTSHADE))

    assert describe(bot) == "Completes Nightshade's Tower, Wizard City."


def test_the_dungeon_named_is_the_one_the_last_fight_is_behind():
    bot = _chain(
        _dungeon(TRITON, TRITON_T1, dungeon_name="Galvanost Tower"),
        _fight(NIGHTSHADE),
        _dungeon(),
        _fight(NIGHTSHADE),
    )

    assert describe(bot) == "Completes Nightshade's Tower, Wizard City."


def test_a_dungeon_with_no_known_name_is_placed_by_its_street():
    bot = _chain(_dungeon(TRITON, TRITON_T1), _fight(NIGHTSHADE), _fight(NIGHTSHADE))

    assert describe(bot) == "Completes a dungeon in Triton Avenue, Wizard City."


def test_a_glob_dungeon_zone_is_never_printed():
    bot = _chain(_dungeon(TRITON, SISYPHUS), _fight(""), _fight(""))

    assert describe(bot) == "Completes a dungeon in Triton Avenue, Wizard City."


def test_a_dungeon_with_no_fights_is_completed():
    assert describe(_chain(_dungeon())) == "Completes Nightshade's Tower, Wizard City."


def test_a_name_that_repeats_the_world_is_said_once():
    facts = BotFacts(fights=(None, None), dungeon=Entrance("Castle Darkmoor", "Castle Darkmoor", "Castle Darkmoor"))

    assert phrase(facts) == "Completes Castle Darkmoor."


# ---------------------------------------------------------------------------
# Gauntlets
# ---------------------------------------------------------------------------


def test_a_gauntlet_is_named_alone():
    bot = _chain(("enter_gauntlet", {"dungeon_zone": TRITON_T1, "gauntlet_name": "Spiral Cup"}), _fight(NIGHTSHADE))

    assert describe(bot) == "Completes Spiral Cup."


def test_a_gauntlet_falls_back_to_its_zones_name():
    bot = _chain(("enter_gauntlet", {"dungeon_zone": NIGHTSHADES_TOWER}))

    assert describe(bot) == "Completes Nightshade's Tower."


def test_a_gauntlet_with_no_name_says_nothing():
    """Not "Defeats …": the one fight in a gauntlet is not what the bot is for."""
    bot = _chain(("enter_gauntlet", {"dungeon_zone": SISYPHUS}), _fight(NIGHTSHADE))

    assert describe(bot) is None


# ---------------------------------------------------------------------------
# Reagents
# ---------------------------------------------------------------------------


def test_reagents_in_one_zone():
    assert describe(_chain(_reagent(AERIEL_SHORE), _reagent(AERIEL_SHORE))) == (
        "Collects reagents in Aeriel Shore in Empyrea."
    )


def test_reagents_in_two_zones():
    assert describe(_chain(_reagent(AERIEL_SHORE), _reagent(AERIEL_JUNGLE))) == (
        "Collects reagents in Aeriel Shore and Aeriel Jungle in Empyrea."
    )


def test_reagents_in_three_zones():
    bot = _chain(_reagent(HAUNTED_CAVE), _reagent(TRITON), _reagent("WizardCity/WC_Streets/WC_OldeTown"))

    assert describe(bot) == "Collects reagents in Haunted Cave, Triton Avenue, and Olde Town in Wizard City."


def test_reagents_in_more_than_three_zones_name_only_the_world():
    zones = [HAUNTED_CAVE, TRITON, "WizardCity/WC_Streets/WC_OldeTown", "WizardCity/WC_Streets/WC_Firecat"]

    assert describe(_chain(*map(_reagent, zones))) == "Collects reagents in Wizard City."


def test_reagent_zones_across_worlds_leave_the_world_out():
    assert describe(_chain(_reagent(AERIEL_SHORE), _reagent(TRITON))) == (
        "Collects reagents in Aeriel Shore and Triton Avenue."
    )


def test_a_fight_outranks_the_reagents():
    bot = _chain(_dungeon(), _reagent(NIGHTSHADES_TOWER), _fight(NIGHTSHADE))

    assert describe(bot).startswith("Defeats Lord Nightshade")


# ---------------------------------------------------------------------------
# Nothing to say
# ---------------------------------------------------------------------------


def test_a_graph_that_implies_nothing_says_nothing():
    assert describe(_chain(("mark_location", {}))) is None


def test_an_unfilled_template_says_nothing():
    """Every template's entrance and fight are blank until the author fills them in."""
    for path in sorted(Path("bots/templates").glob("*.json")):
        assert describe(BotConfig.load(path)) is None, path.name


# ---------------------------------------------------------------------------
# Run order
# ---------------------------------------------------------------------------


def test_success_edges_are_walked_before_the_others():
    edges = [("a", "recover", "FAILURE"), ("a", "b", "SUCCESS"), ("b", "a", "SUCCESS"), ("recover", "a", None)]

    assert run_order("a", ["recover", "b", "a"], edges) == ["a", "b", "recover"]


def test_states_the_walk_cannot_reach_follow_in_file_order():
    assert run_order("a", ["x", "a", "y"], []) == ["a", "x", "y"]


def test_a_missing_initial_starts_from_the_first_state():
    assert run_order("gone", ["x", "y"], [("x", "y", "SUCCESS")]) == ["x", "y"]


# ---------------------------------------------------------------------------
# Raw files, as the library reads them
# ---------------------------------------------------------------------------


def test_a_raw_file_reads_the_same_as_a_loaded_one():
    bot = _chain(_dungeon(), _fight(NIGHTSHADE))

    assert describe_data(json.loads(bot.model_dump_json())) == describe(bot)


@pytest.mark.parametrize("data", [{}, {"states": [{"id": "a"}]}, {"states": "nonsense"}])
def test_a_malformed_file_says_nothing_rather_than_raising(data):
    assert describe_data(data) is None
