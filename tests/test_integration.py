"""Integration tests: exercises the seams between config, machine, and nodes.

These tests cover five integration boundaries that existing unit tests leave untested:

  1. Config pipeline: BotConfig.to_machine_config() produces the correct structure
     that BotMachine expects (condition strings, flag registry, round-trip save/load).
  2. BotMachine construction from real config: a real bot JSON file can be loaded,
     converted, and used to construct BotMachine without errors.
  3. Flag transitions through BotMachine: BotConfig-generated flag conditions
     (_flag_X, _flag_compound_X) are evaluated correctly by the live FSM.
  4. Real node execution through the FSM: enter_zone returns SUCCESS/FAILURE and
     the machine routes those results through real transitions from a real BotConfig.
  5. Shipped bot corpus health: every file under bots/ carries a stable, unique id
     and uses no legacy node params.

What is NOT tested here (by design):
  - AppController / GUI: requires QApplication, high setup cost for wiring logic.
  - WizWalker / WizSprinter APIs: FakeClient stubs these out (same as node unit tests).
"""

import asyncio
import itertools
import json
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker.errors import ClientClosedError, ExceptionalTimeout, MemoryReadError

import bartlebot.nodes.zone_entry.enter_zone as gtz
from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, BotOptionModel, FlagCondition, FlagGroup, StateModel, TransitionModel
from bartlebot.bot_options import (
    orphaned_flag_options,
    required_option_specs,
    storable_options,
    system_option_names,
)
from bartlebot.nodes.registry import NODE_REGISTRY
from bartlebot.template_store import list_templates
from tests.conftest import FakeClient

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BOTS_DIR = Path(__file__).parent.parent / "bots"

# ---------------------------------------------------------------------------
# Part 1: Config Pipeline
# ---------------------------------------------------------------------------


async def test_real_bot_config_loads_and_round_trips(tmp_path):
    """Load a real bot file, save it to a temp path, reload it, and compare key fields."""
    src = _BOTS_DIR / "04_mooshu" / "crimson-fields.json"
    config = BotConfig.load(src)
    dest = tmp_path / "roundtrip.json"
    config.save(dest)
    reloaded = BotConfig.load(dest)

    assert reloaded.name == config.name
    assert reloaded.initial == config.initial
    assert len(reloaded.states) == len(config.states)
    assert len(reloaded.transitions) == len(config.transitions)
    assert len(reloaded.options) == len(config.options)
    assert [o.name for o in reloaded.options] == [o.name for o in config.options]
    assert [s.id for s in reloaded.states] == [s.id for s in config.states]


async def test_to_machine_config_condition_string_format():
    """to_machine_config() converts 'when' to _result_is_<VALUE> condition strings.

    Verifies the specific string format that BotMachine.__getattr__ expects.
    Unconditional transitions (no 'when') must not have a conditions key.
    """
    config = BotConfig(
        id="test-bot",
        name="test",
        initial="a",
        recovery_state="a",
        states=[
            StateModel(id="a", node_type="mock"),
            StateModel(id="b", node_type="mock"),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="a", dest="a", when="FAILURE"),
            TransitionModel(trigger="advance", source="b", dest="b"),  # unconditional fallthrough
        ],
    )
    mc = config.to_machine_config()

    trans_by_key = {(t["source"], t["dest"]): t for t in mc["transitions"]}
    assert "_result_is_SUCCESS" in trans_by_key[("a", "b")].get("conditions", [])
    assert "_result_is_FAILURE" in trans_by_key[("a", "a")].get("conditions", [])
    assert "conditions" not in trans_by_key[("b", "b")]


async def test_to_machine_config_simple_flag_condition():
    """Single non-negated flag uses the backward-compat _flag_<name> form.

    When exactly one flag is present, non-negated, AND-combined, to_machine_config()
    emits _flag_<name> directly instead of a compound registry entry.
    """
    config = BotConfig(
        id="test-bot",
        name="test",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="mock"), StateModel(id="b", node_type="mock")],
        transitions=[
            TransitionModel(
                trigger="advance",
                source="a",
                dest="b",
                when="SUCCESS",
                flags=[FlagCondition(option="repeat")],
            ),
            TransitionModel(trigger="advance", source="a", dest="a"),
            TransitionModel(trigger="advance", source="b", dest="b"),
        ],
    )
    mc = config.to_machine_config()

    success_trans = next(t for t in mc["transitions"] if t["source"] == "a" and t["dest"] == "b")
    assert "_flag_repeat" in success_trans["conditions"]
    # Simple path does not populate the compound registry
    assert mc["conditions_registry"] == {}


async def test_to_machine_config_compound_flag_conditions_registered():
    """Two-flag AND condition uses _flag_compound_<idx> and registers an entry.

    When multiple flags are present (or negate=True), to_machine_config() emits a
    _flag_compound_<idx> condition name and stores the expression in conditions_registry
    so BotMachine.__getattr__ can evaluate it.
    """
    config = BotConfig(
        id="test-bot",
        name="test",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="mock"), StateModel(id="b", node_type="mock")],
        transitions=[
            TransitionModel(
                trigger="advance",
                source="a",
                dest="b",
                when="SUCCESS",
                flags=[FlagCondition(option="go"), FlagCondition(option="bonus")],
                flag_op="AND",
            ),
            TransitionModel(trigger="advance", source="a", dest="a"),
            TransitionModel(trigger="advance", source="b", dest="b"),
        ],
    )
    mc = config.to_machine_config()

    success_trans = next(t for t in mc["transitions"] if t["source"] == "a" and t["dest"] == "b")
    cond_name = next(c for c in success_trans["conditions"] if c.startswith("_flag_compound_"))

    assert cond_name in mc["conditions_registry"]
    reg = mc["conditions_registry"][cond_name]
    assert reg["op"] == "AND"
    assert {f["option"] for f in reg["flags"]} == {"go", "bonus"}


