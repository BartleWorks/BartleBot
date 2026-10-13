"""The shipped default duels (Stage 3 of #201): their content, ids and shape.

The files under ``strategies/`` are Official content, so what a fight with nothing
tailored runs is decided by what these assert. The default duels are only resolved by
Stage 4; until then the files ship and are found by id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.shared import combat_defaults
from bartlebot.shared.combat_defaults import (
    LAST_RESORT_DSL,
    PREFERENCE_KEYS,
    SHIPPED_DEFAULT_PLAYER_ID,
    SHIPPED_DEFAULT_SUPPORT_ID,
    SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
    SHIPPED_SOLO_DUEL_ID,
    SHIPPED_TEAM_DUEL_ID,
    DefaultSource,
    TeamSize,
    chosen_default,
    default_duel,
    players_text,
    set_default_choice,
    team_size,
)
from bartlebot.shared.official_set import SHARED_SET_VERSION
from bartlebot.shared.store_files import index_dir, slugify
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy

STRATEGIES = Path(__file__).resolve().parents[2] / "strategies"
DUELS = STRATEGIES / "duel"
PLAYERS = STRATEGIES / "player"

#: The value of ``SHARED_SET_VERSION`` before this stage changed shared Official items.
VERSION_BEFORE_DEFAULT_DUELS = 1


def _duel(duel_id: str) -> DuelCombatStrategy:
    return DuelCombatStrategy.load(index_dir(DUELS)[duel_id])


def _player(player_id: str) -> PlayerCombatStrategy:
    return PlayerCombatStrategy.load(index_dir(PLAYERS)[player_id])


def _duel_files() -> list[Path]:
    return sorted(DUELS.glob("*.json"))


def _player_files() -> list[Path]:
    return sorted(PLAYERS.glob("*.json"))


@pytest.mark.parametrize("path", _player_files(), ids=lambda p: p.name)
def test_every_shipped_player_strategy_parses(path: Path):
    strategy = PlayerCombatStrategy.load(path)
    assert validate_dsl(strategy.dsl) is None, strategy.name


def test_default_support_ends_in_two_damage_lines():
    priorities = [p.strip() for p in _player(SHIPPED_DEFAULT_SUPPORT_ID).dsl.split(" | ")]
    assert len(priorities) == 7
    assert all(p.startswith("any<damage") for p in priorities[-2:])
    assert not any(p.startswith("?(") for p in priorities[-2:])


def test_default_support_gates_what_does_not_aim_at_the_boss_on_a_boss_fight():
    priorities = [p.strip() for p in _player(SHIPPED_DEFAULT_SUPPORT_ID).dsl.split(" | ")]
    gate = "?(any(enemies).is_boss == 1)"
    gated = [p for p in priorities if p.startswith(gate)]
    assert len(gated) == 3
    # The ungated lines either aim at the boss, which skips when there is none, or are damage.
    ungated = [p for p in priorities if not p.startswith(gate)]
    assert all("@ boss" in p or p.startswith("any<damage") for p in ungated)
    assert not any("boss.hp" in p or "boss.health" in p for p in priorities)


def test_default_dsl_equals_the_last_resort():
    assert _player(SHIPPED_DEFAULT_PLAYER_ID).dsl == LAST_RESORT_DSL
    assert _player(SHIPPED_DEFAULT_PLAYER_ID).name == "Default"


def test_the_last_resort_parses():
    assert validate_dsl(LAST_RESORT_DSL) is None


def test_solo_duel_accepts_one_player_only():
    duel = _duel(SHIPPED_SOLO_DUEL_ID)
    assert (duel.min_players, duel.max_players) == (1, 1)
    assert duel.resolve_dsl_for_player(1, {SHIPPED_DEFAULT_PLAYER_ID: "default"}) == "default"


@pytest.mark.parametrize("duel_id", [SHIPPED_TEAM_DUEL_ID, SHIPPED_EVERYONE_ATTACKS_DUEL_ID])
def test_team_duels_accept_two_to_four_players(duel_id: str):
    duel = _duel(duel_id)
    assert (duel.min_players, duel.max_players) == (2, 4)


def test_team_duel_has_p1_hit_and_the_rest_support_casting_p1_last():
    duel = _duel(SHIPPED_TEAM_DUEL_ID)
    pcs = {SHIPPED_DEFAULT_PLAYER_ID: "hit", SHIPPED_DEFAULT_SUPPORT_ID: "support"}
    assert [duel.resolve_dsl_for_player(n, pcs) for n in (1, 2, 3, 4)] == ["hit", "support", "support", "support"]
    assert duel.combat_order == [4, 3, 2, 1]


def test_everyone_attacks_runs_default_on_all_four():
    duel = _duel(SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    pcs = {SHIPPED_DEFAULT_PLAYER_ID: "hit"}
    assert [duel.resolve_dsl_for_player(n, pcs) for n in (1, 2, 3, 4)] == ["hit"] * 4


@pytest.mark.parametrize(
    ("duel_id", "name"),
    [
        (SHIPPED_SOLO_DUEL_ID, "Default Solo Duel"),
        (SHIPPED_TEAM_DUEL_ID, "Default Team Duel"),
        (SHIPPED_EVERYONE_ATTACKS_DUEL_ID, "Default Team Duel (Everyone Attacks)"),
    ],
)
def test_each_shipped_duel_id_resolves_to_its_file_named_after_it(duel_id: str, name: str):
    path = index_dir(DUELS)[duel_id]
    assert path.name == f"{slugify(name)}.json"
    assert DuelCombatStrategy.load(path).name == name


def test_every_link_in_the_default_duels_resolves():
    known = set(index_dir(PLAYERS))
    for duel_id in (SHIPPED_SOLO_DUEL_ID, SHIPPED_TEAM_DUEL_ID, SHIPPED_EVERYONE_ATTACKS_DUEL_ID):
        assert set(_duel(duel_id).linked_pcs_ids()) <= known


@pytest.mark.parametrize("files", [_duel_files(), _player_files()], ids=["duel", "player"])
def test_every_shipped_file_is_named_after_its_item_and_declares_a_unique_id(files: list[Path]):
    ids = [json.loads(p.read_text(encoding="utf-8"))["id"] for p in files]
    assert len(set(ids)) == len(ids)
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert path.name == f"{slugify(data['name'])}.json"


def test_the_ids_are_distinct():
    ids = [v for k, v in vars(combat_defaults).items() if k.startswith("SHIPPED_") and k.endswith("_ID")]
    assert len(ids) == 5
    assert len(set(ids)) == len(ids)


def test_shared_set_version_rose_with_the_default_duels():
    assert SHARED_SET_VERSION > VERSION_BEFORE_DEFAULT_DUELS


def test_a_missing_default_does_not_resolve():
    """The failure path: an id no file declares is not found, never invented."""
    assert "00000000-0000-0000-0000-000000000000" not in index_dir(DUELS)


# ---------------------------------------------------------------------------
# Levels 3 and 4: default_duel (Stage 4)
# ---------------------------------------------------------------------------


def _library(*duels: DuelCombatStrategy):
    by_id = {d.id: d for d in duels}
    return by_id.get


def _prefs(tmp_path: Path):
    from bartlebot.shared.preferences import PreferenceStore  # noqa: PLC0415

    return PreferenceStore(tmp_path)


@pytest.mark.parametrize(("clients", "size"), [(1, TeamSize.SOLO), (2, TeamSize.TEAM), (4, TeamSize.TEAM)])
def test_team_size_is_solo_for_one_client_only(clients, size):
    assert team_size(clients) is size


@pytest.mark.parametrize(("players", "fits"), [(1, False), (2, True), (3, True), (4, False)])
def test_a_duel_accepts_a_team_within_its_range(players, fits):
    assert DuelCombatStrategy(name="D", min_players=2, max_players=3).accepts(players) is fits


def test_players_text_reads_as_a_range():
    assert players_text(DuelCombatStrategy(name="D", min_players=1, max_players=1)) == "1 player"
    assert players_text(DuelCombatStrategy(name="D", min_players=4, max_players=4)) == "4 players"
    assert players_text(DuelCombatStrategy(name="D", min_players=2, max_players=4)) == "2 to 4 players"


def test_no_key_means_the_shipped_duel(tmp_path):
    team = _duel(SHIPPED_TEAM_DUEL_ID)
    answer = default_duel(2, _prefs(tmp_path), _library(team))
    assert (answer.duel, answer.source, answer.reasons) == (team, DefaultSource.SHIPPED, ())


def test_a_stored_id_that_fits_is_yours(tmp_path):
    mine = DuelCombatStrategy(name="Mine", min_players=2)
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)

    answer = default_duel(3, prefs, _library(mine, _duel(SHIPPED_TEAM_DUEL_ID)))

    assert (answer.duel, answer.source) == (mine, DefaultSource.YOURS)


def test_a_stored_id_that_does_not_fit_falls_to_the_shipped_duel_and_says_why(tmp_path):
    mine = DuelCombatStrategy(name="Four Box", min_players=4)
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)
    team = _duel(SHIPPED_TEAM_DUEL_ID)

    answer = default_duel(2, prefs, _library(mine, team))

    assert (answer.duel, answer.source) == (team, DefaultSource.SHIPPED)
    assert answer.reasons == ("your Team default 'Four Box' takes 4 players, and 2 are connected",)


def test_an_ambient_team_fight_falls_to_everyone_attacks(tmp_path):
    """Nothing orders who joins a fight no node started, so no seat is the hitter's."""
    everyone = _duel(SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    answer = default_duel(3, _prefs(tmp_path), _library(_duel(SHIPPED_TEAM_DUEL_ID), everyone), ambient=True)
    assert (answer.duel, answer.source) == (everyone, DefaultSource.SHIPPED)


def test_an_ambient_solo_fight_keeps_the_solo_duel(tmp_path):
    solo = _duel(SHIPPED_SOLO_DUEL_ID)
    answer = default_duel(1, _prefs(tmp_path), _library(solo), ambient=True)
    assert answer.duel == solo


def test_your_team_default_still_answers_an_ambient_fight(tmp_path):
    """Only the shipped default changes: a duel the user chose is theirs to run."""
    mine = DuelCombatStrategy(name="Mine", min_players=2)
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)

    answer = default_duel(2, prefs, _library(mine, _duel(SHIPPED_EVERYONE_ATTACKS_DUEL_ID)), ambient=True)

    assert (answer.duel, answer.source) == (mine, DefaultSource.YOURS)


