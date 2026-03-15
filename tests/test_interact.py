"""Unit tests for the interact node."""

import asyncio
from unittest.mock import patch

import bartlebot.nodes.interact as ix

from tests.conftest import FakeClient, FakeMachine

_PARAMS = {
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}


def _ready_client() -> FakeClient:
    """Client with interact prompt showing immediately and no dialogue."""
    c = FakeClient()
    c.is_in_npc_range.return_value = True
    return c


async def test_success_no_dialog():
    """Happy path: prompt appears, X pressed, no dialogue window — returns SUCCESS."""
    client = _ready_client()

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    from wizwalker import Keycode
    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_success_with_dialog():
    """Dialogue appears after X and is advanced with SPACE until dismissed."""
    client = _ready_client()
    # Dialog visible on first check, cleared on second.
    client.is_in_dialog.side_effect = [True, False]

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode
    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls
    assert Keycode.SPACEBAR in calls


async def test_success_multiple_clients():
    """Both clients must have their dialogue dismissed before SUCCESS."""
    def _make_client():
        c = _ready_client()
        c.is_in_dialog.side_effect = [True, False]
        return c

    clients = [_make_client(), _make_client()]

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode
    for c in clients:
        calls = [call.args[0] for call in c.send_key.call_args_list]
        assert Keycode.X in calls
        assert Keycode.SPACEBAR in calls


async def test_prompt_never_appears_fails():
    """FAILURE when the interact prompt never shows after MAX_RETRIES attempts."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False

    with patch.object(ix, "POLL_INTERVAL", 0), patch.object(ix, "PROMPT_TIMEOUT", 0.01):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # teleport(interact) + teleport(reset) for each attempt
    assert client.teleport.call_count == ix.MAX_RETRIES * 2


async def test_prompt_retry_then_success():
    """Prompt times out on first attempt; appears after reset on second attempt."""
    client = FakeClient()
    reset_visited: list[bool] = []

    async def tracking_teleport(xyz):
        if xyz.x == 1.0:  # reset_xyz has x=1.0
            reset_visited.append(True)

    client.teleport.side_effect = tracking_teleport

    async def npc_range():
        return bool(reset_visited)

    client.is_in_npc_range.side_effect = npc_range

    with (
        patch.object(ix, "POLL_INTERVAL", 0),
        patch.object(ix, "PROMPT_TIMEOUT", 0.01),
    ):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 1
    assert client.teleport.call_count == 3  # interact, reset, interact


async def test_dialog_timeout_fails():
    """FAILURE when dialogue never clears within DIALOG_TIMEOUT."""
    client = _ready_client()
    client.is_in_dialog.return_value = True  # never clears

    with patch.object(ix, "POLL_INTERVAL", 0), patch.object(ix, "DIALOG_TIMEOUT", 0.01):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_late_dialog_caught_within_safety_window():
    """Dialog appearing after several polls (but before safety_delay expires) is caught and advanced."""
    client = _ready_client()
    call_count = 0

    async def late_dialog():
        nonlocal call_count
        call_count += 1
        # Each call takes real time so elapsed accumulates; dialog appears on 3rd poll.
        await asyncio.sleep(0.007)
        return call_count == 3

    client.is_in_dialog.side_effect = late_dialog

    machine = FakeMachine()
    machine.get_option = lambda name, default=None: 0.1 if name == "Safety Delay" else 0

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode
    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.SPACEBAR in calls