# ---------------------------------------------------------------------------
# Part 2: BotMachine construction from real config
# ---------------------------------------------------------------------------


async def test_botmachine_constructs_from_real_bot_config():
    """BotMachine can be built from a real bot JSON file without errors.

    Exercises the full path: BotConfig.load() → to_machine_config() → BotMachine()
    and verifies that the machine starts at the correct initial state.
    """
    src = _BOTS_DIR / "04_mooshu" / "crimson-fields.json"
    config = BotConfig.load(src)
    mc = config.to_machine_config()

    machine = BotMachine(clients=[], config=mc, node_registry=NODE_REGISTRY, bot_config=config)

    assert machine.state == config.initial


async def test_a_bot_wide_option_is_visible_via_get_option():
    """The declaration answers when the user has set no override of their own. A node
    can no longer pin a bot-wide option for itself: that was the same author-vs-user
    confusion one level down, and the field is gone."""
    config = BotConfig(
        id="test-bot",
        name="test",
        initial="state_a",
        recovery_state="state_a",
        options=[BotOptionModel(name="Safety Delay", type="float", default=5.0)],
        states=[StateModel(id="state_a", node_type="mock")],
        transitions=[],  # terminal state — machine stops after one node execution
    )
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[],
        config=mc,
        node_registry={"mock": AsyncMock(return_value="SUCCESS")},
        bot_config=config,
    )

    assert machine.state == "state_a"
    assert machine.get_option("Safety Delay") == 5.0
    assert machine.get_option("Safety Delay", default=3.0) == 5.0


# ---------------------------------------------------------------------------
# Part 3: Flag transitions through BotMachine
# ---------------------------------------------------------------------------

# Two-state FSM used across flag tests:
#   state_a → state_b  when SUCCESS + flag condition (guarded)
#   state_a → state_a  fallthrough (no condition guard — fires when guarded transition fails)
#   state_b            terminal (no outgoing transitions — machine auto-stops)


def _make_machine_for_flag_test(options: list[BotOptionModel], flags: list[FlagCondition], flag_op: str = "AND"):
    """Build a BotMachine for testing flag-guarded transitions.

    The mock node always returns 'SUCCESS'.  state_b is terminal so the machine
    stops naturally after one successful guarded advance.  A nonlocal tick counter
    caps execution at 2 ticks to keep the failure-path tests finite.
    """
    tick = 0

    async def _mock_node(machine, clients, params):
        nonlocal tick
        tick += 1
        if tick >= 2:
            machine.stop()
        return "SUCCESS"

    config = BotConfig(
        id="test-bot",
        name="flag-test",
        initial="state_a",
        recovery_state="state_a",
        options=options,
        states=[
            StateModel(id="state_a", node_type="mock"),
            StateModel(id="state_b", node_type="mock"),
        ],
        transitions=[
            TransitionModel(
                trigger="advance",
                source="state_a",
                dest="state_b",
                when="SUCCESS",
                flags=flags,
                flag_op=flag_op,
            ),
            TransitionModel(trigger="advance", source="state_a", dest="state_a"),
            # state_b: no outgoing transitions → terminal
        ],
    )
    transitions_seen: list[tuple] = []
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[],
        config=mc,
        node_registry={"mock": _mock_node},
        bot_config=config,
        on_transition=lambda prev, result, nxt: transitions_seen.append((prev, result, nxt)),
    )
    return machine, transitions_seen


async def test_flag_true_takes_guarded_transition():
    """When the flag option is true the guarded (state_a → state_b) transition fires."""
    machine, seen = _make_machine_for_flag_test(
        options=[BotOptionModel(name="go", type="bool", default=True)],
        flags=[FlagCondition(option="go")],
    )
    await machine.run()

    assert ("state_a", "SUCCESS", "state_b") in seen


async def test_flag_false_takes_fallthrough_transition():
    """When the flag option is false the guarded transition is skipped; fallthrough fires."""
    machine, seen = _make_machine_for_flag_test(
        options=[BotOptionModel(name="go", type="bool", default=False)],
        flags=[FlagCondition(option="go")],
    )
    await machine.run()

    # Every recorded transition should be the self-loop (a→a), never a→b
    assert all(prev == "state_a" and nxt == "state_a" for prev, _, nxt in seen)
    assert ("state_a", "SUCCESS", "state_b") not in seen


async def test_compound_flag_AND_all_true_fires():
    """Compound AND condition fires the guarded transition when all flags are true."""
    machine, seen = _make_machine_for_flag_test(
        options=[
            BotOptionModel(name="go", type="bool", default=True),
            BotOptionModel(name="bonus", type="bool", default=True),
        ],
        flags=[FlagCondition(option="go"), FlagCondition(option="bonus")],
        flag_op="AND",
    )
    await machine.run()

    assert ("state_a", "SUCCESS", "state_b") in seen


