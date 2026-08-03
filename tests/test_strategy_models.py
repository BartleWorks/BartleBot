"""Tests for bartlebot.bot.strategy_models."""

import json
from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)

# ---------------------------------------------------------------------------
# PlayerCombatStrategy
# ---------------------------------------------------------------------------


def test_pcs_auto_uuid():
    pcs = PlayerCombatStrategy(name="Test", dsl="some dsl")
    assert len(pcs.id) == 36  # UUID4 format
    assert pcs.dsl == "some dsl"


def test_pcs_save_load_roundtrip(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Blade", dsl="trap @ self")
    path = tmp_path / f"{pcs.id}.json"
    pcs.save(path)
    assert path.exists()
    loaded = PlayerCombatStrategy.load(path)
    assert loaded.id == pcs.id
    assert loaded.name == pcs.name
    assert loaded.dsl == pcs.dsl


def test_pcs_save_atomic(tmp_path: Path):
    """save() creates parent directories and writes valid JSON."""
    pcs = PlayerCombatStrategy(name="X", dsl="y")
    path = tmp_path / "sub" / "dir" / f"{pcs.id}.json"
    pcs.save(path)
    with open(path) as f:
        data = json.load(f)
    assert data["name"] == "X"


# ---------------------------------------------------------------------------
# DeckData
# ---------------------------------------------------------------------------


def test_deck_data_is_empty():
    assert DeckData().is_empty()
    assert not DeckData(normal={"Fireball": 1}).is_empty()


def test_deck_data_total_cards():
    """Normal and item cards count; tc is legacy and never applied."""
    d = DeckData(normal={"Fireball": 4}, tc={"Feint": 2}, item={"Reshuffle": 1})
    assert d.total_cards() == 5


def test_deck_data_item_only_is_not_empty():
    assert not DeckData(item={"Reshuffle": 1}).is_empty()


def test_deck_data_treasure_only_is_empty():
    """A deck holding only unrestorable treasure cards counts as empty."""
    assert DeckData(tc={"Feint": 3}).is_empty()


# ---------------------------------------------------------------------------
# DuelCombatStrategy
# ---------------------------------------------------------------------------


def test_dcs_resolve_dsl_for_player():
    pcs_id_a = "aaa"
    pcs_id_b = "bbb"
    pcs_map = {"aaa": "dsl_a", "bbb": "dsl_b"}
    dcs = DuelCombatStrategy(
        name="Test",
        player_assignments=[
            PlayerAssignment(players=[1, 2], player_combat_strategy_id=pcs_id_a),
            PlayerAssignment(players=[3, 4], player_combat_strategy_id=pcs_id_b),
        ],
    )
    assert dcs.resolve_dsl_for_player(1, pcs_map) == "dsl_a"
    assert dcs.resolve_dsl_for_player(2, pcs_map) == "dsl_a"
    assert dcs.resolve_dsl_for_player(3, pcs_map) == "dsl_b"
    assert dcs.resolve_dsl_for_player(5, pcs_map) is None  # unassigned


def test_dcs_resolve_inline_dsl():
    """Inline assignments resolve from their own dsl, with no library lookup."""
    dcs = DuelCombatStrategy(
        name="Inline",
        player_assignments=[
            PlayerAssignment(players=[1, 2], dsl="inline_a"),
            PlayerAssignment(players=[3], dsl="inline_b"),
        ],
    )
    assert dcs.resolve_dsl_for_player(1, {}) == "inline_a"
    assert dcs.resolve_dsl_for_player(2, {}) == "inline_a"
    assert dcs.resolve_dsl_for_player(3, {}) == "inline_b"
    assert dcs.resolve_dsl_for_player(4, {}) is None  # unassigned


def test_dcs_resolve_empty_inline_dsl_is_none():
    """An empty inline DSL falls through to the caller's built-in default."""
    dcs = DuelCombatStrategy(name="Empty", player_assignments=[PlayerAssignment(players=[1], dsl="")])
    assert dcs.resolve_dsl_for_player(1, {}) is None


def test_dcs_resolve_link_wins_over_inline_dsl():
    """When a preset is linked, the stale inline dsl is ignored."""
    dcs = DuelCombatStrategy(
        name="Mixed",
        player_assignments=[
            PlayerAssignment(players=[1], dsl="stale", player_combat_strategy_id="pcs-1"),
            PlayerAssignment(players=[2], dsl="inline"),
        ],
    )
    assert dcs.resolve_dsl_for_player(1, {"pcs-1": "from_preset"}) == "from_preset"
    assert dcs.resolve_dsl_for_player(2, {"pcs-1": "from_preset"}) == "inline"


def test_assignment_is_linked():
    assert PlayerAssignment(players=[1], dsl="x").is_linked is False
    assert PlayerAssignment(players=[1], player_combat_strategy_id="pcs-1").is_linked is True


def test_dcs_linked_pcs_ids_skips_inline():
    dcs = DuelCombatStrategy(
        name="Mixed",
        player_assignments=[
            PlayerAssignment(players=[1], dsl="inline"),
            PlayerAssignment(players=[2], player_combat_strategy_id="pcs-1"),
            PlayerAssignment(players=[3], player_combat_strategy_id="pcs-2"),
        ],
    )
    assert dcs.linked_pcs_ids() == ["pcs-1", "pcs-2"]


def test_dcs_loads_legacy_assignment_without_dsl(tmp_path: Path):
    """Pre-migration files have no 'dsl' key and a required PCS id — still parse."""
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "id": "dcs-legacy",
                "name": "Legacy",
                "player_assignments": [{"players": [1], "player_combat_strategy_id": "pcs-1"}],
            }
        ),
        encoding="utf-8",
    )
    loaded = DuelCombatStrategy.load(path)
    assignment = loaded.player_assignments[0]
    assert assignment.is_linked is True
    assert assignment.dsl == ""
    assert loaded.resolve_dsl_for_player(1, {"pcs-1": "legacy_dsl"}) == "legacy_dsl"


