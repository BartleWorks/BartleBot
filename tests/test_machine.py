"""Tests for BotMachine FSM logic.

Nodes are replaced with simple async mock functions so no Wizard101 client is needed.
All tests use the same two-state config: state_a <-> state_b.

Transitions use the ``conditions`` key with ``_result_is_<VALUE>`` names — this is
exactly what ``BotConfig.to_machine_config()`` produces from a ``when`` field, so
these tests exercise the same path real bots use at runtime.
"""

import asyncio
import pytest

from bartlebot.bot.machine import BotMachine


# ---------------------------------------------------------------------------
# Shared config and helpers
# ---------------------------------------------------------------------------

TWO_STATE_CONFIG = {
    "initial": "state_a",
    "states": [
        {"id": "state_a", "node_type": "mock", "params": {}},
        {"id": "state_b", "node_type": "mock", "params": {}},
    ],
    "transitions": [
        # On success: advance to the other state.
        {"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_b", "dest": "state_a", "conditions": ["_result_is_SUCCESS"]},
        # On failure: retry the same state.
        {"trigger": "advance", "source": "state_a", "dest": "state_a"},
        {"trigger": "advance", "source": "state_b", "dest": "state_b"},
    ],
}


def make_machine(node_fn, on_state_change=None, on_transition=None):
    registry = {"mock": node_fn}
    return BotMachine(
        clients=[],
        config=TWO_STATE_CONFIG,
        node_registry=registry,
        on_state_change=on_state_change,
        on_transition=on_transition,
    )


# ---------------------------------------------------------------------------
# Dynamic _result_is_* condition methods
# ---------------------------------------------------------------------------

def test_result_is_success_true():
    machine = make_machine(lambda *_: None)
    machine.last_result = "SUCCESS"
    assert machine._result_is_SUCCESS() is True


def test_result_is_success_false_on_failure():
    machine = make_machine(lambda *_: None)
    machine.last_result = "FAILURE"
    assert machine._result_is_SUCCESS() is False


def test_result_is_success_false_on_none():
    machine = make_machine(lambda *_: None)
    machine.last_result = None
    assert machine._result_is_SUCCESS() is False


def test_result_is_failure_true():
    machine = make_machine(lambda *_: None)
    machine.last_result = "FAILURE"
    assert machine._result_is_FAILURE() is True


def test_result_is_unknown_raises():
    machine = make_machine(lambda *_: None)
    with pytest.raises(AttributeError):
        _ = machine.no_such_method


# ---------------------------------------------------------------------------
# FSM transitions
# ---------------------------------------------------------------------------

async def test_machine_advances_on_success():
    """SUCCESS result transitions state_a → state_b → state_a → ..."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 4:
            machine.stop()
        return "SUCCESS"

    await make_machine(node).run()
    assert visited == ["state_a", "state_b", "state_a", "state_b"]


async def test_machine_retries_on_failure():
    """FAILURE result keeps the machine in the same state (self-loop)."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 3:
            machine.stop()
        return "FAILURE"

    machine = make_machine(node)
    await machine.run()
    assert all(s == "state_a" for s in visited)


async def test_machine_transitions_after_failure_then_success():
    """FAILURE → FAILURE → SUCCESS should eventually advance to state_b."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if machine.state == "state_b":
            machine.stop()
            return "SUCCESS"
        # First visit to state_a: fail; second visit: succeed
        return "FAILURE" if visited.count("state_a") == 1 else "SUCCESS"

    machine = make_machine(node)
    await machine.run()
    assert visited == ["state_a", "state_a", "state_b"]
    assert machine.state == "state_b"


# ---------------------------------------------------------------------------
# Pause / resume behaviour
# ---------------------------------------------------------------------------

async def test_is_paused_reflects_state():
    """is_paused returns True after pause() and False after resume()."""
    machine = make_machine(lambda *_: None)
    assert not machine.is_paused
    machine.pause()
    assert machine.is_paused
    machine.resume()
    assert not machine.is_paused


async def test_pause_before_run_suspends_loop():
    """pause() called before run() blocks the loop until resume() is called."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        machine.stop()
        return "SUCCESS"

    machine = make_machine(node)
    machine.pause()

    run_task = asyncio.create_task(machine.run())
    await asyncio.sleep(0)  # yield so the task can start

    assert visited == [], "node should not execute while paused"
    machine.resume()
    await run_task
    assert visited == ["state_a"]