async def test_compound_flag_AND_one_false_falls_through():
    """Compound AND condition fails when one flag is false; fallthrough (a→a) fires instead."""
    machine, seen = _make_machine_for_flag_test(
        options=[
            BotOptionModel(name="go", type="bool", default=True),
            BotOptionModel(name="bonus", type="bool", default=False),  # ← one flag false
        ],
        flags=[FlagCondition(option="go"), FlagCondition(option="bonus")],
        flag_op="AND",
    )
    await machine.run()

    assert all(prev == "state_a" and nxt == "state_a" for prev, _, nxt in seen)
    assert ("state_a", "SUCCESS", "state_b") not in seen


# Grouped flags are the two-level form the edge editor writes: each group combines its
# own flags with its op, and the groups are combined with flag_groups_op. They compile to
# a different registry shape from the flat form above, evaluated by a different branch.


def _group(op: str, *options: str) -> FlagGroup:
    """A FlagGroup over these option names; a leading '!' negates one."""
    return FlagGroup(op=op, flags=[FlagCondition(option=o.lstrip("!"), negate=o.startswith("!")) for o in options])


async def _run_grouped_flag_bot(values: dict[str, bool], groups: list[FlagGroup], groups_op: str) -> list[tuple]:
    """Run the two-state flag bot with a grouped guard on state_a → state_b; return the transitions taken."""
    tick = 0

    async def _mock_node(machine, clients, params):
        nonlocal tick
        tick += 1
        if tick >= 2:
            machine.stop()
        return "SUCCESS"

    config = BotConfig(
        id="test-bot",
        name="flag-group-test",
        initial="state_a",
        recovery_state="state_a",
        options=[BotOptionModel(name=name, type="bool", default=value) for name, value in values.items()],
        states=[StateModel(id="state_a", node_type="mock"), StateModel(id="state_b", node_type="mock")],
        transitions=[
            TransitionModel(
                trigger="advance",
                source="state_a",
                dest="state_b",
                when="SUCCESS",
                flag_groups=groups,
                flag_groups_op=groups_op,
            ),
            TransitionModel(trigger="advance", source="state_a", dest="state_a"),
        ],
    )
    seen: list[tuple] = []
    machine = BotMachine(
        clients=[],
        config=config.to_machine_config(),
        node_registry={"mock": _mock_node},
        bot_config=config,
        on_transition=lambda prev, result, nxt: seen.append((prev, result, nxt)),
    )
    await machine.run()
    return seen


_A_AND_B_OR_C = [_group("AND", "a", "b"), _group("AND", "c")]
_A_OR_B_AND_C = [_group("OR", "a", "b"), _group("AND", "c")]
_A_AND_NOT_B = [_group("AND", "a", "!b")]


@pytest.mark.parametrize(
    ("values", "groups", "groups_op", "fires"),
    [
        pytest.param({"a": True, "b": False, "c": True}, _A_AND_B_OR_C, "OR", True, id="(a&b)|c-second-group"),
        pytest.param({"a": True, "b": True, "c": False}, _A_AND_B_OR_C, "OR", True, id="(a&b)|c-first-group"),
        pytest.param({"a": True, "b": False, "c": False}, _A_AND_B_OR_C, "OR", False, id="(a&b)|c-neither"),
        pytest.param({"a": False, "b": True, "c": True}, _A_OR_B_AND_C, "AND", True, id="(a|b)&c-both"),
        pytest.param({"a": False, "b": True, "c": False}, _A_OR_B_AND_C, "AND", False, id="(a|b)&c-one"),
        pytest.param({"a": True, "b": False}, _A_AND_NOT_B, "OR", True, id="a&!b-b-false"),
        pytest.param({"a": True, "b": True}, _A_AND_NOT_B, "OR", False, id="a&!b-b-true"),
    ],
)
async def test_grouped_flags_decide_the_guarded_transition(values, groups, groups_op, fires):
    seen = await _run_grouped_flag_bot(values, groups, groups_op)

    assert (("state_a", "SUCCESS", "state_b") in seen) is fires


# ---------------------------------------------------------------------------
# Part 4: Real node execution through BotMachine
# ---------------------------------------------------------------------------

# Shared params for enter_zone in all real-node tests
_GZ_PARAMS = {"from_zone": "Src", "to_zone": "Dst", "xyz": {"x": 0.0, "y": 0.0, "z": 0.0}}

# Safety Delay = 0.0 so await_instance_prompt returns immediately in tests
_SAFETY_DELAY_ZERO = BotOptionModel(name="Safety Delay", type="float", default=0.0)


def _make_enter_zone_gateway_client_success():
    """FakeClient that simulates a successful Src → Dst zone transition.

    It reads "Src" for the pre-condition check and the first leave-poll, then "Dst"
    for good. Not a fixed-length script: how often enter_zone reads the zone depends
    on how many trigger attempts it takes, and a script it outruns raises inside the
    node -- which the machine routes back to the same state, forever, so the test
    hangs rather than failing.
    """
    client = FakeClient()
    client.window_handle = 99999  # required by _client_status_watcher
    client.zone_name.side_effect = itertools.chain(["Src", "Src"], itertools.repeat("Dst"))
    return client


