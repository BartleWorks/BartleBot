"""Tests for the ambient combat watcher cancelling the running node (#116, Stage 2).

Pausing stops the run loop only at the next node boundary, so a node could go on moving a
client the watcher is fighting with. The watcher cancels it instead, and the loop runs that
state again from the start once the fight is over: no result, no transition, no counters.

Nodes are async stand-ins; no Wizard101 client is involved.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bartlebot.bot.machine import BotMachine
from bartlebot.nodes import combat_runner, node_helpers
from tests.conftest import FakeClient


def _config(node_type: str = "work") -> dict:
    return {
        "initial": "a",
        "states": [
            {"id": "a", "node_type": node_type, "params": {}},
            {"id": "b", "node_type": "done", "params": {}},
        ],
        "transitions": [
            {"trigger": "advance", "source": "a", "dest": "b", "conditions": []},
            {"trigger": "advance", "source": "b", "dest": "b", "conditions": []},
        ],
    }


class _Node:
    """A node that blocks on its first run until released, and returns at once after that."""

    def __init__(self):
        self.runs = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = 0

    async def __call__(self, *_args):
        self.runs += 1
        if self.runs == 1:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return "SUCCESS"


async def _done_node(*_args):
    await asyncio.sleep(0.001)
    return "SUCCESS"


def _machine(node, node_type: str = "work", clients=None) -> tuple[BotMachine, list]:
    transitions: list[tuple[str, str, str]] = []
    machine = BotMachine(
        clients=clients or [],
        config=_config(node_type),
        node_registry={node_type: node, "done": _done_node},
        stop_grace_period=0.1,
        on_transition=lambda src, result, dst: transitions.append((src, result, dst)),
    )
    return machine, transitions


async def _until(predicate, timeout: float = 2.0) -> None:
    async def _wait():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(_wait(), timeout)


async def _finish(machine: BotMachine, task: asyncio.Task) -> None:
    machine.stop()
    await asyncio.wait_for(task, timeout=2.0)


async def test_a_cancelled_node_runs_again_in_the_same_state_with_nothing_counted():
    node = _Node()
    machine, transitions = _machine(node)
    task = asyncio.create_task(machine.run())
    await node.started.wait()

    machine.pause()
    await machine._preempt_node()

    assert node.cancelled == 1
    assert await machine.wait_until_parked(1.0) is True
    assert machine.state == "a"
    assert machine.last_result is None
    assert transitions == []

    machine.resume()
    await _until(lambda: machine.state == "b")
    await _finish(machine, task)

    assert node.runs == 2
    assert transitions[0] == ("a", "SUCCESS", "b")


async def test_a_node_that_finishes_before_the_cancel_lands_is_not_run_again():
    """It swallowed the cancel and returned: the FSM has moved on, and the next state is right."""

    class _Stubborn(_Node):
        async def __call__(self, *_args):
            self.runs += 1
            self.started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return "SUCCESS"
            return "SUCCESS"

    node = _Stubborn()
    machine, transitions = _machine(node)
    task = asyncio.create_task(machine.run())
    await node.started.wait()

    machine.pause()
    await machine._preempt_node()
    assert machine.state == "b"

    machine.resume()
    await _until(lambda: len(transitions) >= 2)
    await _finish(machine, task)

    assert node.runs == 1
    assert transitions[:2] == [("a", "SUCCESS", "b"), ("b", "SUCCESS", "b")]


async def test_a_stop_during_the_cancel_still_stops_the_run():
    node = _Node()
    machine, _transitions = _machine(node)
    task = asyncio.create_task(machine.run())
    await node.started.wait()

    machine.pause()
    preempt = asyncio.create_task(machine._preempt_node())
    await asyncio.sleep(0)  # the cancel is issued
    machine.stop()
    await asyncio.wait_for(task, timeout=2.0)
    await asyncio.wait_for(preempt, timeout=2.0)

    assert node.runs == 1


@pytest.mark.parametrize("node_type", ["relog", "handle_combat"])
async def test_relog_and_handle_combat_are_never_cancelled(node_type):
    node = _Node()
    machine, transitions = _machine(node, node_type)
    task = asyncio.create_task(machine.run())
    await node.started.wait()

    machine.pause()
    await machine._preempt_node()
    assert node.cancelled == 0

    node.release.set()
    assert await machine.wait_until_parked(1.0) is True
    machine.resume()
    await _until(lambda: machine.state == "b")
    await _finish(machine, task)

    assert node.runs == 1
    assert transitions[0] == ("a", "SUCCESS", "b")


async def test_an_ambient_fight_cancels_the_node_then_runs_it_again(monkeypatch):
    """End to end: the watcher run() starts takes the fight mid-node."""
    monkeypatch.setattr(node_helpers, "POLL_INTERVAL", 0.001)
    fights: list[tuple[int, bool]] = []
    node = _Node()
    battling = FakeClient()
    machine, transitions = _machine(node, clients=[battling])

    async def fight(client, _dsl, _watch):
        fights.append((node.runs, machine.is_paused))
        client.in_battle.return_value = False

    monkeypatch.setattr(combat_runner, "resolve_combat", MagicMock(return_value=(["dsl"], [], "test")))
    monkeypatch.setattr(combat_runner, "seed_chat_baselines", AsyncMock())
    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    monkeypatch.setattr(combat_runner, "poll_combat_drops", AsyncMock(return_value=[]))

    task = asyncio.create_task(machine.run())
    await node.started.wait()
    battling.in_battle.return_value = True
    await _until(lambda: machine.state == "b")
    await _finish(machine, task)

    assert node.cancelled == 1
    assert fights == [(1, True)]
    assert node.runs == 2
    assert transitions[0] == ("a", "SUCCESS", "b")
