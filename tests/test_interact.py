"""Unit tests for the interact node."""

import asyncio
from itertools import chain, repeat
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.interaction.interact as ix
import bartlebot.nodes.node_helpers as _nh
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
    # Second call onward: prompt is present at interact_xyz (infinite Trues via repeat so
    # the mock never exhausts if the node retries due to any scheduling variance).
    c.is_in_npc_range.side_effect = chain([False], repeat(True))
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
    # is_visible_by_path returns True on first check (dialogue visible), then False.
    _seen: list = []

    async def _visible(c, path):
        if not _seen:
            _seen.append(True)
            return True
        return False

    with (
        patch.object(ix, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.POLL_INTERVAL", 0),
        patch.object(_nh, "is_visible_by_path", side_effect=_visible),
    ):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls
    assert Keycode.SPACEBAR in calls


async def test_success_multiple_clients():
    """Both clients must have their dialogue dismissed before SUCCESS."""
    c1 = _ready_client()
    c2 = _ready_client()
    clients = [c1, c2]
    _seen: dict[int, list] = {id(c1): [], id(c2): []}

    async def _visible(c, path):
        seen = _seen[id(c)]
        if not seen:
            seen.append(True)
            return True
        return False

    with (
        patch.object(ix, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.POLL_INTERVAL", 0),
        patch.object(_nh, "is_visible_by_path", side_effect=_visible),
    ):
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

    with patch.object(ix, "POLL_INTERVAL", 0):
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

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 1
    assert client.teleport.call_count == 3  # interact, reset, interact


async def test_dialog_timeout_fails():
    """FAILURE when dialogue never clears within DIALOG_TIMEOUT."""
    client = _ready_client()

    with (
        patch.object(ix, "POLL_INTERVAL", 0),
        patch.object(ix, "DIALOG_TIMEOUT", 0.01),
        patch("bartlebot.nodes.node_helpers.POLL_INTERVAL", 0),
        patch.object(_nh, "is_visible_by_path", new=AsyncMock(return_value=True)),
    ):
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


async def test_has_dialogue_false_skips_dialogue_wait():
    """has_dialogue=False returns SUCCESS immediately after X, skipping the dialogue phase."""
    client = _ready_client()
    params = {**_PARAMS, "has_dialogue": False}

    with patch.object(ix, "POLL_INTERVAL", 0):
        result = await ix.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)
    assert not any(call.args[0] == Keycode.SPACEBAR for call in client.send_key.call_args_list)


async def test_has_dialogue_true_explicit_behaves_as_default():
    """has_dialogue=True (explicit) is identical to the default — dialogue phase runs normally."""
    client = _ready_client()
    params = {**_PARAMS, "has_dialogue": True}
    vis_mock = AsyncMock(return_value=False)

    with (
        patch.object(ix, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.POLL_INTERVAL", 0),
        patch.object(_nh, "is_visible_by_path", vis_mock),
    ):
        result = await ix.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    vis_mock.assert_called()  # dialogue phase ran — is_visible_by_path was polled


async def test_late_dialog_caught_within_safety_window():
    """Dialog appearing after several polls (but before safety_delay expires) is caught and advanced."""
    client = _ready_client()
    call_count = 0

    async def _late_visible(c, path):
        nonlocal call_count
        call_count += 1
        # Each call takes real time so elapsed accumulates; dialog appears on 3rd poll.
        await asyncio.sleep(0.007)
        return call_count == 3

    machine = FakeMachine(options={"Safety Delay": 0.05})

    with (
        patch.object(ix, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.POLL_INTERVAL", 0),
        patch.object(_nh, "is_visible_by_path", side_effect=_late_visible),
    ):
        result = await ix.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