async def test_enter_zone_success_advances_state():
    """enter_zone returning SUCCESS routes the FSM from state_a to the terminal state_b.

    This is the primary seam test: a real node function executes via the FSM engine,
    its return value is routed through a real transition, and on_transition fires with
    the correct arguments.
    """
    client = _make_enter_zone_gateway_client_success()
    transitions_seen: list[tuple] = []

    config = BotConfig(
        id="test-bot",
        name="gz-success-test",
        initial="state_a",
        recovery_state="state_a",
        options=[_SAFETY_DELAY_ZERO],
        states=[
            StateModel(id="state_a", node_type="enter_zone", params=_GZ_PARAMS),
            StateModel(id="state_b", node_type="enter_zone", params=_GZ_PARAMS),
            # state_b has no outgoing transitions → terminal
        ],
        transitions=[
            TransitionModel(trigger="advance", source="state_a", dest="state_b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="state_a", dest="state_a", when="FAILURE"),
        ],
    )
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[client],
        config=mc,
        node_registry=NODE_REGISTRY,
        bot_config=config,
        on_transition=lambda prev, result, nxt: transitions_seen.append((prev, result, nxt)),
    )

    with patch.object(gtz, "POLL_INTERVAL", 0), patch.object(gtz, "ZONE_CHANGE_TIMEOUT", 1.0):
        await machine.run()

    assert ("state_a", "SUCCESS", "state_b") in transitions_seen


async def test_enter_zone_failure_self_loops():
    """enter_zone returning FAILURE self-loops on state_a until the machine's idle limit stops it.

    Verifies that FAILURE results are routed through real transitions and that
    max_consecutive_same_state provides a circuit-breaker for a stuck node.
    """
    client = FakeClient()
    client.window_handle = 99999
    client.zone_name.return_value = "Src"  # never changes → zone change times out → FAILURE

    transitions_seen: list[tuple] = []

    config = BotConfig(
        id="test-bot",
        name="gz-failure-test",
        initial="state_a",
        recovery_state="state_a",
        options=[_SAFETY_DELAY_ZERO],
        states=[
            StateModel(id="state_a", node_type="enter_zone", params=_GZ_PARAMS),
            StateModel(id="state_b", node_type="enter_zone", params=_GZ_PARAMS),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="state_a", dest="state_b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="state_a", dest="state_a", when="FAILURE"),
        ],
    )
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[client],
        config=mc,
        node_registry=NODE_REGISTRY,
        bot_config=config,
        max_consecutive_same_state=2,  # stop after 2 consecutive FAILURE self-loops
        on_transition=lambda prev, result, nxt: transitions_seen.append((prev, result, nxt)),
    )

    with patch.object(gtz, "ZONE_CHANGE_TIMEOUT", 0):
        await machine.run()

    assert len(transitions_seen) == 2
    assert all(result == "FAILURE" for _, result, _ in transitions_seen)
    assert all(prev == "state_a" and nxt == "state_a" for prev, _, nxt in transitions_seen)
    assert ("state_a", "SUCCESS", "state_b") not in transitions_seen


# ---------------------------------------------------------------------------
# Part 5: Machine stop during execution
# ---------------------------------------------------------------------------


async def test_stop_during_node_execution_cooperative():
    """A node that polls machine.is_stopped exits cooperatively before the grace period.

    Verifies the full pipeline: BotConfig → to_machine_config() → BotMachine with a
    cooperative node that checks is_stopped in a loop. stop() is called externally while
    the node is running; the node exits cleanly and no transition fires (the stop flag
    prevents advance).
    """
    execution_started = asyncio.Event()
    transitions_seen: list[tuple] = []

    async def cooperative_node(machine, clients, params):
        execution_started.set()
        while not machine.is_stopped:
            await asyncio.sleep(0.01)
        return "FAILURE"

    config = BotConfig(
        id="test-bot",
        name="stop-cooperative-test",
        initial="state_a",
        recovery_state="state_a",
        states=[
            StateModel(id="state_a", node_type="coop"),
            StateModel(id="state_b", node_type="coop"),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="state_a", dest="state_b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="state_a", dest="state_a", when="FAILURE"),
        ],
    )
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[],
        config=mc,
        node_registry={"coop": cooperative_node},
        bot_config=config,
        stop_grace_period=1.0,
        on_transition=lambda prev, result, nxt: transitions_seen.append((prev, result, nxt)),
    )

    task = asyncio.create_task(machine.run())
    await execution_started.wait()
    machine.stop()
    await asyncio.wait_for(task, timeout=1.5)

    assert machine.is_stopped
    assert machine.state == "state_a"
    assert ("state_a", "SUCCESS", "state_b") not in transitions_seen


async def test_stop_forces_cancellation_after_grace_period():
    """A node that never checks is_stopped is forcibly cancelled after the grace period.

    Verifies that _deferred_cancel fires after stop_grace_period seconds and the FSM
    exits cleanly via the CancelledError path in run() (machine.py lines 245-249).
    No state transition fires.
    """
    execution_started = asyncio.Event()
    transitions_seen: list[tuple] = []

    async def blocking_node(machine, clients, params):
        execution_started.set()
        await asyncio.sleep(30)  # never returns within any reasonable test window
        return "SUCCESS"

    config = BotConfig(
        id="test-bot",
        name="stop-forced-cancel-test",
        initial="state_a",
        recovery_state="state_a",
        states=[
            StateModel(id="state_a", node_type="blocker"),
            StateModel(id="state_b", node_type="blocker"),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="state_a", dest="state_b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="state_a", dest="state_a", when="FAILURE"),
        ],
    )
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[],
        config=mc,
        node_registry={"blocker": blocking_node},
        bot_config=config,
        stop_grace_period=0.05,
        on_transition=lambda prev, result, nxt: transitions_seen.append((prev, result, nxt)),
    )

    task = asyncio.create_task(machine.run())
    await execution_started.wait()
    machine.stop()
    await asyncio.wait_for(task, timeout=0.5)

    assert machine.is_stopped
    assert machine.state == "state_a"
    assert ("state_a", "SUCCESS", "state_b") not in transitions_seen


