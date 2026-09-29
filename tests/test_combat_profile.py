"""Tests for bartlebot.combat_profile: the lookup order, and what a profile owns (#118).

Pure functions over the models and a tmp_path store — no QApplication.
"""

from __future__ import annotations

import json
from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.combat_profile import (
    StrategyLookup,
    duplicate_profile,
    library_links_to_profile,
    profile_players_of,
    read_bot_names,
)
from bartlebot.strategy_store import load_bot_strategy, save_bot_strategy, save_duel_strategy, save_player_strategy


def _duel(name: str, *pcs: PlayerCombatStrategy) -> DuelCombatStrategy:
    return DuelCombatStrategy(
        name=name,
        player_assignments=[
            PlayerAssignment(players=[i + 1], player_combat_strategy_id=p.id) for i, p in enumerate(pcs)
        ],
    )


# ---------------------------------------------------------------------------
# StrategyLookup
# ---------------------------------------------------------------------------


def test_a_profile_entry_wins_over_a_library_entry_with_the_same_id():
    library = DuelCombatStrategy(name="Library")
    own = library.model_copy(update={"name": "Own"})
    lookup = StrategyLookup(BotCombatStrategy(name="P", bot_id="b", duel_strategies=[own]), [library], [])

    assert lookup.duel(library.id).name == "Own"


def test_the_library_answers_what_the_profile_does_not_have():
    library = PlayerCombatStrategy(name="Library")
    lookup = StrategyLookup(BotCombatStrategy(name="P", bot_id="b"), [], [library])

    assert lookup.player(library.id) is library


def test_an_unknown_id_is_none():
    lookup = StrategyLookup(BotCombatStrategy(name="P", bot_id="b"), [], [])

    assert lookup.duel("nowhere") is None
    assert lookup.player("nowhere") is None
    assert lookup.duel(None) is None


def test_with_no_profile_only_the_library_is_searched():
    library = DuelCombatStrategy(name="Library")

    assert StrategyLookup(None, [library], []).duel(library.id) is library


def test_the_library_is_read_from_the_store_when_no_list_is_given(tmp_path: Path):
    duel = DuelCombatStrategy(name="Stored")
    pcs = PlayerCombatStrategy(name="Stored P")
    save_duel_strategy(duel, tmp_path)
    save_player_strategy(pcs, tmp_path)
    lookup = StrategyLookup(BotCombatStrategy(name="P", bot_id="b"), root=tmp_path)

    assert lookup.duel(duel.id).name == "Stored"
    assert lookup.player(pcs.id).name == "Stored P"
    assert lookup.duel("nowhere") is None


def test_the_merged_lists_put_the_profile_first_and_drop_what_it_shadows():
    shadowed = DuelCombatStrategy(name="Shadowed")
    other = DuelCombatStrategy(name="Other")
    own = shadowed.model_copy(update={"name": "Own"})
    lookup = StrategyLookup(BotCombatStrategy(name="P", bot_id="b", duel_strategies=[own]), [shadowed, other], [])

    assert [d.name for d in lookup.duels] == ["Own", "Other"]


def test_profile_only_lookups_ignore_the_library():
    library = DuelCombatStrategy(name="Library")
    lookup = StrategyLookup(BotCombatStrategy(name="P", bot_id="b"), [library], [])

    assert lookup.profile_duel(library.id) is None


# ---------------------------------------------------------------------------
# What a duel takes with it, and the one-way rule
# ---------------------------------------------------------------------------


def test_profile_players_of_lists_only_the_profiles_own_each_once():
    own = PlayerCombatStrategy(name="Own")
    library = PlayerCombatStrategy(name="Library")
    duel = _duel("D", own, library, own)
    profile = BotCombatStrategy(name="P", bot_id="b", player_strategies=[own])

    assert profile_players_of(duel, profile) == [own]


def test_a_library_duel_linking_profile_content_is_a_violation():
    own = PlayerCombatStrategy(name="Own")
    library_duel = _duel("Shared", own)
    profile = BotCombatStrategy(name="P", bot_id="b", player_strategies=[own])

    assert library_links_to_profile(profile, [library_duel]) == [(library_duel, own)]


def test_a_profile_duel_linking_profile_content_is_not():
    own = PlayerCombatStrategy(name="Own")
    own_duel = _duel("Own duel", own)
    profile = BotCombatStrategy(name="P", bot_id="b", player_strategies=[own], duel_strategies=[own_duel])

    assert library_links_to_profile(profile, [own_duel]) == []


# ---------------------------------------------------------------------------
# duplicate_profile
# ---------------------------------------------------------------------------


def _profile_with_everything() -> BotCombatStrategy:
    own_pcs = PlayerCombatStrategy(name="Own P")
    library_pcs_id = "library-pcs"
    own_duel = DuelCombatStrategy(
        name="Own D",
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id=own_pcs.id),
            PlayerAssignment(players=[2], player_combat_strategy_id=library_pcs_id),
        ],
    )
    return BotCombatStrategy(
        name="Solo",
        bot_id="b",
        default_strategy_id=own_duel.id,
        state_assignments=[
            StateAssignment(state_ids=["s1"], duel_combat_strategy_id=own_duel.id),
            StateAssignment(state_ids=["s2"], duel_combat_strategy_id="library-duel"),
        ],
        duel_strategies=[own_duel],
        player_strategies=[own_pcs],
    )


def test_duplicating_a_profile_gives_every_owned_strategy_a_new_id():
    source = _profile_with_everything()

    copy = duplicate_profile(source, "Solo copy")

    assert copy.id != source.id
    assert copy.name == "Solo copy"
    assert copy.duel_strategies[0].id != source.duel_strategies[0].id
    assert copy.player_strategies[0].id != source.player_strategies[0].id


def test_duplicating_a_profile_rewrites_every_link_inside_the_copy():
    source = _profile_with_everything()

    copy = duplicate_profile(source, "Solo copy")

    new_duel, new_pcs = copy.duel_strategies[0], copy.player_strategies[0]
    assert copy.default_strategy_id == new_duel.id
    assert copy.state_assignments[0].duel_combat_strategy_id == new_duel.id
    assert new_duel.player_assignments[0].player_combat_strategy_id == new_pcs.id


def test_duplicating_a_profile_leaves_library_links_alone():
    copy = duplicate_profile(_profile_with_everything(), "Solo copy")

    assert copy.state_assignments[1].duel_combat_strategy_id == "library-duel"
    assert copy.duel_strategies[0].player_assignments[1].player_combat_strategy_id == "library-pcs"


def test_duplicating_a_profile_does_not_touch_the_source():
    source = _profile_with_everything()
    before = source.model_dump()

    duplicate_profile(source, "Solo copy")

    assert source.model_dump() == before


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_a_profiles_own_strategies_survive_a_save_and_load(tmp_path: Path):
    source = _profile_with_everything()
    save_bot_strategy(source, tmp_path)

    loaded = load_bot_strategy(source.id, tmp_path)

    assert loaded.duel_strategies == source.duel_strategies
    assert loaded.player_strategies == source.player_strategies


def test_read_bot_names_maps_ids_to_names(tmp_path: Path):
    (tmp_path / "world").mkdir()
    (tmp_path / "world" / "a.json").write_text(json.dumps({"id": "bot-a", "name": "Atlantea"}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"id": "bot-b"}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")

    assert read_bot_names(tmp_path) == {"bot-a": "Atlantea", "bot-b": "b"}