def test_nothing_readable_answers_nothing(tmp_path):
    answer = default_duel(1, _prefs(tmp_path), _library())
    assert (answer.duel, answer.source) == (None, None)


def test_default_duel_writes_nothing(tmp_path):
    default_duel(2, _prefs(tmp_path), _library(_duel(SHIPPED_TEAM_DUEL_ID)))
    assert list(tmp_path.iterdir()) == []


def test_a_choice_is_stored_and_read_back(tmp_path):
    prefs = _prefs(tmp_path)

    assert set_default_choice(prefs, TeamSize.TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    assert chosen_default(_prefs(tmp_path), TeamSize.TEAM) == SHIPPED_EVERYONE_ATTACKS_DUEL_ID
    assert chosen_default(prefs, TeamSize.SOLO) is None


@pytest.mark.parametrize("recommended", [None, SHIPPED_TEAM_DUEL_ID])
def test_the_recommended_choice_is_stored_as_no_key(tmp_path, recommended):
    """So a later fix to the shipped duel reaches everyone who kept it (epic #201)."""
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.SOLO], "kept")
    set_default_choice(prefs, TeamSize.TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    assert set_default_choice(prefs, TeamSize.TEAM, recommended)

    record = json.loads((tmp_path / "preferences.json").read_text(encoding="utf-8"))
    assert PREFERENCE_KEYS[TeamSize.TEAM] not in record
    assert record[PREFERENCE_KEYS[TeamSize.SOLO]] == "kept"