async def test_stop_while_machine_is_paused_with_real_config():
    """stop() unblocks a paused machine built from a real BotConfig and exits cleanly.

    Integration-level counterpart to test_machine.py::test_stop_while_paused_exits_cleanly.
    Uses the full BotConfig.load() → to_machine_config() → BotMachine path with the
    real crimson-fields.json bot file.
    """
    src = _BOTS_DIR / "04_mooshu" / "crimson-fields.json"
    config = BotConfig.load(src)
    mc = config.to_machine_config()
    machine = BotMachine(clients=[], config=mc, node_registry=NODE_REGISTRY, bot_config=config)

    machine.pause()
    task = asyncio.create_task(machine.run())
    # Yield control so the run() loop can start and block on _resume_event.wait().
    await asyncio.sleep(0.05)
    machine.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert machine.is_stopped


@pytest.mark.parametrize(
    "error",
    [ClientClosedError(), MemoryReadError(299), ExceptionalTimeout("memory poll timed out")],
    ids=["client-closed", "memory-read", "exceptional-timeout"],
)
async def test_a_dead_client_pauses_the_run_instead_of_failing_the_node(error):
    """A node that hits a dead client pauses the FSM for recovery rather than returning FAILURE.

    Treated as a FAILURE, the retry and no-match counters would run out before the client
    loop noticed the removal. So the node runs once, the machine parks with no transition
    fired, and stop() still unblocks that parked wait.

    ClientClosedError takes no arguments. This test used to construct it with a message,
    which raised TypeError instead -- so it passed through the generic FAILURE branch and
    never reached the one it was named for.
    """
    calls = 0
    transitions_seen: list[tuple] = []

    async def error_node(machine, clients, params):
        nonlocal calls
        calls += 1
        raise error

    config = BotConfig(
        id="test-bot",
        name="client-error-pause-test",
        initial="state_a",
        recovery_state="state_a",
        states=[
            StateModel(id="state_a", node_type="error_node"),
            StateModel(id="state_b", node_type="error_node"),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="state_a", dest="state_b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="state_a", dest="state_a", when="FAILURE"),
        ],
    )
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[],
        config=mc,
        node_registry={"error_node": error_node},
        bot_config=config,
        on_transition=lambda prev, result, nxt: transitions_seen.append((prev, result, nxt)),
    )

    task = asyncio.create_task(machine.run())
    assert await machine.wait_until_parked(1.0), "the machine should pause itself, not retry the node"
    machine.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert calls == 1
    assert transitions_seen == []
    assert machine.is_stopped
    assert machine.state == "state_a"


# ---------------------------------------------------------------------------
# Part 5: Shipped bot corpus health
# ---------------------------------------------------------------------------


def _all_bot_files() -> list[Path]:
    """Every bot under ``bots/``, and every template's draft, which each bot made from it starts as.

    Drafts, not released versions: a version shares its draft's id, and is checked by
    the shipped-template tests against the hash it was released with.
    """
    files = sorted(_BOTS_DIR.rglob("*.json"))
    assert files, f"no bot files found under {_BOTS_DIR}"
    return files + [t.draft_path for t in list_templates(_BOTS_DIR.parent)]


def _rel(path: Path) -> str:
    """``bots/04_mooshu/crimson-fields.json`` or ``templates/boss-farmer/draft.json``, for a message."""
    return path.relative_to(_BOTS_DIR.parent).as_posix()


