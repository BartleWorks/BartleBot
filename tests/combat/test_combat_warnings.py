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
    check_combat,
    exposed_combat_states,
)
from bartlebot.combat.strategy_store import (
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.paths import project_root
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy

_VALID_DSL = "any<blade>[sharp]@self"
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


def _user_root() -> Path:
    """A user root holding nothing: the redirected ``%APPDATA%``'s vendor folder."""
    from bartlebot.shared.vendor import vendor_dir  # noqa: PLC0415

    return vendor_dir()


def _faults(*args, **kwargs) -> list[str]:
    return check_combat(*args, **kwargs).faults


def _notices(*args, **kwargs) -> list[str]:
    return check_combat(*args, **kwargs).notices


def _joined(warnings: list[str]) -> str:
    return "\n".join(warnings)


# ---------------------------------------------------------------------------
# Top-level guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("active_id", [None, ""])
def test_no_active_strategy_is_a_notice_not_a_fault(active_id):
    """A fresh install: every fight falls to the shipped default, and nothing asks."""
    check = check_combat(active_id, num_clients=2, root=project_root(), user_root=_user_root())

    assert check.faults == []
    assert check.notices == [
        "Every fight runs duel strategy 'Default Team Duel' "
        "(the shipped Team default; this bot has no Bot Combat Strategy)."
    ]


def test_missing_bcs_file_is_a_fault(tmp_path: Path):
    faults = _faults("deadbeef-0000-0000-0000-000000000000", 1, root=tmp_path)

    assert len(faults) == 1
    assert "The Bot Combat Strategy picked for this bot is missing its file" in faults[0]
    assert "deadbeef" in faults[0]


def test_clean_config_produces_no_warnings(tmp_path: Path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert _faults(bcs.id, num_clients=1, root=tmp_path) == []


def test_bcs_with_no_strategies_at_all_is_clean(tmp_path: Path):
    """Running entirely on built-in defaults is a valid configuration."""
    bcs = _bcs(tmp_path)

    assert _faults(bcs.id, num_clients=1, root=tmp_path) == []


# ---------------------------------------------------------------------------
# Missing referenced files
# ---------------------------------------------------------------------------


def test_missing_default_dcs_file(tmp_path: Path):
    bcs = _bcs(tmp_path, default_strategy_id="12345678-0000-0000-0000-000000000000")

    warnings = _faults(bcs.id, 1, root=tmp_path)

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

    warnings = _faults(bcs.id, 1, root=tmp_path)

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

    warnings = _faults(bcs.id, 1, root=tmp_path)

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

    warnings = _faults(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" not in _joined(warnings)


# ---------------------------------------------------------------------------
# min_players
# ---------------------------------------------------------------------------


def test_min_players_shortfall_warns(tmp_path: Path):
    dcs = _dcs(tmp_path, name="Four Player", min_players=4)
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = _faults(bcs.id, num_clients=2, root=tmp_path)

    assert len(warnings) == 1
    assert "requires ≥4 players" in warnings[0]
    assert "only 2 client(s)" in warnings[0]
    assert "Four Player" in warnings[0]


def test_min_players_satisfied_is_silent(tmp_path: Path):
    dcs = _dcs(tmp_path, min_players=2)
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert _faults(bcs.id, num_clients=4, root=tmp_path) == []


def test_min_players_exactly_met_is_silent(tmp_path: Path):
    dcs = _dcs(tmp_path, min_players=2)
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert _faults(bcs.id, num_clients=2, root=tmp_path) == []


# ---------------------------------------------------------------------------
# DSL validation
# ---------------------------------------------------------------------------


def test_broken_inline_dsl_is_reported(tmp_path: Path):
    dcs = _dcs(tmp_path, name="Broken", player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = _faults(bcs.id, 1, root=tmp_path)

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

    warnings = _faults(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" in warnings[0]


@pytest.mark.parametrize(
    "dsl, named",
    [
        ("?(boss.hp > 0) any<damage> @ boss | any<damage> @ enemy", "'hp'"),
        ("?(self.power_pips < 50%) pass | any<damage> @ enemy", "'power_pips' takes no %"),
        ("?(deck.health >= 2) pass | any<damage> @ enemy", "deck.health"),
    ],
)
def test_a_condition_that_reads_nothing_is_a_fault_that_names_it(tmp_path: Path, dsl, named):
    """#214: each of these parsed and was false on every turn, which reads like a
    condition that doesn't hold. Now the run stops before it starts, and says which."""
    pcs = _pcs(tmp_path, "Typo", dsl)
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = _faults(bcs.id, 1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" in warnings[0]
    assert named in warnings[0]


@pytest.mark.parametrize("dsl", ["", "   ", "\n\t "])
def test_empty_dsl_is_never_flagged(tmp_path: Path, dsl):
    """Empty resolves to the built-in default and never runs; a new preset starts
    empty by design, so flagging it would warn on every newly created strategy."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=dsl)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert _faults(bcs.id, 1, root=tmp_path) == []


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

    warnings = _faults(bcs.id, 2, root=tmp_path)

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

    warnings = _faults(bcs.id, 2, root=tmp_path)

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

    assert _faults(bcs.id, 2, root=tmp_path) == []


def test_dsl_checked_even_when_slot_exceeds_client_count(tmp_path: Path):
    """A broken DSL is broken whether or not this run has enough clients to reach it."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[4], dsl=_BROKEN_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    warnings = _faults(bcs.id, num_clients=1, root=tmp_path)

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

    warnings = _faults(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1


def test_default_and_assigned_same_strategy_warns_once(tmp_path: Path):
    dcs = _dcs(tmp_path, min_players=4)
    bcs = _bcs(
        tmp_path,
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s1"])],
    )

    warnings = _faults(bcs.id, num_clients=1, root=tmp_path)

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

    warnings = _faults(bcs.id, num_clients=1, root=tmp_path)

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

    warnings = _faults(bcs.id, num_clients=1, root=tmp_path)

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


def test_exposed_state_without_an_assignment_is_a_notice(tmp_path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("s1", "Defeat Howling Chaney", False))

    check = check_combat(bcs.id, 1, config, root=tmp_path)

    assert check.faults == []
    assert check.notices == [
        "No duel strategy is assigned to 'Defeat Howling Chaney', so it uses 'Test DCS', "
        "the default of your Bot Combat Strategy 'Test BCS'."
    ]


def test_states_using_the_bot_default_are_not_reported(tmp_path):
    """use_default_strategy: true means the author is happy with the default."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("s1", "Ordinary mobs", True))

    assert _faults(bcs.id, 1, config, root=tmp_path) == []


def test_exposed_state_with_an_assignment_is_not_reported(tmp_path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(
        tmp_path,
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s1"])],
    )
    config = _config(("s1", "Defeat Howling Chaney", False))

    assert _faults(bcs.id, 1, config, root=tmp_path) == []


def test_unassigned_state_with_no_default_falls_through_as_a_notice(tmp_path):
    bcs = _bcs(tmp_path)
    config = _config(("s1", "Defeat Howling Chaney", False))

    check = check_combat(bcs.id, 1, config, root=project_root(), user_root=tmp_path)

    assert check.faults == []
    assert check.notices == [
        "Every fight runs duel strategy 'Default Solo Duel' (the shipped Solo default; "
        "'Test BCS' has no duel strategy for this fight and no default)."
    ]


def test_unlabeled_state_falls_back_to_its_id(tmp_path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("state_9", None, False))

    assert "'state_9'" in _joined(_notices(bcs.id, 1, config, root=tmp_path))


def test_every_unassigned_state_is_named_in_one_notice(tmp_path):
    """Atlantea's eight exposed fights on a default are one line, not eight."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("s1", "Howling Chaney", False), ("s2", "Sir Blackwater", False))

    notices = _notices(bcs.id, 1, config, root=tmp_path)

    assert len(notices) == 1
    assert "'Howling Chaney', 'Sir Blackwater'" in notices[0]
    assert "they use 'Test DCS'" in notices[0]


def test_partially_assigned_bot_reports_only_the_gap(tmp_path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(
        tmp_path,
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=dcs.id, state_ids=["s1"])],
    )
    config = _config(("s1", "Howling Chaney", False), ("s2", "Sir Blackwater", False))

    notices = _notices(bcs.id, 1, config, root=tmp_path)

    assert len(notices) == 1
    assert "Sir Blackwater" in notices[0]
    assert "Howling Chaney" not in notices[0]


def test_legacy_use_default_duel_profile_is_still_honored(tmp_path):
    """Users' un-migrated bots use the pre-v3 param name."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
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

    assert "Old Boss" in _joined(_notices(bcs.id, 1, config, root=tmp_path))


def test_omitting_the_config_skips_the_state_check(tmp_path):
    """The parameter is optional — callers without a config get the old behavior."""
    bcs = _bcs(tmp_path)
    assert _faults(bcs.id, 1, root=tmp_path) == []


def test_no_active_strategy_names_no_fight_and_is_no_fault():
    config = _config(("s1", "Howling Chaney", False))
    check = check_combat(None, 1, config, root=project_root(), user_root=_user_root())
    assert check.faults == []
    assert check.notices == [
        "Every fight runs duel strategy 'Default Solo Duel' "
        "(the shipped Solo default; this bot has no Bot Combat Strategy)."
    ]


def test_notices_come_with_faults(tmp_path):
    """A bot can have both at once; neither suppresses the other."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)
    config = _config(("s1", "Howling Chaney", False))

    check = check_combat(bcs.id, 1, config, root=tmp_path)

    assert "No duel strategy is assigned to 'Howling Chaney'" in _joined(check.notices)
    assert "does not parse" in _joined(check.faults)


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
    assert _faults(bcs.id, num_clients=1, root=tmp_path) == []


def test_state_assignment_that_links_nothing_is_reported_as_unassigned(tmp_path: Path):
    """An assignment row exists but was never filled in — same as not being there."""
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id, state_assignments=[StateAssignment(state_ids=["state_9"])])

    check = check_combat(bcs.id, 1, _boss_bot(), root=tmp_path)

    assert check.faults == []
    assert "No duel strategy is assigned to 'Defeat Howling Chaney'" in check.notices[0]


def test_an_unfilled_assignment_with_no_default_falls_through(tmp_path: Path):
    bcs = _bcs(tmp_path, state_assignments=[StateAssignment(state_ids=["state_9"])])

    notices = _notices(bcs.id, 1, _boss_bot(), root=project_root(), user_root=tmp_path)

    assert notices == [
        "Every fight runs duel strategy 'Default Solo Duel' (the shipped Solo default; "
        "'Test BCS' has no duel strategy for this fight and no default)."
    ]


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

    assert _faults(bcs.id, num_clients=1, root=tmp_path) == []


# ---------------------------------------------------------------------------
# The bot's own strategies (#118)
# ---------------------------------------------------------------------------


def test_an_own_duel_is_found_without_a_library_file(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Own P1", dsl=_VALID_DSL)
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=own.id, duel_strategies=[own], player_strategies=[pcs])

    assert _faults(bcs.id, num_clients=1, root=tmp_path) == []


def test_an_own_player_strategys_dsl_is_checked(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Own P1", dsl=_BROKEN_DSL)
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=own.id, duel_strategies=[own], player_strategies=[pcs])

    warnings = _faults(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1
    assert "does not parse" in warnings[0]


def test_a_library_duel_linking_an_own_player_strategy_is_flagged(tmp_path: Path):
    """Only a hand-edited file gets one in; the dialog never offers it."""
    pcs = PlayerCombatStrategy(name="Frost Heal", dsl=_VALID_DSL)
    library = _dcs(
        tmp_path, "Boss Burn", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=library.id, player_strategies=[pcs])

    warnings = _faults(bcs.id, num_clients=1, root=tmp_path)

    assert len(warnings) == 1
    assert "Library duel strategy 'Boss Burn' uses 'Frost Heal'" in warnings[0]


def test_an_own_duel_linking_an_own_player_strategy_is_not_flagged(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Frost Heal", dsl=_VALID_DSL)
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    bcs = _bcs(tmp_path, default_strategy_id=own.id, duel_strategies=[own], player_strategies=[pcs])

    assert not any("belongs to" in w for w in _faults(bcs.id, 1, root=tmp_path))


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

    assert _faults(bcs.id, 1, _lamia_bot(), root=tmp_path) == []


def test_a_fight_whose_node_and_enemy_both_changed_is_reported_as_unassigned(tmp_path: Path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    stored = StateAssignment(fights=[FightRef(state="state_14", enemy="Lamia")], duel_combat_strategy_id=dcs.id)
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id, state_assignments=[stored])

    notices = _notices(bcs.id, 1, _lamia_bot(enemy="Cronus"), root=tmp_path)

    assert len(notices) == 1
    assert "No duel strategy is assigned to 'Defeat Lamia'" in notices[0]


def test_a_warning_names_the_fight_the_assignment_is_bound_to_now(tmp_path: Path):
    """Stored under state_14, bound by enemy to state_20: the label is that node's."""
    stored = StateAssignment(
        fights=[FightRef(state="state_14", enemy="Lamia")],
        duel_combat_strategy_id="abcdef00-0000-0000-0000-000000000000",
    )
    bcs = _bcs(tmp_path, state_assignments=[stored])

    warnings = _faults(bcs.id, 1, _lamia_bot(), root=tmp_path)

    assert "'Defeat Lamia' Duel Strategy file is missing" in warnings[0]


# ---------------------------------------------------------------------------
# An assignment the run can't reach
# ---------------------------------------------------------------------------


def _left_over(tmp_path: Path, duel_id: str) -> BotCombatStrategy:
    """A pick whose one assignment names a fight the bot no longer has."""
    dcs = _dcs(tmp_path, "General", player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    stored = StateAssignment(fights=[FightRef(state="state_99", enemy="Gone")], duel_combat_strategy_id=duel_id)
    return _bcs(tmp_path, default_strategy_id=dcs.id, state_assignments=[stored])


def test_a_left_over_assignment_linking_a_missing_duel_is_no_fault(tmp_path: Path):
    bcs = _left_over(tmp_path, "abcdef00-0000-0000-0000-000000000000")

    assert _faults(bcs.id, 1, _lamia_bot(), root=tmp_path) == []


def test_a_left_over_assignment_needing_more_players_is_no_fault(tmp_path: Path):
    four = _dcs(tmp_path, "Four Box", min_players=4, player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])

    assert _faults(_left_over(tmp_path, four.id).id, 2, _lamia_bot(), root=tmp_path) == []


def test_a_left_over_assignments_broken_dsl_is_no_fault(tmp_path: Path):
    broken = _dcs(tmp_path, "Broken", player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)])

    assert _faults(_left_over(tmp_path, broken.id).id, 1, _lamia_bot(), root=tmp_path) == []


def test_without_the_bot_every_assignment_is_still_checked(tmp_path: Path):
    """No config, no way to tell which fights exist, so none is skipped."""
    bcs = _left_over(tmp_path, "abcdef00-0000-0000-0000-000000000000")

    assert "Duel Strategy file is missing" in _joined(_faults(bcs.id, 1, root=tmp_path))


# ---------------------------------------------------------------------------
# A duel that left the Official set (Stage 5 of #218)
# ---------------------------------------------------------------------------

#: Default Team Boss Duel and Default Turn 1 AoE Mob Duel, no longer shipped.
_RETIRED_DUEL_IDS = ("1bd16a10-285b-4a78-b8d2-cd16433df54e", "7cd5d91b-2d67-4599-bc25-a3169446e258")


@pytest.mark.parametrize("retired_id", _RETIRED_DUEL_IDS)
@pytest.mark.parametrize(
    ("where", "fault"),
    [
        ("default", "Default Duel Strategy file is missing"),
        ("fight", "'Defeat Howling Chaney' Duel Strategy file is missing"),
    ],
)
def test_a_link_to_a_retired_duel_is_a_fault_and_nothing_rewrites_it(
    tmp_path: Path, retired_id: str, where: str, fault: str
):
    """A user's Bot Combat Strategy still linking a duel the set no longer holds, as its
    default or for a fight, gets the "link that points at nothing" fault, and the check
    leaves the file byte-identical: nothing relinks it for the user."""
    if where == "default":
        bcs = _bcs(tmp_path, default_strategy_id=retired_id)
    else:
        bcs = _bcs(tmp_path, state_assignments=[StateAssignment(duel_combat_strategy_id=retired_id, state_ids=["s1"])])
    files = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    config = _config(("s1", "Defeat Howling Chaney", False))

    faults = _faults(bcs.id, 2, config, root=project_root(), user_root=tmp_path)

    assert len(faults) == 1
    assert fault in faults[0]
    assert retired_id[:8] in faults[0]
    assert {path: path.read_bytes() for path in tmp_path.rglob("*.json")} == files


# ---------------------------------------------------------------------------
# Slot order: a strategy aiming at a slot the duel leaves to chance (#212)
# ---------------------------------------------------------------------------

_SUPPORT_DSL = "any<blade>[sharp] @ ally(last) | pass"


def test_a_duel_with_no_combat_order_and_a_slot_target_is_a_slot_order_notice(tmp_path: Path):
    support = _pcs(tmp_path, "Support", _SUPPORT_DSL)
    dcs = _dcs(
        tmp_path,
        name="Unordered Team",
        max_players=4,
        player_assignments=[PlayerAssignment(players=[2, 3, 4], player_combat_strategy_id=support.id)],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    check = check_combat(bcs.id, 4, root=tmp_path)

    assert check.faults == []
    assert len(check.slot_order) == 1
    assert check.slot_order[0].startswith("Duel strategy 'Unordered Team': P2–P4 target ally(last)")
    assert check.slot_order[0] in check.notices


def test_a_combat_order_leaves_no_slot_order_notice(tmp_path: Path):
    dcs = _dcs(
        tmp_path,
        combat_order=[4, 3, 2, 1],
        player_assignments=[PlayerAssignment(players=[2, 3, 4], dsl=_SUPPORT_DSL)],
    )
    bcs = _bcs(tmp_path, default_strategy_id=dcs.id)

    assert check_combat(bcs.id, 4, root=tmp_path).slot_order == []


def test_a_duel_two_fights_share_is_one_slot_order_notice(tmp_path: Path):
    dcs = _dcs(tmp_path, player_assignments=[PlayerAssignment(players=[2], dsl=_SUPPORT_DSL)])
    bcs = _bcs(
        tmp_path,
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(state_ids=["state_9"], duel_combat_strategy_id=dcs.id)],
    )

    assert len(check_combat(bcs.id, 2, _boss_bot(), root=tmp_path).slot_order) == 1
