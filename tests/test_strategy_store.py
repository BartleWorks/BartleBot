"""Tests for bartlebot.strategy_store."""

from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    PlayerCombatStrategy,
)
from bartlebot.strategy_store import (
    delete_bot_strategy,
    delete_duel_strategy,
    delete_player_strategy,
    list_bot_strategies,
    list_duel_strategies,
    list_player_strategies,
    load_bot_strategy,
    load_duel_strategy,
    load_player_strategy,
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)

# ---------------------------------------------------------------------------
# PlayerCombatStrategy store
# ---------------------------------------------------------------------------


def test_list_player_strategies_empty(tmp_path: Path):
    assert list_player_strategies(tmp_path) == []


def test_save_load_player_strategy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Blade", dsl="any<blade> @ self")
    save_player_strategy(pcs, tmp_path)
    loaded = load_player_strategy(pcs.id, tmp_path)
    assert loaded.id == pcs.id
    assert loaded.name == "Blade"


def test_list_player_strategies(tmp_path: Path):
    pcs1 = PlayerCombatStrategy(name="A", dsl="")
    pcs2 = PlayerCombatStrategy(name="B", dsl="")
    save_player_strategy(pcs1, tmp_path)
    save_player_strategy(pcs2, tmp_path)
    result = list_player_strategies(tmp_path)
    ids = {s.id for s in result}
    assert pcs1.id in ids
    assert pcs2.id in ids


def test_delete_player_strategy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Del", dsl="")
    save_player_strategy(pcs, tmp_path)
    assert len(list_player_strategies(tmp_path)) == 1
    delete_player_strategy(pcs.id, tmp_path)
    assert list_player_strategies(tmp_path) == []


def test_delete_nonexistent_player_strategy_is_noop(tmp_path: Path):
    delete_player_strategy("no-such-uuid", tmp_path)  # should not raise


# ---------------------------------------------------------------------------
# DuelCombatStrategy store
# ---------------------------------------------------------------------------


def test_save_load_duel_strategy(tmp_path: Path):
    dcs = DuelCombatStrategy(name="Boss Fight", combat_order=[2, 1])
    save_duel_strategy(dcs, tmp_path)
    loaded = load_duel_strategy(dcs.id, tmp_path)
    assert loaded.id == dcs.id
    assert loaded.combat_order == [2, 1]


def test_list_duel_strategies_empty(tmp_path: Path):
    assert list_duel_strategies(tmp_path) == []


def test_delete_duel_strategy(tmp_path: Path):
    dcs = DuelCombatStrategy(name="D")
    save_duel_strategy(dcs, tmp_path)
    delete_duel_strategy(dcs.id, tmp_path)
    assert list_duel_strategies(tmp_path) == []


# ---------------------------------------------------------------------------
# BotCombatStrategy store
# ---------------------------------------------------------------------------


def test_save_load_bot_strategy(tmp_path: Path):
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-1")
    save_bot_strategy(bcs, tmp_path)
    loaded = load_bot_strategy(bcs.id, tmp_path)
    assert loaded.id == bcs.id
    assert loaded.bot_id == "bot-1"


def test_list_bot_strategies_filters_by_bot_id(tmp_path: Path):
    bcs_a = BotCombatStrategy(name="A", bot_id="bot-1")
    bcs_b = BotCombatStrategy(name="B", bot_id="bot-2")
    save_bot_strategy(bcs_a, tmp_path)
    save_bot_strategy(bcs_b, tmp_path)
    result = list_bot_strategies("bot-1", tmp_path)
    assert len(result) == 1
    assert result[0].id == bcs_a.id


def test_delete_bot_strategy(tmp_path: Path):
    bcs = BotCombatStrategy(name="X", bot_id="b")
    save_bot_strategy(bcs, tmp_path)
    delete_bot_strategy(bcs.id, tmp_path)
    assert list_bot_strategies("b", tmp_path) == []


def test_list_bot_strategies_empty_dir(tmp_path: Path):
    assert list_bot_strategies("any-bot", tmp_path) == []


def test_list_skips_corrupt_file(tmp_path: Path):
    """Corrupt JSON files are silently skipped."""
    d = tmp_path / "bot_strategies"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{not valid json}")
    assert list_bot_strategies("any", tmp_path) == []
