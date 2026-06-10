"""Tests for bartlebot.nodes.combat_runner.resolve_combat."""

from pathlib import Path
from unittest.mock import patch

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
from bartlebot.nodes.combat_runner import resolve_combat
from tests.conftest import FakeMachine


def _make_machine(bot_id: str = "bot-1", state: str = "some_state") -> FakeMachine:
    m = FakeMachine()
    from unittest.mock import MagicMock  # noqa: PLC0415

    cfg = MagicMock()
    cfg.id = bot_id
    m.bot_config = cfg
    m.state = state
    return m


def _patch_active_strategy(bot_id: str, bcs_id: str | None):
    sm_instance = type("SM", (), {"get_active_strategy": lambda self, _bid: bcs_id})()
    return patch("bartlebot.settings_manager.SettingsManager.instance", return_value=sm_instance)


# ---------------------------------------------------------------------------
# Fallback cases
# ---------------------------------------------------------------------------


def test_no_bot_config_returns_builtin_default():
    machine = FakeMachine()
    machine.bot_config = None
    dsls, order, source = resolve_combat(machine, True, 2)
    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG, BARTLEBOT_DEFAULT_COMBAT_CONFIG]
    assert order == []


def test_no_active_strategy_returns_builtin_default():
    machine = _make_machine()
    with _patch_active_strategy("bot-1", None):
        dsls, order, source = resolve_combat(machine, True, 2)
    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG, BARTLEBOT_DEFAULT_COMBAT_CONFIG]
    assert order == []
    assert "built-in" in source


def test_missing_bcs_file_returns_builtin_default(tmp_path: Path):
    machine = _make_machine()
    with (
        _patch_active_strategy("bot-1", "nonexistent-uuid"),
        patch("bartlebot.strategy_store.load_bot_strategy", side_effect=FileNotFoundError),
    ):
        dsls, order, source = resolve_combat(machine, True, 1)
    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG]
    assert order == []


def test_no_dcs_id_returns_builtin_default(tmp_path: Path):
    """BCS exists but has no default_strategy_id and no state assignment."""
    bcs = BotCombatStrategy(name="X", bot_id="bot-1")
    machine = _make_machine(bot_id="bot-1", state="unknown_state")
    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
    ):
        dsls, order, source = resolve_combat(machine, use_default_strategy=True, num_clients=1)
    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG]
    assert order == []


# ---------------------------------------------------------------------------
# Full resolution path
# ---------------------------------------------------------------------------


def test_full_resolution(tmp_path: Path):
    """Full resolution: BCS → DCS → PCS DSL for each player."""
    pcs1 = PlayerCombatStrategy(name="P1 DSL", dsl="blade @ self")
    pcs2 = PlayerCombatStrategy(name="P2 DSL", dsl="trap @ enemy")
    dcs = DuelCombatStrategy(
        name="Boss",
        combat_order=[2, 1],
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id=pcs1.id),
            PlayerAssignment(players=[2], player_combat_strategy_id=pcs2.id),
        ],
    )
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="bot-1",
        default_strategy_id=dcs.id,
    )

    machine = _make_machine(bot_id="bot-1")

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
        patch("bartlebot.strategy_store.load_player_strategy", side_effect=[pcs1, pcs2]),
    ):
        dsls, order, source = resolve_combat(machine, use_default_strategy=True, num_clients=2)

    assert dsls[0] == "blade @ self"
    assert dsls[1] == "trap @ enemy"
    assert order == [2, 1]
    assert "Boss" in source


def test_state_assignment_used_when_use_default_false(tmp_path: Path):
    """use_default_strategy=False picks DCS from state_assignments, not default_strategy_id."""
    pcs_default = PlayerCombatStrategy(name="Default", dsl="default_dsl")
    pcs_state = PlayerCombatStrategy(name="State", dsl="state_dsl")
    dcs_default = DuelCombatStrategy(
        name="Default DCS",
        player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs_default.id)],
    )
    dcs_state = DuelCombatStrategy(
        name="State DCS",
        player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs_state.id)],
    )
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="bot-1",
        default_strategy_id=dcs_default.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs_state.id, state_ids=["boss_state"])],
    )

    machine = _make_machine(bot_id="bot-1", state="boss_state")

    def _load_dcs(dcs_id, **kwargs):
        return dcs_default if dcs_id == dcs_default.id else dcs_state

    def _load_pcs(pcs_id, **kwargs):
        return pcs_default if pcs_id == pcs_default.id else pcs_state

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", side_effect=_load_dcs),
        patch("bartlebot.strategy_store.load_player_strategy", side_effect=_load_pcs),
    ):
        dsls, order, source = resolve_combat(machine, use_default_strategy=False, num_clients=1)

    assert dsls[0] == "state_dsl"


def test_unassigned_player_falls_back_to_builtin():
    """Players not in any PlayerAssignment get the built-in default DSL."""
    pcs = PlayerCombatStrategy(name="P1", dsl="p1_dsl")
    dcs = DuelCombatStrategy(
        name="D",
        player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)],
    )
    bcs = BotCombatStrategy(name="B", bot_id="bot-1", default_strategy_id=dcs.id)
    machine = _make_machine()

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
        patch("bartlebot.strategy_store.load_player_strategy", return_value=pcs),
    ):
        dsls, order, source = resolve_combat(machine, use_default_strategy=True, num_clients=2)

    assert dsls[0] == "p1_dsl"  # P1 assigned
    assert dsls[1] == BARTLEBOT_DEFAULT_COMBAT_CONFIG  # P2 unassigned → fallback


def test_missing_pcs_file_uses_builtin_for_that_player():
    """If a PCS file is missing, affected player falls back to built-in — others unaffected."""
    pcs = PlayerCombatStrategy(name="P1", dsl="p1_dsl")
    dcs = DuelCombatStrategy(
        name="D",
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id),
            PlayerAssignment(players=[2], player_combat_strategy_id="missing-pcs-uuid"),
        ],
    )
    bcs = BotCombatStrategy(name="B", bot_id="bot-1", default_strategy_id=dcs.id)
    machine = _make_machine()

    def _load_pcs(pcs_id, **kwargs):
        if pcs_id == pcs.id:
            return pcs
        raise FileNotFoundError

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
        patch("bartlebot.strategy_store.load_player_strategy", side_effect=_load_pcs),
    ):
        dsls, order, source = resolve_combat(machine, use_default_strategy=True, num_clients=2)

    assert dsls[0] == "p1_dsl"
    assert dsls[1] == BARTLEBOT_DEFAULT_COMBAT_CONFIG