def _load_raw(path: Path) -> dict:
    """Parse a bot file as raw JSON — BotConfig.load() would mask a missing id."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_every_bot_file_has_an_id():
    """A bot without `id` no longer loads at all: every value the user stores
    against a bot — its combat strategy, and now each of its option values —
    hangs off that id, and a minted one would be a fresh identity each load.
    Migrated by scripts/migrate_bot_ids.py."""
    missing = [_rel(p) for p in _all_bot_files() if not _load_raw(p).get("id")]

    assert not missing, f"bot files with no id: {missing}"


def test_opening_and_saving_a_bot_changes_nothing_in_the_file(tmp_path):
    """The churn goal, as a check: `bots/` moved on every run because opening a
    bot rewrote Safety Delay from the app setting, and editing any value wrote
    it back into the file. Nothing rewrites an option on load now.

    The whole file, not only its options. That is worth more than it looks: the
    library also carried pre-epic legacy combat keys and hand-written field
    order that a save silently dropped or reordered, so *any* save churned a
    third of the library for reasons no diff explained. Every file has now been
    through one, and a save is a no-op from here.
    """
    dest = tmp_path / "roundtrip.json"
    churned = []
    for path in _all_bot_files():
        BotConfig.load(path).save(dest)
        if _load_raw(path) != _load_raw(dest):
            churned.append(_rel(path))

    assert not churned, f"opening and saving changed: {churned}"


def test_no_bot_guards_a_transition_on_an_option_nothing_declares():
    """The cost of deriving declarations, as a check: an option only exists while
    a node in the graph requires it or the file hand-declares it, so cutting a
    node can take one out from under a transition that reads it. Nothing errors
    when that happens -- ``_flag_<name>`` evaluates False -- so the bot silently
    stops taking the edge. The editor's warning banner is the other half; this
    keeps the shipped library clean of them."""
    orphans = {}
    for path in _all_bot_files():
        names = sorted({name for _t, name in orphaned_flag_options(BotConfig.load(path))})
        if names:
            orphans[_rel(path)] = names

    assert not orphans, f"transitions reading options nothing declares: {orphans}"


def test_bot_ids_are_unique():
    """Two bots sharing an id conflate onto one BotCombatStrategy."""
    ids = [_load_raw(p).get("id") for p in _all_bot_files()]
    duplicates = {bot_id: count for bot_id, count in Counter(ids).items() if bot_id and count > 1}

    assert not duplicates, f"bot ids used by more than one file: {duplicates}"


def test_no_bot_carries_a_dead_quicksell_param():
    """Quicksell's five settings are bot options now, so a param left in a state is
    dead weight that ``run()`` will never read.

    That matters most for ``keep_list``: a file still carrying one looks configured
    and sells the very drops its author picked out. scripts/migrate_quicksell_options.py
    moved them; this is what keeps them moved, since an older editor saving over a bot
    would put them back.
    """
    offenders: list[str] = []
    for path in _all_bot_files():
        for state in _load_raw(path).get("states") or []:
            if state.get("node_type") != "quicksell":
                continue
            for key in sorted(state.get("params") or {}):
                offenders.append(f"{_rel(path)}:{state.get('id')} → {key}")

    assert not offenders, f"quicksell states still carrying params: {offenders}"


def test_no_bot_carries_a_dead_refill_potions_param():
    """Refill Potions is a bot option the app setting answers, so a ``refill_potions``
    param left on a ``handle_potions`` state is never read. Left in place it would look
    like a choice the bot made, and an older editor saving over a bot would put it back.
    """
    offenders = [
        f"{_rel(path)}:{state.get('id')}"
        for path in _all_bot_files()
        for state in _load_raw(path).get("states") or []
        if state.get("node_type") == "handle_potions" and "refill_potions" in (state.get("params") or {})
    ]

    assert not offenders, f"handle_potions states still carrying refill_potions: {offenders}"


def test_no_bot_declares_a_quicksell_flag_it_does_not_mean_to():
    """A sell flag declared equal to the value it would resolve to anyway says nothing,
    and outranks the user's own app setting while saying it.

    The save-time prune drops a declaration matching the NodeSpec default, but these
    three declare ``None`` — the author has no opinion — so a stored ``true``/``false``
    always survives it and has to be a real choice.
    """
    from bartlebot.nodes.utility.quicksell import SELL_FLAG_DEFAULTS, SELL_FLAG_OPTIONS  # noqa: PLC0415

    floors = {SELL_FLAG_OPTIONS[key]: floor for key, floor in SELL_FLAG_DEFAULTS.items()}
    offenders: list[str] = []
    for path in _all_bot_files():
        for opt in _load_raw(path).get("options") or []:
            floor = floors.get(opt.get("name"))
            if floor is not None and opt.get("default") is floor:
                offenders.append(f"{_rel(path)} → {opt['name']}={floor}")

    assert not offenders, f"bots declaring a quicksell flag that changes nothing: {offenders}"


def test_every_self_loop_lands_on_a_node_with_a_retry_cap():
    """A self-loop on an uncapped node spins forever: nothing else ever stops it.

    AppController passes no machine-wide ``max_consecutive_same_state``, so the only bound
    on a state re-entering itself is its node's own ``max_consecutive_retries``. Two bots
    had one on a node without it -- a wait_for_health retried in two-minute waits forever,
    and an unbounded apply_level_scaling -- and both nodes carry a cap now.
    """
    from bartlebot.nodes.registry import NODE_SPECS  # noqa: PLC0415

    offenders: list[str] = []
    for path in _all_bot_files():
        raw = _load_raw(path)
        node_types = {s.get("id"): s.get("node_type") for s in raw.get("states") or []}
        for t in raw.get("transitions") or []:
            if t.get("source") != t.get("dest"):
                continue
            node_type = node_types.get(t.get("source"))
            spec = NODE_SPECS.get(node_type)
            if spec is not None and spec.max_consecutive_retries is None:
                offenders.append(f"{_rel(path)}:{t.get('source')} ({node_type})")
    assert not offenders, f"self-loops on a node with no retry cap: {offenders}"


def test_no_bot_stores_a_friend_name():
    """A character name in a bot file is both a privacy leak and a portability bug.

    This repo goes public at 1.0.0 without rewriting history, so a name committed now
    ships then and links the account to whoever wrote the commit (CLAUDE.md, "No real
    player data"). It also breaks the bot for everyone else, who has no such friend.

    The teleport target is the Friend Name bot option or a hooked player slot instead.
    The option's value lives in settings.json and never in the file; the properties panel
    has no such field to write from. So a file carrying one was either hand-edited or
    predates the change.

    Both places are checked: the old param, and a *declaration* of the option — an
    author who typed a name into the Options tab would put it right back in the file.
    """
    offenders: list[str] = []
    for path in _all_bot_files():
        raw = _load_raw(path)
        for state in raw.get("states") or []:
            params = state.get("params")
            if isinstance(params, dict) and params.get("friend_name"):
                offenders.append(f"{_rel(path)}:{state.get('id')}")
        for opt in raw.get("options") or []:
            if opt.get("name") == "Friend Name" and opt.get("default"):
                offenders.append(f"{_rel(path)} → Friend Name declaration")

    assert not offenders, f"bot states storing a character name: {offenders}"


def test_every_reagent_id_names_a_real_entity():
    """A plain collect_reagent name must match one ID exactly (#144), so a typo finds nothing.

    And finding nothing is a quiet SUCCESS, since an empty spawn is normal: the bot would
    run its whole route collecting nothing, with no line in the log to say why. So each
    plain name is checked against the game's own IDs, spelled as the game spells it --
    matching ignores case, but a shipped file has no reason to. A reagent node has no
    display name, so it is in reagent_nodes.json and not entity_names.json; a chest is the
    other way round. Regenerate with tools/dump_reagent_nodes.py after a patch.

    A wildcard pattern is skipped: it names no single ID to look up.
    """
    from bartlebot import entity_names  # noqa: PLC0415
    from bartlebot.nodes.node_helpers import has_glob  # noqa: PLC0415

    reagents = entity_names.reagent_node_ids()
    assert reagents, "bartlebot/data/reagent_nodes.json is missing or empty"

    unknown: list[str] = []
    for path in _all_bot_files():
        for state in _load_raw(path).get("states") or []:
            if state.get("node_type") != "collect_reagent":
                continue
            for name in (state.get("params") or {}).get("entity_names") or []:
                name = name.strip()
                if not name or has_glob(name):
                    continue
                if name not in reagents and entity_names.display_name(name) is None:
                    unknown.append(f"{_rel(path)}:{state.get('id')} → {name!r}")

    assert not unknown, f"collect_reagent names that are no entity's ID: {unknown}"


def test_no_bot_uses_the_retired_zone_list_node():
    """enter_zone_via_list is not a node any more (#36) -- Sync In Zone is.

    A list object only ever stands in a zone the navigation map declares, so there is nowhere
    it could be pointed that Sync In Zone cannot already plan a route to. The module survives as
    the step runner behind a planned list or gate step; what is gone is authoring one by hand,
    with a zone and a destination that had to be kept agreeing with each other and a row's text
    that was one locale's answer frozen into the file.

    A file naming it would fail to load as an unknown node type, which is a worse way to find
    out than this.
    """
    offenders = [
        f"{_rel(path)}:{state.get('id')}"
        for path in _all_bot_files()
        for state in _load_raw(path).get("states") or []
        if state.get("node_type") == "enter_zone_via_list"
    ]

    assert not offenders, f"bot states using the retired node: {offenders}"


def test_no_bot_declares_a_user_owned_option():
    """Some values are the user's whatever the dungeon is, and a declaration of one
    outranks the app setting they set it in.

    Safety Delay describes the connection, not the bot -- a declaration would put back
    the per-node pin the override-layer work removed, one level up. Enable Quicksell is whether the user
    will part with items at all; an author who did not want selling would leave the node
    out. Friend Name is a character name (see the test above).

    The Options tab shows none of the three, so a file carrying one was hand-edited.
    The sell flags are deliberately not here: a bot whose target drop is a pet has to be
    able to say so, and the keep list cannot spare an individual pet.
    """
    from bartlebot.bot_options import USER_OWNED_OPTIONS  # noqa: PLC0415

    offenders: list[str] = []
    for path in _all_bot_files():
        for opt in _load_raw(path).get("options") or []:
            if opt.get("name") in USER_OWNED_OPTIONS:
                offenders.append(f"{_rel(path)} → {opt['name']}")

    assert not offenders, f"bots declaring a value only the user owns: {offenders}"


def _guarded_option_names(raw: dict) -> set[str]:
    """Every option name some transition of *raw* reads, in either flag form.

    The grouped form is what the edge editor writes and what takes precedence at run
    time; the flat one is still what an older editor left behind.
    """
    names: set[str] = set()
    for trans in raw.get("transitions") or []:
        for flag in trans.get("flags") or []:
            names.add(flag.get("option"))
        for group in trans.get("flag_groups") or []:
            for flag in group.get("flags") or []:
                names.add(flag.get("option"))
    return names


def test_no_bot_stores_a_declaration_that_says_nothing():
    """A derived option's declaration belongs in the file only when it *differs* from
    what the NodeSpec composes -- otherwise it is a copy that goes stale the moment the
    node's text or default is improved, which is how one description fix left 80 files
    behind it.

    ``BotConfig.save`` prunes these, so this is the guard against an editor running an
    older build putting them back. It overlaps
    ``test_opening_and_saving_a_bot_changes_nothing_in_the_file`` on purpose: that one
    asserts a round trip moves nothing and would fail for any of a dozen reasons, and
    this one says which reason.
    """
    offenders: list[str] = []
    for path in _all_bot_files():
        config = BotConfig.load(path)
        kept = {opt.name for opt in storable_options(config)}
        for opt in config.options:
            if opt.name not in kept:
                offenders.append(f"{_rel(path)} → {opt.name}")

    assert not offenders, f"declarations a save would drop as saying nothing: {offenders}"


def test_no_bot_declares_an_option_nothing_reads():
    """The mirror of ``test_no_bot_guards_a_transition_on_an_option_nothing_declares``.

    An option a node requires is read by that node. An option the *author* hand-added
    has no node behind it, so a transition guarding on it is the only thing that can
    ever read it -- without one it is a switch wired to nothing, and the Option Values surface
    offers it to the user as though it did something. ``graveyard.json`` carried two
    copied out of ``castle_darkmoor.json`` along with a graph that has no Observatory
    and no treasure chest in it.
    """
    offenders: list[str] = []
    for path in _all_bot_files():
        config = BotConfig.load(path)
        required = set(required_option_specs(config))
        guarded = _guarded_option_names(_load_raw(path))
        for opt in config.options:
            if opt.name not in required and opt.name not in guarded:
                offenders.append(f"{_rel(path)} → {opt.name}")

    assert not offenders, f"declared options no node requires and no transition reads: {offenders}"


def test_no_bot_declares_an_option_borrowed_from_a_node_it_does_not_run():
    """A hand-added option whose name collides with one *any* node declares is deleted
    from the file on the next save, silently.

    ``compose_options`` and ``storable_options`` both treat every name in
    ``system_option_names()`` as derived, so such an option is filed as dead weight
    rather than as author intent -- and it is the file that is the only copy. Inherited
    from ``_inject_required_options``, but sharper now that the prune runs on every
    save.

    ``test_no_bot_stores_a_declaration_that_says_nothing`` catches the symptom -- a
    borrowed option is dropped like any other -- but names the wrong cause, and the
    fix it implies (delete it, the save would anyway) is the opposite of the right
    one. This says the option is real and its *name* is the problem.
    """
    offenders: list[str] = []
    system = system_option_names()
    for path in _all_bot_files():
        config = BotConfig.load(path)
        required = set(required_option_specs(config))
        for opt in config.options:
            if opt.name in system and opt.name not in required:
                offenders.append(f"{_rel(path)} → {opt.name}")

    assert not offenders, f"declarations borrowed from a node the bot does not run: {offenders}"


def test_no_bot_pins_a_bot_wide_option_for_one_node():
    """`option_overrides` is gone from the schema, and Pydantic ignores what it does not
    know — so a file still carrying one would be silently dropped on the next save rather
    than failing, taking a real setting with it.

    Every cause behind the pins the library had now has a param of its own: has_dialogue
    for an interaction with nothing to say, animation_delay for a cutscene, object_use_delay for
    a slow shared object. See scripts/migrate_option_overrides.py.
    """
    offenders: list[str] = []
    for path in _all_bot_files():
        for state in _load_raw(path).get("states") or []:
            if "option_overrides" in state:
                offenders.append(f"{_rel(path)}:{state.get('id')}")

    assert not offenders, f"bot states with per-node option overrides: {offenders}"


def test_no_bot_uses_legacy_combat_params():
    """`use_default_duel_profile` is honored by the Combat tab's fallback but not by
    handle_combat, so a bot still carrying it silently ignores its per-state strategy
    assignments at runtime."""
    offenders: list[str] = []
    for path in _all_bot_files():
        for state in _load_raw(path).get("states") or []:
            params = state.get("params")
            if not isinstance(params, dict):
                continue
            for legacy in ("use_default_duel_profile", "duel_profile_name"):
                if legacy in params:
                    offenders.append(f"{_rel(path)}:{state.get('id')} → {legacy}")

    assert not offenders, f"bot states with legacy combat params: {offenders}"


def _shipped_bot_files() -> list[Path]:
    """Every bot in the library proper — test_*.json are scratch files."""
    return [p for p in _all_bot_files() if not p.name.startswith("test_")]


def test_every_shipped_bot_has_at_least_one_tag():
    """An untagged bot is invisible to the library panel's chip filter.

    Tags say what a bot is *for*, and they cannot be derived — every boss drops
    jewels, so a rule keyed on drops[].category would tag everything with everything.
    The table lives in scripts/migrate_bot_tags.py; this is what keeps it applied
    when a bot is added.

    ``subgraphs/`` is out, because the vocabulary has nothing honest to say about a
    building block: every tag names a reward a run yields or an activity it does, and
    a subgraph is neither — it is a fragment of one. Its folder is how it is found,
    which is the same reason the "Type" group was dropped.
    """
    untagged = [_rel(p) for p in _shipped_bot_files() if not _load_raw(p).get("tags") and p.parent.name != "subgraphs"]

    assert not untagged, f"bot files with no tags: {untagged}"


def test_no_bot_carries_a_tag_outside_the_vocabulary():
    """BotConfig silently drops unknown tags on load, so a typo would only ever show
    up as a bot missing from a filter. Caught here instead, against the raw JSON."""
    from bartlebot.bot_tags import BOT_TAGS  # noqa: PLC0415

    offenders: list[str] = []
    for path in _shipped_bot_files():
        for tag in _load_raw(path).get("tags") or []:
            if tag not in BOT_TAGS:
                offenders.append(f"{_rel(path)} → {tag!r}")

    assert not offenders, f"bots carrying unknown tags: {offenders}"
