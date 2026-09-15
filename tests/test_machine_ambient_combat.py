"""Tests for BotMachine's ambient combat watcher.

The watcher runs beside the run loop and takes over a fight no handle_combat node started
-- a mob that wandered into a client between nodes. What matters is that the FSM is paused
for exactly that fight and always resumed afterwards, and that each battling client is
given its own slot's strategy.

The combat runner is patched out: these are about the machine's bookkeeping, not about
SprintyCombat.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bartlebot.bot.machine import BotMachine
from bartlebot.nodes import combat_runner, node_helpers
from tests.conftest import FakeClient


def _config(initial: str = "roam") -> dict:
    return {
        "initial": initial,
        "states": [
            {"id": "roam", "node_type": "mock", "params": {}},
            {"id": "fight", "node_type": "handle_combat", "params": {}},
        ],
        "transitions": [{"trigger": "advance", "source": "roam", "dest": "roam"}],
    }


@pytest.fixture
def fast(monkeypatch):
    """Compress the watcher's poll cadence. A cadence, not a ceiling: the fakes answer at once."""
    monkeypatch.setattr(node_helpers, "POLL_INTERVAL", 0.001)


@pytest.fixture
def combat(monkeypatch):
    """The combat runner's entry points, patched where the watcher looks them up.

    The watcher imports them from the module when it starts, so the watcher task has to be
    created after this fixture has run -- which a test body always is.
    """
    mocks = SimpleNamespace(
        resolve=MagicMock(return_value=(["dsl-0", "dsl-1"], [], "test strategy")),
        seed=AsyncMock(),
        fight=AsyncMock(),
        poll_drops=AsyncMock(return_value=["Wand"]),
    )
    monkeypatch.setattr(combat_runner, "resolve_combat", mocks.resolve)
    monkeypatch.setattr(combat_runner, "seed_chat_baselines", mocks.seed)
    monkeypatch.setattr(combat_runner, "_run_combat", mocks.fight)
    monkeypatch.setattr(combat_runner, "poll_combat_drops", mocks.poll_drops)
    return mocks


async def _until(predicate, timeout: float = 2.0) -> None:
    async def _wait():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(_wait(), timeout)


async def _stop(machine: BotMachine, task: asyncio.Task) -> None:
    """Stop the machine and wait for the watcher to finish the iteration it is in."""
    machine.stop()
    await asyncio.wait_for(task, timeout=2.0)


async def test_a_surprise_fight_pauses_the_fsm_until_it_is_over(fast, combat):
    idle, battling = FakeClient(), FakeClient()
    battling.in_battle.return_value = True
    machine = BotMachine(clients=[idle, battling], config=_config(), node_registry={})
    during: list[tuple] = []

    async def fight(client, dsl):
        during.append((client, dsl, machine.is_paused, machine.is_ambient_combat_active))
        client.in_battle.return_value = False

    combat.fight.side_effect = fight
    task = asyncio.create_task(machine._ambient_combat_watcher())
    await _until(lambda: combat.poll_drops.await_count == 1 and not machine.is_paused)
    await _stop(machine, task)

    assert during == [(battling, "dsl-1", True, True)]
    assert machine.is_ambient_combat_active is False


async def test_the_strategy_is_resolved_per_slot_and_drops_are_credited_to_it(fast, combat):
    """Resolving for only the battling clients would hand slot 1 the strategy for slot 0."""
    idle, battling = FakeClient(), FakeClient()
    battling.in_battle.return_value = True
    machine = BotMachine(clients=[idle, battling], config=_config(), node_registry={})

    async def fight(client, _dsl):
        client.in_battle.return_value = False

    combat.fight.side_effect = fight
    task = asyncio.create_task(machine._ambient_combat_watcher())
    await _until(lambda: combat.poll_drops.await_count == 1)
    await _stop(machine, task)

    combat.resolve.assert_called_once_with(machine, use_default_strategy=True, num_clients=2)
    combat.seed.assert_awaited_once_with(machine, [battling])
    combat.poll_drops.assert_awaited_once_with(machine, [battling], [1])


async def test_a_fight_that_raises_on_one_client_still_collects_drops_and_resumes(fast, combat):
    battling = FakeClient()
    battling.in_battle.return_value = True
    machine = BotMachine(clients=[battling], config=_config(), node_registry={})

    async def fight(client, _dsl):
        client.in_battle.return_value = False
        raise RuntimeError("SprintyCombat lost the duel window")

    combat.fight.side_effect = fight
    task = asyncio.create_task(machine._ambient_combat_watcher())
    await _until(lambda: combat.poll_drops.await_count == 1 and not machine.is_paused)
    await _stop(machine, task)

    combat.poll_drops.assert_awaited_once_with(machine, [battling], [0])


async def test_the_fsm_is_resumed_even_when_handling_the_fight_raises(fast, combat):
    """The resume is in a finally. Without it, one unreadable strategy leaves the run paused for good."""
    battling = FakeClient()
    battling.in_battle.return_value = True
    combat.resolve.side_effect = RuntimeError("strategy file unreadable")
    machine = BotMachine(clients=[battling], config=_config(), node_registry={})

    task = asyncio.create_task(machine._ambient_combat_watcher())
    # resolve_combat runs after the pause, and the finally resumes before anything yields,
    # so by the time the call is observable the FSM must already be running again.
    await _until(lambda: combat.resolve.called and not machine.is_paused)

    machine.stop()
    await asyncio.wait({task}, timeout=2.0)
    if task.done() and not task.cancelled():
        task.exception()  # retrieved, so asyncio does not warn about it at teardown


async def test_no_fight_is_taken_over_while_the_fsm_is_in_handle_combat(fast, combat):
    """handle_combat is already fighting it; a second SprintyCombat would contend for the same cards."""
    battling = FakeClient()
    battling.in_battle.return_value = True
    machine = BotMachine(clients=[battling], config=_config(initial="fight"), node_registry={})
    machine.pause = MagicMock(wraps=machine.pause)

    task = asyncio.create_task(machine._ambient_combat_watcher())
    await asyncio.sleep(0.05)
    await _stop(machine, task)

    combat.resolve.assert_not_called()
    combat.fight.assert_not_awaited()
    machine.pause.assert_not_called()


async def test_a_fight_the_fsm_reaches_first_is_left_to_handle_combat(fast, combat):
    """The in_battle reads are an await, and the FSM can enter handle_combat while they are out.

    The watcher re-checks the state once it holds the lock, so it neither pauses the FSM nor
    starts a second fight.
    """
    battling = FakeClient()
    machine = BotMachine(clients=[battling], config=_config(), node_registry={})
    machine.pause = MagicMock(wraps=machine.pause)

    async def in_battle():
        machine.force_state("fight")
        return True

    battling.in_battle.side_effect = in_battle
    task = asyncio.create_task(machine._ambient_combat_watcher())
    await _until(lambda: battling.in_battle.await_count >= 1)
    await _stop(machine, task)

    assert machine.state == "fight"
    combat.resolve.assert_not_called()
    machine.pause.assert_not_called()
