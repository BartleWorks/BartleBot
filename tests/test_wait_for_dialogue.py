"""Unit tests for the wait_for_dialogue node."""

from unittest.mock import patch

import bartlebot.nodes.interaction.wait_for_dialogue as wfd
from tests.conftest import FakeClient, FakeMachine


async def test_no_dialogue_returns_success():
    """No dialogue at all → SUCCESS immediately (safety_delay=0 from FakeMachine)."""
    client = FakeClient()
    client.is_in_dialog.return_value = False

    result = await wfd.run(FakeMachine(), [client], {})

    assert result == "SUCCESS"


async def test_dialogue_dismissed_then_success():
    """Dialogue appears once, is dismissed, then clears → one click, SUCCESS."""
    client = FakeClient()
    client.is_in_dialog.side_effect = [True, False]

    with patch.object(wfd, "POLL_INTERVAL", 0):
        with patch("bartlebot.nodes.node_helpers.click_by_path") as mock_click:
            result = await wfd.run(FakeMachine(options={"Safety Delay": 0}), [client], {})

    assert result == "SUCCESS"
    assert mock_click.call_count == 1


async def test_multiple_dialogues_dismissed():
    """Two dialogue windows in sequence; both are dismissed → two clicks, SUCCESS."""
    client = FakeClient()
    client.is_in_dialog.side_effect = [True, True, False]

    with patch.object(wfd, "POLL_INTERVAL", 0):
        with patch("bartlebot.nodes.node_helpers.click_by_path") as mock_click:
            result = await wfd.run(FakeMachine(options={"Safety Delay": 0}), [client], {})

    assert result == "SUCCESS"
    assert mock_click.call_count == 2


async def test_timeout_returns_failure():
    """Dialogue persists beyond the timeout → FAILURE."""
    client = FakeClient()
    client.is_in_dialog.return_value = True

    with patch.object(wfd, "POLL_INTERVAL", 0):
        with patch("bartlebot.nodes.node_helpers.click_by_path"):
            result = await wfd.run(FakeMachine(), [client], {"timeout": 0.01})

    assert result == "FAILURE"


async def test_zero_timeout_does_not_expire():
    """timeout=0 means no limit; no dialogue → SUCCESS."""
    client = FakeClient()
    client.is_in_dialog.return_value = False

    result = await wfd.run(FakeMachine(), [client], {"timeout": 0.0})

    assert result == "SUCCESS"


async def test_all_clients_must_be_clear():
    """Two clients: one always free, one has dialogue → dialogue dismissed on busy client only, then SUCCESS."""
    free = FakeClient()
    free.is_in_dialog.return_value = False

    busy = FakeClient()
    busy.is_in_dialog.side_effect = [True, False]

    with patch.object(wfd, "POLL_INTERVAL", 0):
        with patch("bartlebot.nodes.node_helpers.click_by_path") as mock_click:
            result = await wfd.run(FakeMachine(options={"Safety Delay": 0}), [free, busy], {})

    assert result == "SUCCESS"
    assert mock_click.call_count == 1


async def test_empty_clients_returns_success():
    """No clients → no dialogue possible → SUCCESS immediately."""
    result = await wfd.run(FakeMachine(), [], {})

    assert result == "SUCCESS"


async def test_timer_resets_after_dialogue():
    """Safety delay timer resets after each dismissal — late dialogue is not missed."""
    client = FakeClient()
    # No dialogue initially, then a dialogue window appears. Provide enough trailing Falses
    # for the safety_delay window to elapse after the dismissal.
    client.is_in_dialog.side_effect = [False, True] + [False] * 500

    machine = FakeMachine()
    machine.get_option = lambda _: 0.001  # 1ms Safety Delay

    with patch.object(wfd, "POLL_INTERVAL", 0):
        with patch("bartlebot.nodes.node_helpers.click_by_path") as mock_click:
            result = await wfd.run(machine, [client], {})

    assert result == "SUCCESS"
    assert mock_click.call_count == 1  # exactly one dismissal for the one dialogue window
