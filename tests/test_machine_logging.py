"""Tests for what BotMachine writes to the log.

The subject here is attribution and landmarks, not FSM behavior: which logger a
node's own messages are filed under, and the pass/summary lines that give a
five-hour log something to navigate by. Both are invisible to every other test —
a run can be perfectly correct and still produce a log nobody can read.
"""

import logging

import pytest

from bartlebot.bot.machine import BotMachine

TWO_STATE_CONFIG = {
    "initial": "state_a",
    "states": [
        {"id": "state_a", "node_type": "walk", "params": {}},
        {"id": "state_b", "node_type": "fight", "params": {}},
    ],
    "transitions": [
        {"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_b", "dest": "state_a", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_a", "dest": "state_a"},
        {"trigger": "advance", "source": "state_b", "dest": "state_b"},
    ],
}


def _machine(walk, fight):
    return BotMachine(clients=[], config=TWO_STATE_CONFIG, node_registry={"walk": walk, "fight": fight})


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def _from(logs, name: str) -> list[str]:
    return [r.getMessage() for r in logs.records if r.name == name]


def _messages(logs) -> list[str]:
    return [r.getMessage() for r in logs.records]


# ---------------------------------------------------------------------------
# Per-node attribution
# ---------------------------------------------------------------------------


async def test_a_nodes_own_log_is_filed_under_that_node(logs):
    """Every node used to log as bartlebot.bot.machine — four fifths of a session."""

    async def walk(machine, clients, params):
        machine.log("walking")
        machine.stop()
        return "SUCCESS"

    async def fight(machine, clients, params):
        machine.log("fighting")
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert "walking" in _from(logs, "bartlebot.node.walk")
    assert "walking" not in _from(logs, "bartlebot.bot.machine")


async def test_two_nodes_are_two_loggers(logs):
    """What makes "everything the fight node did" a grep rather than a text search."""
    seen = []

    async def walk(machine, clients, params):
        seen.append("walk")
        machine.log("walking")
        return "SUCCESS"

    async def fight(machine, clients, params):
        machine.log("fighting")
        machine.stop()
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert _from(logs, "bartlebot.node.walk") == ["walking"]
    assert _from(logs, "bartlebot.node.fight") == ["fighting"]


async def test_the_machines_own_bookkeeping_stays_on_the_machine_logger(logs):
    """A transition is the machine talking about the graph, not any one node."""

    async def walk(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    async def fight(machine, clients, params):
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert any("=== pass 1 ===" in m for m in _from(logs, "bartlebot.bot.machine"))


async def test_attribution_is_cleared_once_the_node_returns():
    """A log() from outside a node has no node to name, and must not borrow the last one."""

    async def walk(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    async def fight(machine, clients, params):
        return "SUCCESS"

    machine = _machine(walk, fight)
    await machine.run()

    machine.log("after the run")
    assert machine._node_logger_name() == "bartlebot.bot.machine"


async def test_a_node_that_raises_still_releases_attribution(logs):
    """The finally clause matters: an exception must not pin the logger to that node."""

    async def walk(machine, clients, params):
        machine.stop()
        raise RuntimeError("boom")

    async def fight(machine, clients, params):
        return "SUCCESS"

    machine = _machine(walk, fight)
    await machine.run()

    assert machine._node_logger_name() == "bartlebot.bot.machine"


# ---------------------------------------------------------------------------
# Pass landmarks
# ---------------------------------------------------------------------------


async def test_each_return_to_the_first_state_is_numbered(logs):
    calls = [0]

    async def walk(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 3:
            machine.stop()
        return "SUCCESS"

    async def fight(machine, clients, params):
        return "SUCCESS"

    await _machine(walk, fight).run()

    passes = [m for m in _messages(logs) if m.startswith("=== pass")]
    assert passes == ["=== pass 1 ===", "=== pass 2 ===", "=== pass 3 ==="]


async def test_a_state_that_is_not_the_first_does_not_start_a_pass(logs):
    """Otherwise every state in the loop would claim to be the start of one."""

    async def walk(machine, clients, params):
        return "SUCCESS"

    async def fight(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert [m for m in _messages(logs) if m.startswith("=== pass")] == ["=== pass 1 ==="]


async def test_a_self_loop_on_the_first_state_still_counts_as_a_pass(logs):
    """A retry re-enters the first state, and the marker is about re-entry.

    Worth stating rather than assuming: a bot stuck retrying its first state
    produces rising pass numbers with no progress behind them, which is a
    recognizable shape in a log rather than a miscount.
    """
    calls = [0]

    async def walk(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 3:
            machine.stop()
        return "FAILURE"

    async def fight(machine, clients, params):
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert [m for m in _messages(logs) if m.startswith("=== pass")] == [
        "=== pass 1 ===",
        "=== pass 2 ===",
        "=== pass 3 ===",
    ]


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------


async def test_the_run_ends_with_a_summary(logs):
    async def walk(machine, clients, params):
        return "SUCCESS"

    async def fight(machine, clients, params):
        machine.stop()
        return "FAILURE"

    await _machine(walk, fight).run()

    messages = _messages(logs)
    assert any(m.startswith("=== run finished in ") for m in messages)
    assert "Passes: 1   States executed: 2" in messages
    assert "Results: FAILURE=1, SUCCESS=1" in messages


async def test_the_summary_is_written_even_when_a_node_raises(logs):
    """A run that fell over is the one you most want the summary for."""

    async def walk(machine, clients, params):
        machine.stop()
        raise RuntimeError("boom")

    async def fight(machine, clients, params):
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert any(m.startswith("=== run finished in ") for m in _messages(logs))
    assert "Results: FAILURE=1" in _messages(logs)


async def test_drops_are_counted_in_the_summary(logs):
    async def walk(machine, clients, params):
        machine.on_drop("Amber", 2)
        machine.on_drop("Sunstone", 1)
        machine.stop()
        return "SUCCESS"

    async def fight(machine, clients, params):
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert "Drops: 3 item(s) over 2 entries" in _messages(logs)


async def test_a_run_with_no_drops_says_nothing_about_them(logs):
    async def walk(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    async def fight(machine, clients, params):
        return "SUCCESS"

    await _machine(walk, fight).run()

    assert not [m for m in _messages(logs) if m.startswith("Drops:")]
