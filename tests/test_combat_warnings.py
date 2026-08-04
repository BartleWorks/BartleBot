"""Tests for bartlebot.combat_warnings.collect_combat_warnings.

Strategies are written to a real tmp_path root rather than mocked, so the
missing-file branches exercise the actual store behavior.  No QApplication is
needed — that is the point of having this outside AppController.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.combat_warnings import _format_players, collect_combat_warnings
from bartlebot.strategy_store import (
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)

_VALID_DSL = "any<blade>[sharp]@ally(2)"
_OTHER_VALID_DSL = "any<aura & out_damage> | any<aoe>[epic]"
_BROKEN_DSL = "@@@ not a dsl"


def _bcs(root: Path, **kwargs) -> BotCombatStrategy:
    bcs = BotCombatStrategy(name="Test BCS", bot_id="bot-1", **kwargs)
    save_bot_strategy(bcs, root)
    return bcs


def _dcs(root: Path, name: str = "Test DCS", **kwargs) -> DuelCombatStrategy:
    dcs = DuelCombatStrategy(name=name, **kwargs)
    save_duel_strategy(dcs, root)
    return dcs


def _pcs(root: Path, name: str, dsl: str) -> PlayerCombatStrategy:
    pcs = PlayerCombatStrategy(name=name, dsl=dsl)
    save_player_strategy(pcs, root)
    return pcs


def _joined(warnings: list[str]) -> str:
    return "\n".join(warnings)


# ---------------------------------------------------------------------------
# Top-level guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("active_id", [None, ""])
def test_no_active_strategy_warns(tmp_path: Path, active_id):
    warnings = collect_combat_warnings(active_id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1
    assert "No Bot Combat Strategy is set" in warnings[0]


def test_missing_bcs_file_warns_and_stops(tmp_path: Path):
    warnings = collect_combat_warnings("deadbeef-0000-0000-0000-000000000000", 1, root=tmp_path)

    assert len(warnings) == 1
    assert "Bot Combat Strategy file is missing" in warnings[0]
    assert "deadbeef" in warnings[0]


def test_clean_config_produces_no_warnings(tmp_path: Path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path) == []


def test_bcs_with_no_strategies_at_all_is_clean(tmp_path: Path):
    """Running entirely on built-in defaults is a valid configuration."""
    bcs = _bcs(tmp_path)

    assert collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path) == []


# ---------------------------------------------------------------------------
# Missing referenced files
# ---------------------------------------------------------------------------


def test_missing_default_dcs_file(tmp_path: Path):
    bcs = _bcs(tmp_path, default_strategy_id="12345678-0000-0000-0000-000000000000")

    warnings = collect_combat_warnings(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "Default Duel Strategy file is missing" in warnings[0]
    assert "12345678" in warnings[0]


def test_missing_assigned_dcs_file(tmp_path: Path):
    bcs = _bcs(
        tmp_path,
        state_assignments=[
            StateAssignment(duel_combat_strategy_id="abcdef00-0000-0000-0000-000000000000", state_ids=["s1"])
        ],
    )

    warnings = collect_combat_warnings(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "Assigned Duel Strategy file is missing" in warnings[0]


def test_missing_linked_pcs_file(tmp_path: Path):
    dcs = _dcs(
        tmp_path,
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id="99999999-0000-0000-0000-000000000000")
        ],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "Player strategy file missing" in warnings[0]
    assert "99999999" in warnings[0]


def test_missing_pcs_does_not_also_report_a_parse_error(tmp_path: Path):
    """A missing preset must produce one warning, not a second bogus DSL failure."""
    dcs = _dcs(
        tmp_path,
        player_assignments=[
            PlayerAssignment(players=[1], player_combat_strategy_id="99999999-0000-0000-0000-000000000000")
        ],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" not in _joined(warnings)


# ---------------------------------------------------------------------------
# min_players
# ---------------------------------------------------------------------------


def test_min_players_shortfall_warns(tmp_path: Path):
    dcs = _dcs(tmp_path, name="Four Player", min_players=4)
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, num_clients=2, root=tmp_path)

    assert len(warnings) == 1
    assert "requires ≥4 players" in warnings[0]
    assert "only 2 client(s)" in warnings[0]
    assert "Four Player" in warnings[0]


def test_min_players_satisfied_is_silent(tmp_path: Path):
    dcs = _dcs(tmp_path, min_players=2)
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert collect_combat_warnings(bcs.id, num_clients=4, root=tmp_path) == []


def test_min_players_exactly_met_is_silent(tmp_path: Path):
    dcs = _dcs(tmp_path, min_players=2)
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert collect_combat_warnings(bcs.id, num_clients=2, root=tmp_path) == []


# ---------------------------------------------------------------------------
# DSL validation
# ---------------------------------------------------------------------------


def test_broken_inline_dsl_is_reported(tmp_path: Path):
    dcs = _dcs(tmp_path, name="Broken", player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" in warnings[0]
    assert "Broken" in warnings[0]
    assert "P1" in warnings[0]


def test_broken_linked_preset_dsl_is_reported(tmp_path: Path):
    pcs = _pcs(tmp_path, "Bad Preset", _BROKEN_DSL)
    dcs = _dcs(
        tmp_path,
        player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" in warnings[0]


@pytest.mark.parametrize("dsl", ["", "   ", "\n\t "])
def test_empty_dsl_is_never_flagged(tmp_path: Path, dsl):
    """Empty resolves to the built-in default and never runs; a new preset starts
    empty by design, so flagging it would warn on every newly created strategy."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=dsl)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert collect_combat_warnings(bcs.id, 1, root=tmp_path) == []


