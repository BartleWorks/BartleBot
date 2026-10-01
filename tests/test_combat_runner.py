"""Tests for bartlebot.nodes.combat_runner.resolve_combat."""

from pathlib import Path
from unittest.mock import Mock, patch

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    FightRef,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
from bartlebot.nodes.combat_runner import _BUILTIN_SOURCE, resolve_combat
from tests.conftest import FakeMachine


def _make_machine(bot_id: str = "bot-1", state: str = "some_state", enemy: str = "") -> FakeMachine:
    """A machine standing on *state*, a fight that asked for a strategy of its own."""
    m = FakeMachine()
    m.bot_config = BotConfig(
        id=bot_id,
        name="Bot",
        initial=state,
        recovery_state=state,
        states=[
            StateModel(id=state, node_type="handle_combat", params={"use_default_strategy": False, "enemy": enemy})
        ],
        transitions=[],
    )
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


def test_full_resolution_inline_dsl_skips_store(tmp_path: Path):
    """Inline assignments resolve without ever touching the PCS store."""
    dcs = DuelCombatStrategy(
        name="Inline Boss",
        combat_order=[1, 2],
        player_assignments=[
            PlayerAssignment(players=[1], dsl="blade @ self"),
            PlayerAssignment(players=[2], dsl="trap @ enemy"),
        ],
    )
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-1", default_strategy_id=dcs.id)
    machine = _make_machine(bot_id="bot-1")

    load_pcs = Mock(side_effect=AssertionError("inline assignments must not load PCS files"))
    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
        patch("bartlebot.strategy_store.load_player_strategy", load_pcs),
    ):
        dsls, order, source = resolve_combat(machine, use_default_strategy=True, num_clients=2)

    assert dsls == ["blade @ self", "trap @ enemy"]
    assert order == [1, 2]
    load_pcs.assert_not_called()


def test_mixed_inline_and_linked_assignments(tmp_path: Path):
    """A duel may mix inline DSL with a linked preset; only the linked one loads."""
    pcs = PlayerCombatStrategy(name="Shared", dsl="shared_dsl")
    dcs = DuelCombatStrategy(
        name="Mixed",
        player_assignments=[
            PlayerAssignment(players=[1], dsl="inline_dsl"),
            PlayerAssignment(players=[2], player_combat_strategy_id=pcs.id),
        ],
    )
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-1", default_strategy_id=dcs.id)
    machine = _make_machine(bot_id="bot-1")

    load_pcs = Mock(return_value=pcs)
    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
        patch("bartlebot.strategy_store.load_player_strategy", load_pcs),
    ):
        dsls, _order, _source = resolve_combat(machine, use_default_strategy=True, num_clients=2)

    assert dsls == ["inline_dsl", "shared_dsl"]
    load_pcs.assert_called_once()
    assert load_pcs.call_args.args == (pcs.id,)


def test_empty_inline_dsl_falls_back_to_builtin(tmp_path: Path):
    """A blank inline DSL uses the built-in default rather than an empty strategy."""
    dcs = DuelCombatStrategy(name="Blank", player_assignments=[PlayerAssignment(players=[1], dsl="")])
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-1", default_strategy_id=dcs.id)
    machine = _make_machine(bot_id="bot-1")

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
    ):
        dsls, _order, _source = resolve_combat(machine, use_default_strategy=True, num_clients=1)

    assert dsls[0] == BARTLEBOT_DEFAULT_COMBAT_CONFIG


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


# ---------------------------------------------------------------------------
# Unset links, and the inline configs removed in #118
# ---------------------------------------------------------------------------


def test_an_unset_default_falls_back_to_builtin(tmp_path: Path):
    """No default_strategy_id means no default at all."""
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-1")
    machine = _make_machine(bot_id="bot-1")

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
    ):
        dsls, order, source = resolve_combat(machine, use_default_strategy=True, num_clients=1)

    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG]
    assert order == []
    assert source == _BUILTIN_SOURCE


def test_an_unlinked_state_assignment_falls_back_to_bcs_default(tmp_path: Path):
    """An assignment exists for the state but links nothing — falls through to the BCS
    default exactly as if the state had no assignment at all.
    """
    dcs_default = DuelCombatStrategy(
        name="Default DCS", player_assignments=[PlayerAssignment(players=[1], dsl="default_dsl")]
    )
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="bot-1",
        default_strategy_id=dcs_default.id,
        state_assignments=[StateAssignment(state_ids=["boss_state"])],  # unlinked
    )
    machine = _make_machine(bot_id="bot-1", state="boss_state")

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs_default),
    ):
        dsls, _order, source = resolve_combat(machine, use_default_strategy=False, num_clients=1)

    assert dsls[0] == "default_dsl"
    assert "default strategy" in source


