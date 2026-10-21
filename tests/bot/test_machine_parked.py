"""Tests for BotMachine's parked signal and the health-check suspension.

``pause()`` clears an event and returns, so ``is_paused`` goes true while the current node
is still running — for the whole of a fight, in the worst case. A scheduled relog that
trusted it would close a client mid-node. These pin the difference.

Nodes are async mocks; no Wizard101 client is involved.
"""

import asyncio

import pytest
from wizwalker.errors import ClientClosedError

from bartlebot.bot.client_health_monitor import ClientHealthMonitor
from bartlebot.bot.machine import BotMachine
from tests.conftest import FakeClient

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

    async def test_a_logout_keeps_the_disconnect_watcher_on(self):
        machine = make_machine(quick_node)
        task = await _run_briefly(machine)
        await asyncio.sleep(0.02)

        machine.suspend_health_checks_for([111], watch_disconnects=True)
        monitor = machine.health_monitor
        assert monitor.is_suspended(111)
        assert not monitor.is_flagged(111)  # the flag is what the disconnect dialog watcher skips

        await _finish(machine, task)

    async def test_resuming_leaves_a_client_a_watcher_recovered_meanwhile_flagged(self, monkeypatch):
        """The flag is what stops a second watcher recovering a client recovery is already relaunching."""
        client = FakeClient()
        machine = BotMachine(clients=[client], config=CONFIG, node_registry={"mock": quick_node}, stop_grace_period=0.1)
        task = await _run_briefly(machine)
        await asyncio.sleep(0.02)
        monkeypatch.setattr(ClientHealthMonitor, "_force_kill_process", staticmethod(lambda _pid: None))
        monitor = machine.health_monitor

        machine.suspend_health_checks_for([client.window_handle], watch_disconnects=True)
        monitor.recover(client, "shows a disconnect modal")
        machine.resume_health_checks_for([client.window_handle])

        assert machine.is_handed_to_recovery(client)
        assert monitor.is_flagged(client.window_handle)
        await _finish(machine, task)


class TestAbandonClient:
    """A node handing a client it wedged to crash recovery, or stopping when recovery cannot help."""

    @staticmethod
    def _machine(node_fn, clients, **kwargs):
        stopped: list[str] = []
        machine = BotMachine(
            clients=clients,
            config=CONFIG,
            node_registry={"mock": node_fn},
            stop_grace_period=0.1,
            on_stopped=stopped.append,
            **kwargs,
        )
        return machine, stopped

    @staticmethod
    async def _started(machine, monkeypatch) -> tuple[asyncio.Task, list]:
        """Run the loop until the monitor exists, with its kill swapped for a recorder."""
        task = await _run_briefly(machine)
        await asyncio.sleep(0.02)
        recovered: list = []
        monkeypatch.setattr(machine.health_monitor, "recover", lambda c, r: recovered.append((c, r)))
        return task, recovered

    def test_with_no_run_in_flight_there_is_nothing_to_hand_to(self):
        client = FakeClient()
        machine, _ = self._machine(quick_node, [client])
        assert machine.abandon_client(client, "has been loading for 60s") is False

    async def test_a_recoverable_client_goes_to_the_monitor(self, monkeypatch):
        client = FakeClient()
        machine, stopped = self._machine(quick_node, [client], recovery_gap=lambda _hwnd: None)
        task, recovered = await self._started(machine, monkeypatch)

        assert machine.abandon_client(client, "has been loading for 60s") is True
        assert recovered == [(client, "has been loading for 60s")]
        assert not machine.is_stopped and stopped == []
        await _finish(machine, task)

    async def test_a_client_recovery_cannot_relaunch_stops_the_run_and_says_why(self, monkeypatch):
        other, client = FakeClient(), FakeClient()
        machine, stopped = self._machine(
            quick_node, [other, client], recovery_gap=lambda _hwnd: "no password saved for 'Account 2'"
        )
        task, recovered = await self._started(machine, monkeypatch)

        assert machine.abandon_client(client, "has been loading for 60s") is False
        assert recovered == []  # closing it would leave the bot waiting on a player that never returns
        assert machine.is_stopped
        (message,) = stopped
        assert message.startswith("Player 2 has been loading for 60s")
        assert "no password saved for 'Account 2'" in message
        await _finish(machine, task)

    async def test_a_client_already_handed_over_is_not_recovered_again(self, monkeypatch):
        """The disconnect dialog watcher runs through a relog, and can close the client first."""
        client = FakeClient()
        machine, _ = self._machine(quick_node, [client], recovery_gap=lambda _hwnd: None)
        task = await _run_briefly(machine)
        await asyncio.sleep(0.02)
        monkeypatch.setattr(ClientHealthMonitor, "_force_kill_process", staticmethod(lambda _pid: None))
        machine.health_monitor.recover(client, "shows a disconnect modal")
        recovered: list = []
        monkeypatch.setattr(machine.health_monitor, "recover", lambda c, r: recovered.append((c, r)))

        assert machine.abandon_client(client, "has been loading for 60s") is True
        assert recovered == []
        await _finish(machine, task)

    async def test_a_gap_lookup_that_raises_is_not_a_gap(self, monkeypatch):
        """As recovery_gaps does: an unverifiable gap is not claimed."""
        client = FakeClient()

        def _broken(_hwnd):
            raise RuntimeError("Credential Manager is unhappy")

        machine, _ = self._machine(quick_node, [client], recovery_gap=_broken)
        task, recovered = await self._started(machine, monkeypatch)
        assert machine.abandon_client(client, "has been loading for 60s") is True
        assert len(recovered) == 1
        await _finish(machine, task)

    async def test_a_node_that_abandons_and_raises_parks_without_counting(self, monkeypatch):
        """The contract relog relies on: the run waits for recovery, even with no retries left.

        A FAILURE here would hit max_consecutive_retries=1 and stop the run before recovery
        could resume it, which is the bug this path exists to avoid.
        """
        client = FakeClient()

        async def abandoning_node(machine, _clients, _params):
            machine.abandon_client(client, "has been loading for 60s")
            raise ClientClosedError()

        config = {**CONFIG, "states": [{**CONFIG["states"][0], "max_consecutive_retries": 1}]}
        machine = BotMachine(
            clients=[client], config=config, node_registry={"mock": abandoning_node}, stop_grace_period=0.1
        )
        task = await _run_briefly(machine)
        monkeypatch.setattr(ClientHealthMonitor, "recover", lambda self, c, r: None)

        assert await machine.wait_until_parked(1.0) is True
        assert not machine.is_stopped
        await _finish(machine, task)


@pytest.mark.parametrize("hwnds", [[], (h for h in ())])
def test_an_empty_hwnd_collection_is_harmless(hwnds):
    machine = make_machine(quick_node)
    machine.suspend_health_checks_for(hwnds)
