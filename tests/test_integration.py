"""Integration tests: exercises the seams between config, machine, and nodes.

These tests cover four integration boundaries that existing unit tests leave untested:

  1. Config pipeline: BotConfig.to_machine_config() produces the correct structure
     that BotMachine expects (condition strings, flag registry, round-trip save/load).
  2. BotMachine construction from real config: a real bot JSON file can be loaded,
     converted, and used to construct BotMachine without errors.
  3. Flag transitions through BotMachine: BotConfig-generated flag conditions
     (_flag_X, _flag_compound_X) are evaluated correctly by the live FSM.
  4. Real node execution through the FSM: enter_zone returns SUCCESS/FAILURE and
     the machine routes those results through real transitions from a real BotConfig.

What is NOT tested here (by design):
  - AppController / GUI: requires QApplication, high setup cost for wiring logic.
  - WizWalker / WizSprinter APIs: FakeClient stubs these out (same as node unit tests).
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.zone_entry.enter_zone as gtz
from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, BotOptionModel, FlagCondition, StateModel, TransitionModel
from bartlebot.nodes.registry import NODE_REGISTRY
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
    src = _BOTS_DIR / "04_mooshu" / "crimson_fields.json"
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
        name="test",
        initial="a",
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
        name="test",
        initial="a",
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
        name="test",
        initial="a",
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
    src = _BOTS_DIR / "04_mooshu" / "crimson_fields.json"
    config = BotConfig.load(src)
    mc = config.to_machine_config()

    machine = BotMachine(clients=[], config=mc, node_registry=NODE_REGISTRY, bot_config=config)

    assert machine.state == config.initial


async def test_option_override_per_state_visible_via_get_option():
    """Per-state option_overrides survive to_machine_config() and are read by BotMachine.

    Exercises: StateModel.option_overrides → to_machine_config() → BotMachine._state_map
    → BotMachine.get_option() priority (per-state override > bot-wide default).
    """
    config = BotConfig(
        name="test",
        initial="state_a",
        # Bot-wide default: Safety Delay = 5.0
        options=[BotOptionModel(name="Safety Delay", type="float", default=5.0)],
        states=[
            StateModel(
                id="state_a",
                node_type="mock",
                # Per-state override: Safety Delay = 0.0
                option_overrides={"Safety Delay": 0.0},
            ),
        ],
        transitions=[],  # terminal state — machine stops after one node execution
    )
    mc = config.to_machine_config()
    machine = BotMachine(
        clients=[],
        config=mc,
        node_registry={"mock": AsyncMock(return_value="SUCCESS")},
        bot_config=config,
    )

    # Machine starts in state_a; the per-state override should take priority
    assert machine.state == "state_a"
    assert machine.get_option("Safety Delay") == 0.0  # override, not bot-wide 5.0
    assert machine.get_option("Safety Delay", default=3.0) == 0.0  # still override


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
        name="flag-test",
        initial="state_a",
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


# ---------------------------------------------------------------------------
# Part 4: Real node execution through BotMachine
# ---------------------------------------------------------------------------

# Shared params for enter_zone in all real-node tests
_GZ_PARAMS = {"from_zone": "Src", "to_zone": "Dst", "xyz": {"x": 0.0, "y": 0.0, "z": 0.0}}

# Safety Delay = 0.0 so confirm_dungeon_warning_dialog returns immediately in tests
_SAFETY_DELAY_ZERO = BotOptionModel(name="Safety Delay", type="float", default=0.0)


def _make_enter_zone_gateway_client_success():
    """FakeClient that simulates a successful Src → Dst zone transition.

    zone_name sequence matches the enter_zone call pattern:
      call 1 – pre-condition check           → "Src"
      call 2 – poll_each_until_seen iter 1   → "Src" (still leaving; reteleport)
      call 3 – poll_each_until_seen iter 2   → "Dst" (left zone; seen)
      call 4 – wait_for_zone post-check      → "Dst" (in destination)
    """
    client = FakeClient()
    client.window_handle = 99999  # required by _client_status_watcher
    client.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]
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
        name="gz-success-test",
        initial="state_a",
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
        name="gz-failure-test",
        initial="state_a",
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
        name="stop-cooperative-test",
        initial="state_a",
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
        name="stop-forced-cancel-test",
        initial="state_a",
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
    real crimson_fields.json bot file.
    """
    src = _BOTS_DIR / "04_mooshu" / "crimson_fields.json"
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


async def test_stop_after_client_error_induced_pause():
    """stop() exits cleanly when the machine has self-paused after a ClientClosedError.

    When a node raises ClientClosedError the FSM catches it, calls pause(), and loops
    back to block on _resume_event.wait() (machine.py lines 252-270). stop() must
    unblock that wait and exit without resuming execution or advancing state.
    """
    from wizwalker.errors import ClientClosedError

    execution_started = asyncio.Event()

    async def error_node(machine, clients, params):
        execution_started.set()
        raise ClientClosedError("simulated client death")

    config = BotConfig(
        name="client-error-pause-test",
        initial="state_a",
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
    )

    task = asyncio.create_task(machine.run())
    await execution_started.wait()
    # Yield control so the exception handler can call pause() and re-enter the while loop.
    await asyncio.sleep(0.05)
    machine.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert machine.is_stopped
    assert machine.state == "state_a"
