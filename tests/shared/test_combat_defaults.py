"""The shipped default duels (Stage 3 of #201): their content, ids and shape.

The files under ``strategies/`` are Official content, so what a fight with nothing
tailored runs is decided by what these assert. The default duels are only resolved by
Stage 4; until then the files ship and are found by id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.combat.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.shared import combat_defaults
from bartlebot.shared.combat_defaults import (
    LAST_RESORT_DSL,
    SHIPPED_DEFAULT_PLAYER_ID,
    SHIPPED_DEFAULT_SUPPORT_ID,
    SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
    SHIPPED_SOLO_DUEL_ID,
    SHIPPED_TEAM_DUEL_ID,
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


def test_the_old_name_is_the_last_resort():
    assert BARTLEBOT_DEFAULT_COMBAT_CONFIG == LAST_RESORT_DSL
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
