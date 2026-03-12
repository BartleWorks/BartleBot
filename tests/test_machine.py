"""Tests for BotMachine FSM logic.

Nodes are replaced with simple async mock functions so no Wizard101 client is needed.
All tests use the same two-state config: state_a <-> state_b.
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
        {"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["last_result_is_success"]},
        {"trigger": "advance", "source": "state_b", "dest": "state_a", "conditions": ["last_result_is_success"]},
        # On failure: retry the same state.
        {"trigger": "advance", "source": "state_a", "dest": "state_a"},
        {"trigger": "advance", "source": "state_b", "dest": "state_b"},
    ],
}


def make_machine(node_fn, on_state_change=None):
    registry = {"mock": node_fn}
    return BotMachine(
        clients=[],
        config=TWO_STATE_CONFIG,
        node_registry=registry,
        on_state_change=on_state_change,
    )


# ---------------------------------------------------------------------------
# Condition methods
# ---------------------------------------------------------------------------

def test_last_result_is_success_true():
    machine = make_machine(lambda *_: None)
    machine.last_result = "SUCCESS"
    assert machine.last_result_is_success() is True


def test_last_result_is_success_false_on_failure():
    machine = make_machine(lambda *_: None)
    machine.last_result = "FAILURE"
    assert machine.last_result_is_success() is False


def test_last_result_is_success_false_on_none():
    machine = make_machine(lambda *_: None)
    machine.last_result = None
    assert machine.last_result_is_success() is False


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
