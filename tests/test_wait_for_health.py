"""Unit tests for the wait_for_health node."""

from unittest.mock import patch

import bartlebot.nodes.action.wait_for_health as wfh
from tests.conftest import FakeClient, FakeMachine


async def test_already_healthy_returns_success():
    """Health already at threshold on first check → returns SUCCESS immediately."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.8

    result = await wfh.run(FakeMachine(), [client], {"threshold": 0.8})

    assert result == "SUCCESS"
    client.calc_health_ratio.assert_called_once()


async def test_waits_until_threshold_reached():
    """Health starts below threshold, rises after two polls → returns SUCCESS."""
    client = FakeClient()
    client.calc_health_ratio.side_effect = [0.5, 0.6, 0.8]

    with patch.object(wfh, "POLL_INTERVAL", 0):
        result = await wfh.run(FakeMachine(), [client], {"threshold": 0.8})

    assert result == "SUCCESS"
    assert client.calc_health_ratio.call_count == 3


async def test_timeout_returns_failure():
    """Health never reaches threshold before timeout → returns FAILURE."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.3

    with patch.object(wfh, "POLL_INTERVAL", 0):
        result = await wfh.run(FakeMachine(), [client], {"threshold": 0.8, "timeout": 0.01})

    assert result == "FAILURE"


async def test_zero_timeout_does_not_expire():
    """timeout=0 means no limit; condition met on first check → SUCCESS without timing out."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 1.0

    result = await wfh.run(FakeMachine(), [client], {"threshold": 0.8, "timeout": 0.0})

    assert result == "SUCCESS"


async def test_all_clients_must_meet_threshold():
    """Two clients: one healthy, one not → waits until both are above threshold."""
    healthy = FakeClient()
    healthy.calc_health_ratio.return_value = 1.0

    recovering = FakeClient()
    recovering.calc_health_ratio.side_effect = [0.4, 0.4, 0.9]

    with patch.object(wfh, "POLL_INTERVAL", 0):
        result = await wfh.run(FakeMachine(), [healthy, recovering], {"threshold": 0.8})

    assert result == "SUCCESS"
    # Healthy client polled once per cycle (3 cycles); recovering polled 3 times.
    assert recovering.calc_health_ratio.call_count == 3


async def test_threshold_exactly_met():
    """Health ratio exactly equal to threshold satisfies the condition."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.5

    result = await wfh.run(FakeMachine(), [client], {"threshold": 0.5})

    assert result == "SUCCESS"
