"""Tests for ClientHealthMonitor's loops as loops: one bad pass must not end any of them (#154).

What each watcher detects is covered beside the watcher's own subject: the disconnect modal
and the network icons in their own files, the restricted zones in tests/game.
"""

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

import bartlebot.bot.client_health_monitor as health_module
from bartlebot.bot.client_health_monitor import ClientHealthMonitor
from tests.conftest import FakeClient

WATCHERS = {
    "_disconnect_dialog_watcher": "Disconnect dialog watcher",
    "_network_link_watcher": "Network link watcher",
    "_client_status_watcher": "Client status watcher",
    "_restricted_zone_watcher": "Restricted-zone watcher",
    "_loading_screen_watcher": "Loading screen watcher",
}


@pytest.fixture
def fast(monkeypatch):
    for interval in (
        "POLL_INTERVAL",
        "NETWORK_LINK_POLL_INTERVAL",
        "_CLIENT_STATUS_CHECK_INTERVAL",
        "_RESTRICTED_ZONE_CHECK_INTERVAL",
    ):
        monkeypatch.setattr(health_module, interval, 0.001)


@pytest.mark.parametrize("watcher", list(WATCHERS))
async def test_a_pass_that_raises_does_not_end_the_watcher(fast, caplog, watcher):
    """The restricted-zone watcher is the PvP guard: ended by one stray exception, it stayed
    ended for the rest of the run, and said so only when the run was over."""
    passes = []

    def clients():
        passes.append(None)
        if len(passes) == 1:
            raise RuntimeError("the client list could not be read")
        return [FakeClient()]

    stop = asyncio.Event()
    monitor = ClientHealthMonitor(clients_ref=clients, stop_flag=stop, on_pause=MagicMock())
    task = asyncio.create_task(getattr(monitor, watcher)())
    with caplog.at_level(logging.ERROR):
        for _ in range(500):
            if len(passes) >= 3:
                break
            await asyncio.sleep(0.002)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert len(passes) >= 3
    assert f"{WATCHERS[watcher]} failed; it carries on" in caplog.text


async def test_the_restricted_zone_watcher_still_fires_after_a_bad_pass(fast):
    calls = []
    arena = FakeClient()
    arena.zone_name.return_value = "WizardCity/MB_Arena"

    def clients():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("the client list could not be read")
        return [arena]

    fired = MagicMock()
    monitor = ClientHealthMonitor(
        clients_ref=clients, stop_flag=asyncio.Event(), on_pause=MagicMock(), on_restricted_zone=fired
    )
    await asyncio.wait_for(monitor._restricted_zone_watcher(), timeout=1.0)

    assert fired.call_count == 1


class TestSuspend:
    """A relog's suspension: every watcher stands down for the client but the disconnect dialog one."""

    @staticmethod
    def _monitor(clients) -> ClientHealthMonitor:
        return ClientHealthMonitor(clients_ref=lambda: clients, stop_flag=asyncio.Event(), on_pause=MagicMock())

    def test_a_suspended_client_is_not_flagged(self):
        monitor = self._monitor([])
        monitor.suspend(7)
        assert monitor.is_suspended(7)
        assert not monitor.is_flagged(7)

    def test_unflag_lifts_a_suspension(self):
        monitor = self._monitor([])
        monitor.suspend(7)
        monitor.unflag(7)
        assert not monitor.is_suspended(7)

    def test_clear_flagged_lifts_every_suspension(self):
        monitor = self._monitor([])
        monitor.suspend(7)
        monitor.flag(8)
        monitor.clear_flagged()
        assert not monitor.is_suspended(7)
        assert not monitor.is_flagged(8)

    async def test_the_status_watcher_leaves_a_suspended_client_alone(self, fast):
        """A client at character select fails zone_name(), which is a relog working, not a crash."""
        client = FakeClient()

        async def _unreadable():
            raise RuntimeError("at character select")

        client.zone_name = _unreadable
        monitor = self._monitor([client])
        monitor.suspend(client.window_handle)
        recovered: list = []
        monitor.recover = lambda c, r: recovered.append(c)

        task = asyncio.create_task(monitor._client_status_watcher())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert recovered == []