async def test_pause_halts_after_current_node():
    """pause() during a node prevents the next iteration from starting."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] == 1:
            machine.pause()
        return "SUCCESS"

    machine = make_machine(node)
    run_task = asyncio.create_task(machine.run())
    await asyncio.sleep(0.05)  # give the loop time to pause

    assert machine.is_paused
    assert calls[0] == 1
    machine.stop()
    machine.resume()
    await run_task


async def test_stop_while_paused_exits_cleanly():
    """stop() while paused causes run() to return without hanging."""
    async def node(machine, clients, params):
        return "SUCCESS"

    machine = make_machine(node)
    machine.pause()
    run_task = asyncio.create_task(machine.run())
    await asyncio.sleep(0)  # let task block on resume_event

    machine.stop()
    await asyncio.wait_for(run_task, timeout=1.0)


# ---------------------------------------------------------------------------
# Stop behaviour
# ---------------------------------------------------------------------------

async def test_stop_halts_after_current_node():
    """stop() set during a node prevents the next advance."""
    async def node(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    machine = make_machine(node)
    await machine.run()  # Should return without hanging
    assert machine.state == "state_a"  # advance was skipped


async def test_stop_before_run_exits_immediately():
    """If stop() is called before run(), the loop should not execute any node."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        return "SUCCESS"

    machine = make_machine(node)
    machine.stop()
    await machine.run()
    assert visited == []


async def test_run_can_be_stopped_externally():
    """stop() called from outside (simulating a button press) halts the loop."""
    started = asyncio.Event()

    async def blocking_node(machine, clients, params):
        started.set()
        await asyncio.sleep(10)  # Simulate long-running node
        return "SUCCESS"

    machine = make_machine(blocking_node)
    task = asyncio.create_task(machine.run())
    await started.wait()
    machine.stop()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# on_state_change callback
# ---------------------------------------------------------------------------

async def test_on_state_change_called_for_each_state():
    changes = []

    async def node(machine, clients, params):
        if len(changes) >= 3:
            machine.stop()
        return "SUCCESS"

    await make_machine(node, on_state_change=changes.append).run()
    assert changes[:2] == ["state_a", "state_b"]


# ---------------------------------------------------------------------------
# on_transition callback
# ---------------------------------------------------------------------------

async def test_on_transition_args_on_success():
    """on_transition receives (prev, 'SUCCESS', next) for each successful advance."""
    log = []
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 3:
            machine.stop()
        return "SUCCESS"

    await make_machine(node, on_transition=lambda p, r, n: log.append((p, r, n))).run()
    # Third call sets stop before advance, so only two transitions are logged.
    assert log == [
        ("state_a", "SUCCESS", "state_b"),
        ("state_b", "SUCCESS", "state_a"),
    ]


async def test_on_transition_args_on_failure_retry():
    """On FAILURE, on_transition is called with prev_state == next_state (self-loop)."""
    log = []
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 3:
            machine.stop()
        return "FAILURE"

    await make_machine(node, on_transition=lambda p, r, n: log.append((p, r, n))).run()
    assert log == [
        ("state_a", "FAILURE", "state_a"),
        ("state_a", "FAILURE", "state_a"),
    ]


async def test_on_transition_not_called_when_stop_set():
    """on_transition is skipped entirely when stop() is called inside the node."""
    called = []

    async def node(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    await make_machine(node, on_transition=lambda *a: called.append(a)).run()
    assert called == []


async def test_on_transition_receives_failure_after_node_exception():
    """An unhandled node exception is surfaced to on_transition as FAILURE."""
    log = []
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("crash")
        machine.stop()
        return "SUCCESS"

    await make_machine(node, on_transition=lambda p, r, n: log.append((p, r, n))).run()
    # Exception on first call → FAILURE self-loop; second call stops before advance.
    assert log == [("state_a", "FAILURE", "state_a")]


# ---------------------------------------------------------------------------
# Node exception handling
# ---------------------------------------------------------------------------

async def test_node_exception_results_in_failure():
    """An unhandled exception in a node should be treated as FAILURE."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 2:
            machine.stop()
            return "SUCCESS"
        raise RuntimeError("Simulated node crash")

    machine = make_machine(node)
    await machine.run()
    # First call raised → FAILURE → self-loop back to state_a
    # Second call returned SUCCESS → would advance, but stop was set first
    assert calls[0] == 2
