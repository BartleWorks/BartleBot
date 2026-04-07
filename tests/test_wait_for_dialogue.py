"""Unit tests for the wait_for_dialogue node."""

from unittest.mock import patch

import bartlebot.nodes.action.wait_for_dialogue as wfd
from tests.conftest import FakeClient, FakeMachine


async def test_already_free_returns_success():
    """Client not in dialogue on first check → returns SUCCESS immediately."""
    client = FakeClient()
    client.is_in_dialog.return_value = False

    result = await wfd.run(FakeMachine(), [client], {})

    assert result == "SUCCESS"
    client.is_in_dialog.assert_called_once()


async def test_waits_until_dialogue_ends():
    """Client starts in dialogue, exits after two polls → returns SUCCESS."""
    client = FakeClient()
    client.is_in_dialog.side_effect = [True, True, False]

    with patch.object(wfd, "POLL_INTERVAL", 0):
        result = await wfd.run(FakeMachine(), [client], {})

    assert result == "SUCCESS"
    assert client.is_in_dialog.call_count == 3


async def test_timeout_returns_failure():
    """Client stays in dialogue beyond timeout → returns FAILURE."""
    client = FakeClient()
    client.is_in_dialog.return_value = True

    with patch.object(wfd, "POLL_INTERVAL", 0):
        result = await wfd.run(FakeMachine(), [client], {"timeout": 0.01})

    assert result == "FAILURE"


async def test_zero_timeout_does_not_expire():
    """timeout=0 means no limit; condition met on first check → SUCCESS without timing out."""
    client = FakeClient()
    client.is_in_dialog.return_value = False

    result = await wfd.run(FakeMachine(), [client], {"timeout": 0.0})

    assert result == "SUCCESS"


async def test_all_clients_must_be_free():
    """Two clients: one already free, one still in dialogue → waits until both are free."""
    free = FakeClient()
    free.is_in_dialog.return_value = False

    busy = FakeClient()
    busy.is_in_dialog.side_effect = [True, True, False]

    with patch.object(wfd, "POLL_INTERVAL", 0):
        result = await wfd.run(FakeMachine(), [free, busy], {})

    assert result == "SUCCESS"
    assert busy.is_in_dialog.call_count == 3


async def test_empty_clients_returns_success():
    """No clients → not any([]) is True → returns SUCCESS immediately."""
    result = await wfd.run(FakeMachine(), [], {})

    assert result == "SUCCESS"
