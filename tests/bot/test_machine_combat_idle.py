"""Tests for BotMachine's idle-popup watcher (#203).

The game raises "You Are Idle" (``WorldView/CombatAFKWindow``) once a player has let two
planning phases run out without acting, and passes its turn while the popup is up. The watcher
clicks Flee and sends the bot to its recovery state, cancelling whatever node is running, unless
that node is a relog (the recovery already) or an ambient combat session owns the fight (it
counts and recovers from the flee itself).

The popup and its button are fakes behind node_helpers; no Wizard101 client is involved.
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from bartlebot.bot.machine import BotMachine
from bartlebot.nodes import node_helpers, timing
from bartlebot.nodes.paths import COMBAT_AFK_FLEE_PATH, COMBAT_AFK_WINDOW_PATH
from tests.conftest import FakeClient


def _config(node_type: str) -> dict:
    return {
        "initial": "a",
        "recovery_state": "recover",
        "states": [
            {"id": "a", "node_type": node_type, "params": {}},
            {"id": "recover", "node_type": "recover", "params": {}},
        ],
        "transitions": [
            {"trigger": "advance", "source": "a", "dest": "a", "conditions": []},
            {"trigger": "advance", "source": "recover", "dest": "recover", "conditions": []},
        ],
    }


class _Node:
    """Blocks on its first run until released or cancelled; returns at once after that."""

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
        await asyncio.sleep(0.001)
        return "SUCCESS"


class _Popups:
    """Which clients show "You Are Idle", and every Flee clicked on it."""

    def __init__(self):
        self.up: set = set()
        self.fled: list = []

    async def is_visible(self, client, path):
        return list(path) == COMBAT_AFK_WINDOW_PATH and client in self.up

    async def click(self, client, path):
        if list(path) == COMBAT_AFK_FLEE_PATH and client in self.up:
            self.up.discard(client)
            self.fled.append(client)
            return True
        return False


@pytest.fixture
def popups(monkeypatch) -> _Popups:
    fake = _Popups()
    monkeypatch.setattr(node_helpers, "is_visible_by_path", fake.is_visible)
    monkeypatch.setattr(node_helpers, "click_by_path", fake.click)
    monkeypatch.setattr(timing, "COMBAT_AFK_POLL_INTERVAL", 0.001)
    return fake


def _machine(node, node_type: str = "work", clients=None) -> tuple[BotMachine, _Node]:
    recover = _Node()
    machine = BotMachine(
        clients=clients if clients is not None else [FakeClient(), FakeClient()],
        config=_config(node_type),
        node_registry={node_type: node, "recover": recover},
        stop_grace_period=0.1,
    )
    return machine, recover


async def _until(predicate, timeout: float = 2.0) -> None:
    async def _wait():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(_wait(), timeout)


async def _finish(machine: BotMachine, task: asyncio.Task) -> None:
    machine.stop()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.parametrize("node_type", ["work", "handle_combat"])
async def test_the_popup_is_fled_and_the_running_node_gives_way_to_recovery(popups, node_type):
    """handle_combat too: its fight was just fled, and left alone it would report it as won."""
    node = _Node()
    machine, recover = _machine(node, node_type)
    idle, busy = machine.clients
    task = asyncio.create_task(machine.run())
    await node.started.wait()

    popups.up.add(idle)
    await recover.started.wait()
    await _finish(machine, task)

    assert popups.fled == [idle]
    assert busy not in popups.fled
    assert node.cancelled == 1
    assert machine.state == "recover"


async def test_an_idle_flee_counts_toward_the_flee_limit(popups):
    node = _Node()
    machine, _recover = _machine(node)
    now = time.monotonic()
    machine._flees = [now, now]  # two flees already in the window: this one is the third
    task = asyncio.create_task(machine.run())
    await node.started.wait()

    popups.up.add(machine.clients[0])
    await asyncio.wait_for(task, timeout=2.0)

    assert machine.is_stopped
    assert popups.fled == [machine.clients[0]]


async def test_a_relog_under_way_is_left_to_finish(popups):
    node = _Node()
    machine, recover = _machine(node, "relog")
    task = asyncio.create_task(machine.run())
    await node.started.wait()

    popups.up.add(machine.clients[0])
    await _until(lambda: popups.fled)
    node.release.set()
    await _until(lambda: node.runs >= 2)
    await _finish(machine, task)

    assert node.cancelled == 0
    assert recover.runs == 0
    assert machine.state == "a"


async def test_a_pause_the_user_asked_for_is_left_alone(popups):
    node = _Node()
    machine, _recover = _machine(node)
    machine.pause()
    popups.up.add(machine.clients[0])

    watcher = asyncio.create_task(machine._combat_idle_watcher())
    await asyncio.sleep(0.05)
    machine.stop()
    await asyncio.wait_for(watcher, timeout=1.0)

    assert popups.fled == []


async def test_a_flee_in_an_ambient_session_is_left_to_the_session(popups):
    node = _Node()
    machine, _recover = _machine(node)
    idle = machine.clients[0]
    session = MagicMock()
    machine._ambient_session = session
    machine._combat_auto_paused = True
    machine.pause()
    popups.up.add(idle)

    watcher = asyncio.create_task(machine._combat_idle_watcher())
    await _until(lambda: session.note_fled.called)
    machine.stop()
    await asyncio.wait_for(watcher, timeout=1.0)

    session.note_fled.assert_called_once_with(idle)
    assert machine._flees == []  # the session counts it, once its fight ends
    assert machine.state == "a"
