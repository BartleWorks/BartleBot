"""Unit tests for the apply_level_scaling node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.action.apply_level_scaling as als
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {
    "zone": "TestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}

# No-op patches for the NPCServicesWin menu (instantly visible, then instantly gone).
_patch_menu_visible = lambda: patch(  # noqa: E731
    "bartlebot.nodes.action.apply_level_scaling._all_menu_visible",
    new=AsyncMock(return_value=True),
)
_patch_menu_gone = lambda: patch(  # noqa: E731
    "bartlebot.nodes.action.apply_level_scaling._none_menu_visible",
    new=AsyncMock(return_value=True),
)
# Patch for step 10: elixir is active on all clients after interaction.
_patch_elixir_active = lambda: patch(  # noqa: E731
    "bartlebot.nodes.node_helpers.is_visible_by_path",
    new=AsyncMock(return_value=True),
)


def _ready_client() -> FakeClient:
    """Client with no stale prompt; interact prompt shows immediately at interact_xyz."""
    c = FakeClient()
    # First call: stale-prompt check on entry → no stale prompt.
    # Second call onward: prompt is present at interact_xyz.
    c.is_in_npc_range.side_effect = [False, True]
    return c


async def test_wrong_zone_fails():
    """Pre-condition failure: client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await als.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_one_client_wrong_zone_fails():
    """Pre-condition failure: one of two clients is in the wrong zone."""
    good = FakeClient()  # zone_name defaults to "TestZone"
    bad = FakeClient()
    bad.zone_name.return_value = "WrongZone"

    result = await als.run(FakeMachine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    good.teleport.assert_not_called()
    bad.teleport.assert_not_called()


async def test_all_clients_filtered_out_succeeds():
    """Level Scaling Clients=[5] with only 1 client → all filtered, returns SUCCESS immediately."""
    client = FakeClient()
    machine = FakeMachine(options={"Level Scaling Clients": [5]})

    result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()
    client.send_key.assert_not_called()


async def test_prompt_never_appears_fails():
    """FAILURE when the interact prompt never shows after MAX_RETRIES attempts."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False  # never appears
    machine = FakeMachine(options={"Level Scaling Clients": [1]})

    with patch.object(als, "POLL_INTERVAL", 0), patch.object(als, "INTERACT_TIMEOUT", 0.01):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    # teleport(interact) + teleport(reset) for each attempt
    assert client.teleport.call_count == als.MAX_RETRIES * 2


async def test_menu_never_appears_fails():
    """FAILURE when NPCServicesWin menu never appears after pressing X."""
    client = _ready_client()
    machine = FakeMachine(options={"Level Scaling Clients": [1]})

    with (
        patch("bartlebot.nodes.action.apply_level_scaling._all_menu_visible", new=AsyncMock(return_value=False)),
        patch.object(als, "POLL_INTERVAL", 0),
        patch.object(als, "INTERACT_TIMEOUT", 0.01),
    ):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_success():
    """Happy path: prompt appears, X pressed, menu appears and dismisses → SUCCESS."""
    client = _ready_client()
    machine = FakeMachine(options={"Level Scaling Clients": [1]})

    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(als, "POLL_INTERVAL", 0):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_empty_clients_to_scale_skips_all():
    """Level Scaling Clients=[] (default) skips all clients — returns SUCCESS immediately."""
    c1 = FakeClient()
    c2 = FakeClient()

    result = await als.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c1.teleport.assert_not_called()
    c1.send_key.assert_not_called()
    c2.teleport.assert_not_called()
    c2.send_key.assert_not_called()


async def test_clients_to_scale_subset():
    """Only the specified client is scaled; the other is not touched."""
    c1 = FakeClient()  # should NOT be scaled
    c2 = _ready_client()  # should be scaled
    machine = FakeMachine(options={"Level Scaling Clients": [2]})

    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(als, "POLL_INTERVAL", 0):
        result = await als.run(machine, [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c1.teleport.assert_not_called()
    c1.send_key.assert_not_called()
    c2.teleport.assert_called()
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in c2.send_key.call_args_list)


async def test_out_of_range_client_numbers_skipped():
    """Client numbers beyond the connected count are silently skipped."""
    client = _ready_client()
    machine = FakeMachine(options={"Level Scaling Clients": [1, 99]})

    # [1, 99]: client 99 doesn't exist, only client 1 is scaled.
    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(als, "POLL_INTERVAL", 0):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_stale_prompt_cleared_before_interaction():
    """A pre-existing interact prompt is dismissed via reset_xyz before the retry loop."""
    client = FakeClient()
    machine = FakeMachine(options={"Level Scaling Clients": [1]})
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

    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(als, "POLL_INTERVAL", 0):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    # First teleport must be to reset_xyz (x=1.0), second to interact_xyz (x=0.0).
    first_tp, second_tp = client.teleport.call_args_list[:2]
    assert first_tp.args[0].x == 1.0  # reset_xyz
    assert second_tp.args[0].x == 0.0  # interact_xyz


async def test_prompt_retry_then_success():
    """Prompt times out on first attempt; appears after reset on second attempt."""
    client = FakeClient()
    machine = FakeMachine(options={"Level Scaling Clients": [1]})
    reset_visited: list[bool] = []

    async def tracking_teleport(xyz):
        if xyz.x == 1.0:  # reset_xyz has x=1.0
            reset_visited.append(True)

    client.teleport.side_effect = tracking_teleport

    async def npc_range():
        return bool(reset_visited)

    client.is_in_npc_range.side_effect = npc_range

    with (
        _patch_menu_visible(),
        _patch_menu_gone(),
        _patch_elixir_active(),
        patch.object(als, "POLL_INTERVAL", 0),
        patch.object(als, "INTERACT_TIMEOUT", 0.01),
    ):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 1
    assert client.teleport.call_count == 3  # interact, reset, interact