def test_a_file_from_before_118_loads_with_its_inline_config_ignored(tmp_path: Path):
    """The inline duel configs were removed outright (#118). A stray one still parses —
    the models do not forbid extra fields — and never runs."""
    dcs = DuelCombatStrategy(name="Linked", player_assignments=[PlayerAssignment(players=[1], dsl="linked_dsl")])
    bcs = BotCombatStrategy.model_validate(
        {
            "name": "Farm",
            "bot_id": "bot-1",
            "default_duel_config": {"player_assignments": [{"players": [1], "dsl": "stale_default"}]},
            "state_assignments": [
                {
                    "state_ids": ["boss_state"],
                    "duel_combat_strategy_id": dcs.id,
                    "duel_config": {"player_assignments": [{"players": [1], "dsl": "stale_inline"}]},
                }
            ],
        }
    )
    assert "duel_config" not in bcs.state_assignments[0].model_dump()
    machine = _make_machine(bot_id="bot-1", state="boss_state")

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
    ):
        dsls, _order, source = resolve_combat(machine, use_default_strategy=False, num_clients=1)

    assert dsls[0] == "linked_dsl"
    assert "Linked" in source


def test_a_fight_stored_under_another_state_id_is_found_by_its_enemy(tmp_path: Path):
    """The node was deleted and made again: a new state id, the same enemy."""
    dcs = DuelCombatStrategy(name="Lamia", player_assignments=[PlayerAssignment(players=[1], dsl="lamia_dsl")])
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(fights=[FightRef(state="state_14", enemy="Lamia")], duel_combat_strategy_id=dcs.id)
        ],
    )
    machine = _make_machine(bot_id="bot-1", state="state_20", enemy="Lamia")

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", return_value=dcs),
    ):
        dsls, _order, source = resolve_combat(machine, use_default_strategy=False, num_clients=1)

    assert dsls == ["lamia_dsl"]
    assert "state assignment for 'state_20'" in source


# ---------------------------------------------------------------------------
# Profile strategies (#118): the active profile first, then the library
# ---------------------------------------------------------------------------


def test_a_profile_duel_wins_over_a_library_duel_with_the_same_id(tmp_path: Path):
    library = DuelCombatStrategy(name="Library", player_assignments=[PlayerAssignment(players=[1], dsl="lib")])
    own = library.model_copy(update={"name": "Own", "player_assignments": [PlayerAssignment(players=[1], dsl="own")]})
    bcs = BotCombatStrategy(name="B", bot_id="bot-1", default_strategy_id=library.id, duel_strategies=[own])
    load_dcs = Mock(return_value=library)

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", load_dcs),
    ):
        dsls, _order, source = resolve_combat(_make_machine(), use_default_strategy=True, num_clients=1)

    assert dsls == ["own"]
    assert "'Own'" in source
    load_dcs.assert_not_called()


def test_a_profile_duel_links_a_profile_player_strategy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Frost Heal", dsl="heal")
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = BotCombatStrategy(
        name="B",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=["boss"], duel_combat_strategy_id=own.id)],
        duel_strategies=[own],
        player_strategies=[pcs],
    )
    load_pcs = Mock(side_effect=AssertionError("a profile player strategy must not be loaded from the library"))

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_player_strategy", load_pcs),
    ):
        dsls, _order, _source = resolve_combat(_make_machine(state="boss"), use_default_strategy=False, num_clients=1)

    assert dsls == ["heal"]


def test_an_id_in_neither_the_profile_nor_the_library_falls_back_to_the_builtin(tmp_path: Path):
    bcs = BotCombatStrategy(name="B", bot_id="bot-1", default_strategy_id="nowhere")

    with (
        _patch_active_strategy("bot-1", bcs.id),
        patch("bartlebot.strategy_store.load_bot_strategy", return_value=bcs),
        patch("bartlebot.strategy_store.load_duel_strategy", side_effect=FileNotFoundError),
    ):
        dsls, order, source = resolve_combat(_make_machine(), use_default_strategy=True, num_clients=2)

    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * 2
    assert order == []
    assert source == _BUILTIN_SOURCE
