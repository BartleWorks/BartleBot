"""Tests for bartlebot.combat.combat_warnings.collect_combat_warnings.

Strategies are written to a real tmp_path root rather than mocked, so the
missing-file branches exercise the actual store behavior.  No QApplication is
needed — that is the point of having this outside AppController.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.combat.bot_strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.combat.combat_warnings import (
    _format_players,
    collect_combat_warnings,
    exposed_combat_states,
)
from bartlebot.combat.strategy_store import (
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy

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
    """The label names the state itself now, not a generic "Assigned" — more useful
    when a bot has several state overrides and one file goes missing.
    """
    bcs = _bcs(
        tmp_path,
        state_assignments=[
            StateAssignment(duel_combat_strategy_id="abcdef00-0000-0000-0000-000000000000", state_ids=["s1"])
        ],
    )

    warnings = collect_combat_warnings(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "'s1' Duel Strategy file is missing" in warnings[0]


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


# ---------------------------------------------------------------------------
# Unassigned combat states (5a)
# ---------------------------------------------------------------------------


def _config(*states: tuple[str, str | None, bool]) -> BotConfig:
    """Build a BotConfig from (state_id, label, uses_bot_default) triples."""
    return BotConfig(
        id="test-bot",
        name="Test Bot",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="sleep")]
        + [
            StateModel(
                id=sid,
                node_type="handle_combat",
                label=label,
                params={"use_default_strategy": default},
            )
            for sid, label, default in states
        ],
        transitions=[],
    )


def test_exposed_state_without_an_assignment_is_reported(tmp_path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("s1", "Defeat Howling Chaney", False))

    warnings = collect_combat_warnings(bcs.id, 1, config, root=tmp_path)

    assert warnings == [
        "Combat state 'Defeat Howling Chaney' has no Duel Strategy assigned — will use the bot's default strategy."
    ]


def test_states_using_the_bot_default_are_not_reported(tmp_path):
    """use_default_strategy: true means the author is happy with the default."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("s1", "Ordinary mobs", True))

    assert collect_combat_warnings(bcs.id, 1, config, root=tmp_path) == []


