"""Tests for BotMachine's parked signal and the health-check suspension.

``pause()`` clears an event and returns, so ``is_paused`` goes true while the current node
is still running — for the whole of a fight, in the worst case. A scheduled relog that
trusted it would close a client mid-node. These pin the difference.

Nodes are async mocks; no Wizard101 client is involved.
"""

import asyncio

import pytest

from bartlebot.bot.machine import BotMachine

CONFIG = {
    "initial": "state_a",
    "states": [{"id": "state_a", "node_type": "mock", "params": {}}],
    "transitions": [{"trigger": "advance", "source": "state_a", "dest": "state_a"}],
}


async def quick_node(*_args):
    """A node that returns immediately, so the loop spins until it is paused or stopped."""
    return "SUCCESS"


def make_machine(node_fn):
    return BotMachine(clients=[], config=CONFIG, node_registry={"mock": node_fn}, stop_grace_period=0.1)


async def _run_briefly(machine) -> asyncio.Task:
    """Start the run loop and let it reach its first node."""
    task = asyncio.create_task(machine.run())
    await asyncio.sleep(0)
    return task


async def _finish(machine, task) -> None:
    machine.stop()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except (TimeoutError, asyncio.CancelledError):
        task.cancel()


class TestWaitUntilParked:
    async def test_a_machine_that_never_ran_does_not_park(self):
        machine = make_machine(quick_node)
        machine.pause()
        assert machine.is_paused is True  # a pause was asked for...
        assert await machine.wait_until_parked(0.05) is False  # ...but no loop reached it

    async def test_pause_during_a_node_does_not_park_until_the_node_returns(self):
        release = asyncio.Event()

        async def slow_node(*_args):
            await release.wait()
            return "SUCCESS"

        machine = make_machine(slow_node)
        task = await _run_briefly(machine)
        await asyncio.sleep(0.02)  # let the node start

        machine.pause()
        assert machine.is_paused is True
        assert await machine.wait_until_parked(0.05) is False  # still inside the node

        release.set()
        assert await machine.wait_until_parked(1.0) is True  # node returned, loop parked

        await _finish(machine, task)

    async def test_a_stop_while_waiting_returns_at_once(self):
        """A loop that stops breaks out without parking, so waiting on would cost the whole timeout."""
        release = asyncio.Event()

        async def slow_node(*_args):
            await release.wait()
            return "SUCCESS"

        machine = make_machine(slow_node)
        task = await _run_briefly(machine)
        await asyncio.sleep(0.02)  # let the node start
        machine.pause()
        waiting = asyncio.create_task(machine.wait_until_parked(30.0))
        await asyncio.sleep(0)

        machine.stop()
        assert await asyncio.wait_for(waiting, timeout=1.0) is False
        await _finish(machine, task)

    async def test_resume_unparks(self):
        machine = make_machine(quick_node)
        task = await _run_briefly(machine)
        machine.pause()
        assert await machine.wait_until_parked(1.0) is True

        machine.resume()
        await asyncio.sleep(0)
        assert await machine.wait_until_parked(0.05) is False

        await _finish(machine, task)

    async def test_a_stopped_machine_never_reports_parked(self):
        machine = make_machine(quick_node)
        task = await _run_briefly(machine)
        machine.pause()
        assert await machine.wait_until_parked(1.0) is True

        machine.stop()
        assert await machine.wait_until_parked(0.05) is False
        await _finish(machine, task)


class TestAmbientCombatFlag:
    async def test_false_when_the_watcher_holds_nothing(self):
        machine = make_machine(quick_node)
        assert machine.is_ambient_combat_active is False


class TestSuspendHealthChecks:
    def test_is_a_no_op_with_no_run_in_flight(self):
        machine = make_machine(quick_node)
        machine.suspend_health_checks_for([1, 2, 3])  # must not raise

    async def test_flags_every_hwnd_so_the_watchers_ignore_them(self):
        machine = make_machine(quick_node)
        task = await _run_briefly(machine)
        await asyncio.sleep(0.02)  # let run() build the health monitor

        machine.suspend_health_checks_for([111, 222])
        monitor = machine.health_monitor
        assert monitor is not None
        assert monitor.is_flagged(111) and monitor.is_flagged(222)

        # resume() clears the flags, so nothing has to remember to undo the suspension.
        machine.resume()
        assert monitor.is_flagged(111) is False

        await _finish(machine, task)


@pytest.mark.parametrize("hwnds", [[], (h for h in ())])
def test_an_empty_hwnd_collection_is_harmless(hwnds):
    machine = make_machine(quick_node)
    machine.suspend_health_checks_for(hwnds)