def test_dcs_save_load_roundtrip(tmp_path: Path):
    dcs = DuelCombatStrategy(
        name="Boss Fight",
        min_players=2,
        max_players=4,
        combat_order=[4, 3, 2, 1],
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id="pcs-1"),
        ],
    )
    path = tmp_path / f"{dcs.id}.json"
    dcs.save(path)
    loaded = DuelCombatStrategy.load(path)
    assert loaded.id == dcs.id
    assert loaded.combat_order == [4, 3, 2, 1]
    assert len(loaded.player_assignments) == 1
    assert loaded.player_assignments[0].players == [1]


def test_dcs_simultaneous_combat_order_none(tmp_path: Path):
    """combat_order=None (simultaneous) survives a round-trip."""
    dcs = DuelCombatStrategy(name="X", combat_order=None)
    path = tmp_path / f"{dcs.id}.json"
    dcs.save(path)
    loaded = DuelCombatStrategy.load(path)
    assert loaded.combat_order is None


# ---------------------------------------------------------------------------
# BotCombatStrategy
# ---------------------------------------------------------------------------


def test_bcs_find_dcs_id_for_state():
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="bot-123",
        state_assignments=[
            StateAssignment(duel_combat_strategy_id="dcs-1", state_ids=["foulgaze", "jade_oni"]),
            StateAssignment(duel_combat_strategy_id="dcs-2", state_ids=["darkmoor_boss"]),
        ],
    )
    assert bcs.find_dcs_id_for_state("foulgaze") == "dcs-1"
    assert bcs.find_dcs_id_for_state("jade_oni") == "dcs-1"
    assert bcs.find_dcs_id_for_state("darkmoor_boss") == "dcs-2"
    assert bcs.find_dcs_id_for_state("unknown_state") is None


def test_bcs_save_load_roundtrip(tmp_path: Path):
    bcs = BotCombatStrategy(
        name="Storm Team",
        bot_id="bot-456",
        player_decks={1: DeckData(normal={"Tempest": 7}), 2: DeckData()},
        default_strategy_id="dcs-default",
        state_assignments=[
            StateAssignment(duel_combat_strategy_id="dcs-1", state_ids=["boss1"]),
        ],
    )
    path = tmp_path / f"{bcs.id}.json"
    bcs.save(path)
    loaded = BotCombatStrategy.load(path)
    assert loaded.id == bcs.id
    assert loaded.bot_id == "bot-456"
    assert loaded.default_strategy_id == "dcs-default"
    assert loaded.player_decks[1].normal == {"Tempest": 7}
    assert len(loaded.state_assignments) == 1


def test_bcs_auto_uuid_distinct():
    b1 = BotCombatStrategy(name="A", bot_id="x")
    b2 = BotCombatStrategy(name="B", bot_id="x")
    assert b1.id != b2.id