def test_broken_dsl_reported_once_across_many_users(tmp_path: Path):
    """One shared preset linked from several strategies is parsed and reported once."""
    pcs = _pcs(tmp_path, "Shared Bad", _BROKEN_DSL)
    dcs_a = _dcs(
        tmp_path,
        name="A",
        player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)],
    )
    dcs_b = _dcs(
        tmp_path,
        name="B",
        player_assignments=[PlayerAssignment(players=[2], player_combat_strategy_id=pcs.id)],
    )
    bcs = _bcs(
        tmp_path,
        default_strategy_id=dcs_a.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs_b.id, state_ids=["s1"])],
    )

    warnings = collect_combat_warnings(bcs.id, 2, root=tmp_path)

    parse_failures = [w for w in warnings if "does not parse" in w]
    assert len(parse_failures) == 1
    # ...but both users are named, so the fix is findable from the message.
    assert "'A'" in parse_failures[0]
    assert "'B'" in parse_failures[0]


def test_two_distinct_broken_dsls_are_reported_separately(tmp_path: Path):
    dcs = _dcs(
        tmp_path,
        player_assignments=[
            PlayerAssignment(players=[1], dsl="@@@ one"),
            PlayerAssignment(players=[2], dsl="%%% two"),
        ],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, 2, root=tmp_path)

    assert len([w for w in warnings if "does not parse" in w]) == 2


def test_valid_dsls_produce_no_warnings(tmp_path: Path):
    dcs = _dcs(
        tmp_path,
        player_assignments=[
            PlayerAssignment(players=[1], dsl=_VALID_DSL),
            PlayerAssignment(players=[2], dsl=_OTHER_VALID_DSL),
        ],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert collect_combat_warnings(bcs.id, 2, root=tmp_path) == []


def test_dsl_checked_even_when_slot_exceeds_client_count(tmp_path: Path):
    """A broken DSL is broken whether or not this run has enough clients to reach it."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[4], dsl=_BROKEN_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path)

    assert any("does not parse" in w for w in warnings)


# ---------------------------------------------------------------------------
# Deduplication of strategy references
# ---------------------------------------------------------------------------


def test_strategy_used_by_many_states_warns_once(tmp_path: Path):
    """Five state assignments pointing at one under-staffed strategy is one problem."""
    dcs = _dcs(tmp_path, min_players=4)
    bcs = _bcs(
        tmp_path,
        state_assignments=[
            StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s1"]),
            StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s2"]),
        ],
    )

    warnings = collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1


def test_default_and_assigned_same_strategy_warns_once(tmp_path: Path):
    dcs = _dcs(tmp_path, min_players=4)
    bcs = _bcs(
        tmp_path,
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s1"])],
    )

    warnings = collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1
    assert "Default" in warnings[0]


def test_distinct_strategies_each_warn(tmp_path: Path):
    a = _dcs(tmp_path, name="A", min_players=4)
    b = _dcs(tmp_path, name="B", min_players=3)
    bcs = _bcs(
        tmp_path,
        default_strategy_id=a.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=b.id, state_ids=["s1"])],
    )

    warnings = collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 2


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------


def test_multiple_problem_kinds_are_all_reported(tmp_path: Path):
    dcs = _dcs(
        tmp_path,
        name="Messy",
        min_players=4,
        player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path)

    joined = _joined(warnings)
    assert "requires ≥4 players" in joined
    assert "does not parse" in joined
    assert len(warnings) == 2


# ---------------------------------------------------------------------------
# _format_players
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("players", "expected"),
    [
        ([], "no players"),
        ([1], "P1"),
        ([1, 2, 3], "P1–P3"),
        ([3, 1, 2], "P1–P3"),
        ([1, 3], "P1, P3"),
        ([2, 4], "P2, P4"),
        ([1, 2, 3, 4], "P1–P4"),
    ],
)
def test_format_players(players, expected):
    assert _format_players(players) == expected