def test_exposed_state_with_an_assignment_is_not_reported(tmp_path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(
        tmp_path,
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s1"])],
    )
    config = _config(("s1", "Defeat Howling Chaney", False))

    assert collect_combat_warnings(bcs.id, 1, config, root=tmp_path) == []


def test_unassigned_state_names_the_builtin_fallback_when_there_is_no_default(tmp_path):
    bcs = _bcs(tmp_path)
    config = _config(("s1", "Defeat Howling Chaney", False))

    warnings = collect_combat_warnings(bcs.id, 1, config, root=tmp_path)

    assert warnings == [
        "Combat state 'Defeat Howling Chaney' has no Duel Strategy assigned — will use built-in combat defaults."
    ]


def test_unlabeled_state_falls_back_to_its_id(tmp_path):
    bcs = _bcs(tmp_path)
    config = _config(("state_9", None, False))

    warnings = collect_combat_warnings(bcs.id, 1, config, root=tmp_path)

    assert "'state_9'" in _joined(warnings)


def test_every_unassigned_state_is_reported_separately(tmp_path):
    bcs = _bcs(tmp_path)
    config = _config(("s1", "Howling Chaney", False), ("s2", "Sir Blackwater", False))

    warnings = collect_combat_warnings(bcs.id, 1, config, root=tmp_path)

    assert len(warnings) == 2
    assert "Howling Chaney" in _joined(warnings)
    assert "Sir Blackwater" in _joined(warnings)


def test_partially_assigned_bot_reports_only_the_gap(tmp_path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(
        tmp_path,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s1"])],
    )
    config = _config(("s1", "Howling Chaney", False), ("s2", "Sir Blackwater", False))

    warnings = collect_combat_warnings(bcs.id, 1, config, root=tmp_path)

    assert len(warnings) == 1
    assert "Sir Blackwater" in warnings[0]


def test_legacy_use_default_duel_profile_is_still_honored(tmp_path):
    """Users' un-migrated bots use the pre-v3 param name."""
    bcs = _bcs(tmp_path)
    config = BotConfig(
        id="test-bot",
        name="Legacy Bot",
        initial="s1",
        recovery_state="s1",
        states=[
            StateModel(
                id="s1",
                node_type="handle_combat",
                label="Old Boss",
                params={"use_default_duel_profile": False},
            )
        ],
        transitions=[],
    )

    warnings = collect_combat_warnings(bcs.id, 1, config, root=tmp_path)

    assert "Old Boss" in _joined(warnings)


def test_omitting_the_config_skips_the_state_check(tmp_path):
    """The parameter is optional — callers without a config get the old behavior."""
    bcs = _bcs(tmp_path)
    assert collect_combat_warnings(bcs.id, 1, root=tmp_path) == []


def test_no_active_strategy_short_circuits_before_the_state_check(tmp_path):
    config = _config(("s1", "Howling Chaney", False))
    warnings = collect_combat_warnings(None, 1, config, root=tmp_path)
    assert len(warnings) == 1
    assert "No Bot Combat Strategy is set" in warnings[0]


def test_state_warnings_come_with_dsl_warnings(tmp_path):
    """A bot can have both problems at once; neither suppresses the other."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("s1", "Howling Chaney", False))

    warnings = collect_combat_warnings(bcs.id, 1, config, root=tmp_path)
    joined = _joined(warnings)

    assert "has no Duel Strategy assigned" in joined
    assert "does not parse" in joined


# ---------------------------------------------------------------------------
# exposed_combat_states
# ---------------------------------------------------------------------------


def test_exposed_combat_states_ignores_non_combat_nodes():
    config = _config(("s1", "Boss", False))
    assert [s.id for s in exposed_combat_states(config)] == ["s1"]


def test_exposed_combat_states_defaults_missing_param_to_using_the_default():
    """A handle_combat state with no param at all is not exposed."""
    config = BotConfig(
        id="test-bot",
        name="B",
        initial="s1",
        recovery_state="s1",
        states=[StateModel(id="s1", node_type="handle_combat")],
        transitions=[],
    )
    assert exposed_combat_states(config) == []


# ---------------------------------------------------------------------------
# Unlinked assignments, and the inline configs removed in #118
# ---------------------------------------------------------------------------


def _boss_bot() -> BotConfig:
    return BotConfig(
        id="test-bot",
        name="Bot",
        initial="s0",
        recovery_state="s0",
        states=[
            StateModel(id="s0", node_type="sleep"),
            StateModel(
                id="state_9",
                node_type="handle_combat",
                label="Defeat Howling Chaney",
                params={"use_default_strategy": False},
            ),
        ],
        transitions=[],
    )


def test_an_unset_default_is_not_reported_as_a_default(tmp_path: Path):
    bcs = _bcs(tmp_path)
    assert collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path) == []


def test_state_assignment_that_links_nothing_is_reported_as_unassigned(tmp_path: Path):
    """An assignment row exists but was never filled in — same as not being there."""
    bcs = _bcs(tmp_path, state_assignments=[StateAssignment(state_ids=["state_9"])])

    warnings = collect_combat_warnings(bcs.id, 1, _boss_bot(), root=tmp_path)

    assert len(warnings) == 1
    assert "'Defeat Howling Chaney' has no Duel Strategy assigned" in warnings[0]
    assert "will use built-in combat defaults" in warnings[0]


def test_unassigned_fallback_message_names_a_linked_default(tmp_path: Path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id, state_assignments=[StateAssignment(state_ids=["state_9"])])

    warnings = collect_combat_warnings(bcs.id, 1, _boss_bot(), root=tmp_path)

    assert "will use the bot's default strategy" in warnings[0]


def test_a_file_from_before_118_is_not_warned_about_its_inline_config(tmp_path: Path):
    """A stray inline config still parses, is never run, and so is never checked."""
    bcs = BotCombatStrategy.model_validate(
        {
            "name": "Old",
            "bot_id": "bot-1",
            "default_duel_config": {"player_assignments": [{"players": [1], "dsl": _BROKEN_DSL}]},
            "state_assignments": [
                {"state_ids": ["s1"], "duel_config": {"player_assignments": [{"players": [1], "dsl": _BROKEN_DSL}]}}
            ],
        }
    )
    save_bot_strategy(bcs, tmp_path)

    assert collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path) == []


# ---------------------------------------------------------------------------
# The bot's own strategies (#118)
# ---------------------------------------------------------------------------


def test_an_own_duel_is_found_without_a_library_file(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Own P1", dsl=_VALID_DSL)
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=own.id, duel_strategies=[own], player_strategies=[pcs])

    assert collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path) == []


def test_an_own_player_strategys_dsl_is_checked(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Own P1", dsl=_BROKEN_DSL)
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=own.id, duel_strategies=[own], player_strategies=[pcs])

    warnings = collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" in warnings[0]


def test_a_library_duel_linking_an_own_player_strategy_is_flagged(tmp_path: Path):
    """Only a hand-edited file gets one in; the dialog never offers it."""
    pcs = PlayerCombatStrategy(name="Frost Heal", dsl=_VALID_DSL)
    library = _dcs(
        tmp_path, "Boss Burn", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=library.id, player_strategies=[pcs])

    warnings = collect_combat_warnings(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1
    assert "Library duel strategy 'Boss Burn' uses 'Frost Heal'" in warnings[0]


def test_an_own_duel_linking_an_own_player_strategy_is_not_flagged(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Frost Heal", dsl=_VALID_DSL)
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=own.id, duel_strategies=[own], player_strategies=[pcs])

    assert not any("belongs to" in w for w in collect_combat_warnings(bcs.id, 1, root=tmp_path))


# ---------------------------------------------------------------------------
# Fight keys — a stored fight follows its enemy to a new node
# ---------------------------------------------------------------------------


def _lamia_bot(state_id: str = "state_20", enemy: str = "Lamia") -> BotConfig:
    return BotConfig(
        id="bot-1",
        name="Lamia Farm",
        initial="s0",
        recovery_state="s0",
        states=[
            StateModel(id="s0", node_type="sleep"),
            StateModel(
                id=state_id,
                node_type="handle_combat",
                label="Defeat Lamia",
                params={"use_default_strategy": False, "enemy": enemy},
            ),
        ],
        transitions=[],
    )


def test_a_fight_recreated_against_the_same_enemy_is_not_reported_as_unassigned(tmp_path: Path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    stored = StateAssignment(fights=[FightRef(state="state_14", enemy="Lamia")], duel_combat_strategy_id=dcs.id)
    bcs = _bcs(tmp_path, state_assignments=[stored])

    assert collect_combat_warnings(bcs.id, 1, _lamia_bot(), root=tmp_path) == []


def test_a_fight_whose_node_and_enemy_both_changed_is_reported_as_unassigned(tmp_path: Path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    stored = StateAssignment(fights=[FightRef(state="state_14", enemy="Lamia")], duel_combat_strategy_id=dcs.id)
    bcs = _bcs(tmp_path, state_assignments=[stored])

    warnings = collect_combat_warnings(bcs.id, 1, _lamia_bot(enemy="Cronus"), root=tmp_path)

    assert len(warnings) == 1
    assert "'Defeat Lamia' has no Duel Strategy assigned" in warnings[0]


def test_a_warning_names_the_fight_the_assignment_is_bound_to_now(tmp_path: Path):
    """Stored under state_14, bound by enemy to state_20: the label is that node's."""
    stored = StateAssignment(
        fights=[FightRef(state="state_14", enemy="Lamia")],
        duel_combat_strategy_id="abcdef00-0000-0000-0000-000000000000",
    )
    bcs = _bcs(tmp_path, state_assignments=[stored])

    warnings = collect_combat_warnings(bcs.id, 1, _lamia_bot(), root=tmp_path)

    assert "'Defeat Lamia' Duel Strategy file is missing" in warnings[0]
