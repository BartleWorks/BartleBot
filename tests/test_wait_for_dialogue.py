"""Unit tests for the wait_for_dialogue node."""

from unittest.mock import AsyncMock, patch

from wizwalker import Keycode

import bartlebot.nodes.interaction.wait_for_dialogue as wfd
import bartlebot.nodes.node_helpers as nh
from tests.conftest import FakeClient, FakeMachine


def _spacebar_count(client: FakeClient) -> int:
    return sum(1 for c in client.send_key.call_args_list if c.args[0] == Keycode.SPACEBAR)


async def test_no_dialogue_returns_success():
    """No dialogue at all → SUCCESS immediately (safety_delay elapses after one POLL_INTERVAL sleep)."""
    client = FakeClient()
    # is_visible_by_path returns False by default (FakeClient.root_window.children=[])

    with patch.object(nh, "POLL_INTERVAL", 0):
        result = await wfd.run(FakeMachine(), [client], {})

    assert result == "SUCCESS"


async def test_dialogue_dismissed_then_success():
    """Dialogue appears once, is dismissed with SPACEBAR, then clears → one press, SUCCESS."""
    client = FakeClient()
    _seen: list = []

    async def _visible(c, path):
        if not _seen:
            _seen.append(True)
            return True
        return False

    with (
        patch.object(nh, "is_visible_by_path", side_effect=_visible),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "DIALOG_FAST_POLL_INTERVAL", 0),
    ):
        result = await wfd.run(FakeMachine(options={"Safety Delay": 0}), [client], {})

    assert result == "SUCCESS"
    assert _spacebar_count(client) == 1


async def test_multiple_dialogues_dismissed():
    """Two dialogue windows in sequence; both are dismissed → two SPACEBAR presses, SUCCESS."""
    client = FakeClient()
    _call = [0]

    async def _visible(c, path):
        _call[0] += 1
        return _call[0] <= 2  # True on first two calls, False after

    with (
        patch.object(nh, "is_visible_by_path", side_effect=_visible),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "DIALOG_FAST_POLL_INTERVAL", 0),
    ):
        result = await wfd.run(FakeMachine(options={"Safety Delay": 0}), [client], {})

    assert result == "SUCCESS"
    assert _spacebar_count(client) == 2


async def test_timeout_returns_failure():
    """Dialogue persists beyond the timeout → FAILURE."""
    client = FakeClient()

    with patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)):
        with patch("bartlebot.nodes.node_helpers.POLL_INTERVAL", 0):
            result = await wfd.run(FakeMachine(), [client], {"timeout": 0.01})

    assert result == "FAILURE"


async def test_zero_timeout_does_not_expire():
    """timeout=0 means no limit; no dialogue → SUCCESS."""
    client = FakeClient()
    # is_visible_by_path returns False by default

    with patch.object(nh, "POLL_INTERVAL", 0):
        result = await wfd.run(FakeMachine(), [client], {"timeout": 0.0})

    assert result == "SUCCESS"


async def test_all_clients_must_be_clear():
    """Two clients: one always free, one has dialogue → SPACEBAR pressed on busy client only, then SUCCESS."""
    free = FakeClient()
    busy = FakeClient()
    _busy_seen: list = []

    async def _visible(c, path):
        if c is free:
            return False
        if not _busy_seen:
            _busy_seen.append(True)
            return True
        return False

    with (
        patch.object(nh, "is_visible_by_path", side_effect=_visible),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "DIALOG_FAST_POLL_INTERVAL", 0),
    ):
        result = await wfd.run(FakeMachine(options={"Safety Delay": 0}), [free, busy], {})

    assert result == "SUCCESS"
    assert _spacebar_count(free) == 0
    assert _spacebar_count(busy) == 1


async def test_empty_clients_returns_success():
    """No clients → no dialogue possible → SUCCESS immediately."""
    with patch.object(nh, "POLL_INTERVAL", 0):
        result = await wfd.run(FakeMachine(), [], {})

    assert result == "SUCCESS"


async def test_timer_resets_after_dialogue():
    """Safety delay timer resets after each dismissal — late dialogue is not missed."""
    client = FakeClient()
    _call = [0]

    async def _visible(c, path):
        _call[0] += 1
        # No dialogue initially, then appears on second call, then clears.
        return _call[0] == 2

    machine = FakeMachine()
    machine.get_option = lambda _: 0.001  # 1ms Safety Delay

    with (
        patch.object(nh, "is_visible_by_path", side_effect=_visible),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "DIALOG_FAST_POLL_INTERVAL", 0),
    ):
        result = await wfd.run(machine, [client], {})

    assert result == "SUCCESS"
    assert _spacebar_count(client) == 1  # exactly one SPACEBAR for the one dialogue window
