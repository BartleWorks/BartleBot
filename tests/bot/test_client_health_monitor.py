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
