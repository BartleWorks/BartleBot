"""Unit tests for the enter_zone_interact node."""

from unittest.mock import AsyncMock, patch

from wizwalker import Keycode

import bartlebot.nodes.action.enter_zone_interact as itz
from tests.conftest import FakeClient, FakeMachine

_FROM_ZONE = "TestZone"  # matches FakeClient zone_name default
_TO_ZONE = "TestZone/SnowGlobe"

_PARAMS = {
    "from_zone": _FROM_ZONE,
    "to_zone": _TO_ZONE,
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}


def _ready_client() -> FakeClient:
    """Client with no stale prompt and interact prompt appearing immediately.

    is_in_npc_range side_effect:
        call 1 — stale check on node entry → False (no stale prompt)
        call 2 — retry loop poll → True (prompt present at interact_xyz)
    """
    c = FakeClient()
    c.is_in_npc_range.side_effect = [False, True]
    # zone_name: from_zone check → from_zone; final verify → to_zone
    c.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE]
    # is_loading: trigger loop → loading; none_loading wait → done
    c.is_loading.side_effect = [True, False]
    return c


async def test_wrong_from_zone_fails():
    """FAILURE when client is not in from_zone; no teleports attempted."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_one_client_wrong_from_zone_fails():
    """FAILURE when one of two clients is in the wrong zone."""
    good = FakeClient()  # zone_name defaults to "TestZone" == _FROM_ZONE
    bad = FakeClient()
    bad.zone_name.return_value = "WrongZone"

    result = await itz.run(FakeMachine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    good.teleport.assert_not_called()
    bad.teleport.assert_not_called()


async def test_success_single_client():
    """Happy path: prompt appears, X pressed, client loads and arrives in to_zone."""
    client = _ready_client()

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_success_multiple_clients():
    """Both clients get prompt, press X, load, and arrive in to_zone."""
    clients = [_ready_client(), _ready_client()]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        assert any(call.args[0] == Keycode.X for call in c.send_key.call_args_list)


async def test_stale_prompt_cleared_before_loop():
    """Stale prompt on node entry → first teleport is to reset_xyz, then to interact_xyz."""
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
    client.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE]
    client.is_loading.side_effect = [True, False]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # First teleport must be to reset_xyz (x=1.0), second to interact_xyz (x=0.0).
    first_tp, second_tp = client.teleport.call_args_list[:2]
    assert first_tp.args[0].x == 1.0  # reset_xyz
    assert second_tp.args[0].x == 0.0  # interact_xyz


async def test_prompt_never_appears_fails():
    """FAILURE when the interact prompt never appears after MAX_RETRIES."""
    client = FakeClient()
    client.zone_name.return_value = _FROM_ZONE
    client.is_in_npc_range.return_value = False  # prompt never shows

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # interact + reset teleports for each attempt
    assert client.teleport.call_count == itz.MAX_RETRIES * 2
    client.send_key.assert_not_called()


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
    client.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE]
    client.is_loading.side_effect = [True, False]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 1
    assert client.teleport.call_count == 3  # interact, reset, interact


async def test_trigger_timeout_fails():
    """FAILURE when client never enters a loading screen after pressing X."""
    client = FakeClient()
    client.is_in_npc_range.side_effect = [False, True]
    client.zone_name.return_value = _FROM_ZONE
    client.is_loading.return_value = False  # never starts loading

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch.object(itz, "TRIGGER_TIMEOUT", 0.01),
    ):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_wrong_to_zone_after_loading_fails():
    """FAILURE when clients load but arrive in an unexpected zone."""
    client = FakeClient()
    client.is_in_npc_range.side_effect = [False, True]
    # Pre-check only; wait_for_zone (post-load) is mocked to return wrong zone.
    client.zone_name.side_effect = [_FROM_ZONE]
    client.is_loading.side_effect = [True, False]

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.wait_for_zone", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_x_resent_to_lagging_client():
    """One client loads immediately; the lagging client gets X re-pressed."""
    c1 = _ready_client()
    # iter1: loading (seen); iter2: done (_loading_or_arrived checks zone_name); none_loading: done.
    c1.is_loading.side_effect = [True, False, False]
    # zone_name: zone_check → iter2 loading_or_arrived (_TO_ZONE, c1 already seen) → wait_for_zone.
    c1.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE, _TO_ZONE]

    c2 = _ready_client()
    # iter1: not loading (_loading_or_arrived checks zone_name, not yet arrived); iter2: loading (seen); none_loading: done.
    c2.is_loading.side_effect = [False, True, False]
    # zone_name: zone_check → iter1 loading_or_arrived (still _FROM_ZONE, not arrived yet) → wait_for_zone.
    c2.zone_name.side_effect = [_FROM_ZONE, _FROM_ZONE, _TO_ZONE]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c1_x_presses = [call for call in c1.send_key.call_args_list if call.args[0] == Keycode.X]
    c2_x_presses = [call for call in c2.send_key.call_args_list if call.args[0] == Keycode.X]
    assert len(c1_x_presses) == 1  # only the initial press
    assert len(c2_x_presses) >= 2  # initial press + at least one re-press


async def test_fast_client_finishes_before_lagging_client_starts():
    """Client 1 finishes its loading screen before client 2 even starts loading.

    This is the bug case: is_loading() returns False for client 1 (done) AND
    False for client 2 (not started), so the old all(loading) condition could
    never be True. The seen_loading approach handles it correctly.
    """
    c1 = _ready_client()
    # iter1: loading (seen); iter2+iter3: done (_loading_or_arrived calls zone_name each time); none_loading: done.
    c1.is_loading.side_effect = [True, False, False, False]
    # zone_name: zone_check → iter2 loading_or_arrived → iter3 loading_or_arrived → wait_for_zone.
    c1.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE, _TO_ZONE, _TO_ZONE]

    c2 = _ready_client()
    # iter1+iter2: not started (_loading_or_arrived calls zone_name, not yet arrived); iter3: loading (seen); none_loading: done.
    c2.is_loading.side_effect = [False, False, True, False]
    # zone_name: zone_check → iter1 loading_or_arrived → iter2 loading_or_arrived → wait_for_zone.
    c2.zone_name.side_effect = [_FROM_ZONE, _FROM_ZONE, _FROM_ZONE, _TO_ZONE]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c2_x_presses = [call for call in c2.send_key.call_args_list if call.args[0] == Keycode.X]
    assert len(c2_x_presses) >= 2  # initial press + re-presses until loading
