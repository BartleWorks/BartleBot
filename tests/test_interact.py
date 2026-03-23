"""Unit tests for the interact node."""

import asyncio
from unittest.mock import patch

import bartlebot.nodes.action.interact as ix
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {
    "zone": "TestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}


def _ready_client() -> FakeClient:
    """Client with no stale prompt and interact prompt showing immediately at interact_xyz."""
    c = FakeClient()
    # First call: stale-prompt check on node entry → no stale prompt.
    # Second call onward: prompt is present at interact_xyz.
    c.is_in_npc_range.side_effect = [False, True]
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

    with patch.object(ix, "POLL_INTERVAL", 0), patch.object(ix, "INTERACT_TIMEOUT", 0.01):
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
        patch.object(ix, "INTERACT_TIMEOUT", 0.01),
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


async def test_wrong_zone_fails():
    """Pre-condition failure: client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_one_client_wrong_zone_fails():
    """Pre-condition failure: one of two clients is in the wrong zone."""
    good = FakeClient()  # zone_name defaults to "TestZone"
    bad = FakeClient()
    bad.zone_name.return_value = "WrongZone"

    result = await ix.run(FakeMachine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    good.teleport.assert_not_called()
    bad.teleport.assert_not_called()


async def test_stale_prompt_cleared_before_interaction():
    """A pre-existing interact prompt is dismissed via reset_xyz before the normal
    interaction loop runs, preventing an accidental interaction with the wrong object."""
    client = FakeClient()
    call_count = 0

    async def _npc_range():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True  # stale prompt visible on node entry
        if call_count == 2:
            return False  # cleared after teleporting to reset_xyz
        return True  # correct prompt visible at interact_xyz

    client.is_in_npc_range.side_effect = _npc_range

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # First teleport must be to reset_xyz (x=1.0), second to interact_xyz (x=0.0).
    first_tp, second_tp = client.teleport.call_args_list[:2]
    assert first_tp.args[0].x == 1.0  # reset_xyz
    assert second_tp.args[0].x == 0.0  # interact_xyz


async def test_all_clients_filtered_out_succeeds():
    """interact_clients=[5] with only 1 client → all filtered, returns SUCCESS immediately."""
    client = FakeClient()
    params = {**_PARAMS, "interact_clients": [5]}

    result = await ix.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()
    client.send_key.assert_not_called()


async def test_interact_clients_subset():
    """Only the specified client interacts; the other is not touched."""
    c1 = FakeClient()  # should NOT interact
    c2 = _ready_client()  # should interact
    params = {**_PARAMS, "interact_clients": [2]}

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [c1, c2], params)

    assert result == "SUCCESS"
    c1.teleport.assert_not_called()
    c1.send_key.assert_not_called()
    c2.teleport.assert_called()
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in c2.send_key.call_args_list)


async def test_out_of_range_client_numbers_skipped():
    """Client numbers beyond the connected count are silently skipped."""
    client = _ready_client()
    params = {**_PARAMS, "interact_clients": [1, 99]}

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_empty_interact_clients_interacts_all():
    """interact_clients=[] (default) interacts with every connected client."""
    c1 = _ready_client()
    c2 = _ready_client()
    params = {**_PARAMS, "interact_clients": []}

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [c1, c2], params)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    for c in (c1, c2):
        assert any(call.args[0] == Keycode.X for call in c.send_key.call_args_list)


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
