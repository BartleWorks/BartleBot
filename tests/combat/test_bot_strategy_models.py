"""Tests for bartlebot.combat.bot_strategy_models: the Bot Combat Strategy and its fight assignments."""

from pathlib import Path

import pytest

from bartlebot.combat.bot_strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.shared.strategy_models import DeckData

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


# ---------------------------------------------------------------------------
# StateAssignment
# ---------------------------------------------------------------------------


def test_state_ids_is_read_only():
    """Writers build fights; an assignment to ``state_ids`` must not pass silently."""
    assignment = StateAssignment(state_ids=["boss1"])

    with pytest.raises(AttributeError):
        assignment.state_ids = ["boss2"]


def test_adding_and_removing_a_state_edits_the_fights():
    assignment = StateAssignment(fights=[FightRef(state="boss1", enemy="Lamia")])

    assignment.add_state("boss2")
    assignment.add_state("boss1")  # already covered
    assert assignment.fights == [FightRef(state="boss1", enemy="Lamia"), FightRef(state="boss2")]

    assignment.remove_state("boss1")
    assert assignment.state_ids == ["boss2"]
