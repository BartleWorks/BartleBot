"""Unit tests for the apply_level_scaling node, and the elixir code it shares with handle_level_scaling.

The station mechanics (``apply_elixir`` and its menu waits) live in handle_level_scaling,
which outlives this node, so they are patched there.
"""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.utility.apply_level_scaling as als
import bartlebot.nodes.utility.handle_level_scaling as hls
from tests.conftest import FakeClient, FakeMachine, prompt_at

_PARAMS = {
    "zone": "TestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}

# No-op patches for the NPCServicesWin menu (instantly visible, then instantly gone).
_patch_menu_visible = lambda: patch(  # noqa: E731
    "bartlebot.nodes.utility.handle_level_scaling._all_menu_visible",
    new=AsyncMock(return_value=True),
)
_patch_menu_gone = lambda: patch(  # noqa: E731
    "bartlebot.nodes.utility.handle_level_scaling._none_menu_visible",
    new=AsyncMock(return_value=True),
)
# Patch for step 10: elixir is active on all clients after interaction.
_patch_elixir_active = lambda: patch(  # noqa: E731
    "bartlebot.nodes.node_helpers.is_visible_by_path",
    new=AsyncMock(return_value=True),
)


def _ready_client() -> FakeClient:
    """Client with no stale prompt, whose interact prompt shows at interact_xyz and nowhere else."""
    c = FakeClient()
    prompt_at(c, _PARAMS["interact_xyz"])
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
    """Level Scaling Players=[5] with only 1 client → all filtered, returns SUCCESS immediately."""
    client = FakeClient()
    machine = FakeMachine(options={"Level Scaling Players": [5]})

    result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()
    client.send_key.assert_not_called()


async def test_prompt_never_appears_fails():
    """FAILURE when the interact prompt never shows after MAX_RETRIES attempts."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False  # never appears
    machine = FakeMachine(options={"Level Scaling Players": [1]})

    with patch.object(hls, "POLL_INTERVAL", 0):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    # The first reset, then teleport(interact) + teleport(reset) for each attempt
    assert client.teleport.call_count == 1 + hls.MAX_RETRIES * 2


async def test_menu_never_appears_fails():
    """FAILURE when NPCServicesWin menu never appears after pressing X."""
    client = _ready_client()
    machine = FakeMachine(options={"Level Scaling Players": [1]})

    with (
        patch("bartlebot.nodes.utility.handle_level_scaling._all_menu_visible", new=AsyncMock(return_value=False)),
        patch.object(hls, "POLL_INTERVAL", 0),
    ):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_success():
    """Happy path: prompt appears, X pressed, menu appears and dismisses → SUCCESS."""
    client = _ready_client()
    machine = FakeMachine(options={"Level Scaling Players": [1]})

    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(hls, "POLL_INTERVAL", 0):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_empty_clients_to_scale_skips_all():
    """Level Scaling Players=[] (default) skips all clients — returns SUCCESS immediately."""
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
    machine = FakeMachine(options={"Level Scaling Players": [2]})

    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(hls, "POLL_INTERVAL", 0):
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
    machine = FakeMachine(options={"Level Scaling Players": [1, 99]})

    # [1, 99]: client 99 doesn't exist, only client 1 is scaled.
    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(hls, "POLL_INTERVAL", 0):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_stale_prompt_cleared_before_interaction():
    """A pre-existing interact prompt is dismissed via reset_xyz before the retry loop."""
    client = FakeClient()
    machine = FakeMachine(options={"Level Scaling Players": [1]})
    prompt_at(client, _PARAMS["interact_xyz"], initially=True)  # stale on node entry

    with _patch_menu_visible(), _patch_menu_gone(), _patch_elixir_active(), patch.object(hls, "POLL_INTERVAL", 0):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    # First teleport must be to reset_xyz (x=1.0), second to interact_xyz (x=0.0).
    first_tp, second_tp = client.teleport.call_args_list[:2]
    assert first_tp.args[0].x == 1.0  # reset_xyz
    assert second_tp.args[0].x == 0.0  # interact_xyz


async def test_prompt_retry_then_success():
    """Prompt times out on the first round; appears after reset on the second."""
    client = FakeClient()
    machine = FakeMachine(options={"Level Scaling Players": [1]})
    reset_visited: list[bool] = []

    async def tracking_teleport(xyz):
        if xyz.x == 1.0:  # reset_xyz has x=1.0
            reset_visited.append(True)

    client.teleport.side_effect = tracking_teleport

    async def npc_range():
        # Only at interact_xyz, and only once the client has been reset twice: the
        # reset every run starts with, and the one after the first miss.
        calls = client.teleport.call_args_list
        return bool(calls) and calls[-1].args[0].x == 0.0 and len(reset_visited) >= 2

    client.is_in_npc_range.side_effect = npc_range

    # The elixir only shows up once the station has actually been used, which cannot
    # happen until the prompt appears.
    async def elixir_visible(_client, _path):
        return bool(client.send_key.call_args_list)

    with (
        _patch_menu_visible(),
        _patch_menu_gone(),
        patch("bartlebot.nodes.node_helpers.is_visible_by_path", new=AsyncMock(side_effect=elixir_visible)),
        patch.object(hls, "POLL_INTERVAL", 0),
    ):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 2
    assert client.teleport.call_count == 4  # reset, interact, reset, interact


async def test_missing_elixir_is_retried():
    """A client whose elixir did not take is retried; one that already has it is left alone."""
    c1 = _ready_client()
    c2 = _ready_client()
    machine = FakeMachine(options={"Level Scaling Players": [1, 2]})

    # c1 gets the elixir on the first round; c2 only on the second.
    async def elixir_visible(client, _path):
        if client is c1:
            return True
        return len(c2.send_key.call_args_list) >= 2

    with (
        _patch_menu_visible(),
        _patch_menu_gone(),
        patch("bartlebot.nodes.node_helpers.is_visible_by_path", new=AsyncMock(side_effect=elixir_visible)),
        patch.object(hls, "POLL_INTERVAL", 0),
        patch.object(hls, "LEVEL_SCALING_STAGGER_DELAY", 0),
    ):
        result = await als.run(machine, [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    assert len(c1.send_key.call_args_list) == 1  # used the station once, not retried
    assert len(c2.send_key.call_args_list) == 2  # retried after the elixir did not take


async def test_clients_use_station_one_at_a_time():
    """Two clients never overlap at the station — one finishes before the next presses X."""
    c1 = _ready_client()
    c2 = _ready_client()
    machine = FakeMachine(options={"Level Scaling Players": [1, 2]})
    order: list[str] = []

    def _record_key(client, label):
        async def _send(*_args, **_kwargs):
            order.append(f"x{label}")

        client.send_key.side_effect = _send

    _record_key(c1, 1)
    _record_key(c2, 2)

    async def _click(client, _path):
        order.append(f"click{1 if client is c1 else 2}")
        return True

    with (
        _patch_menu_visible(),
        _patch_menu_gone(),
        _patch_elixir_active(),
        patch("bartlebot.nodes.node_helpers.click_by_path", new=AsyncMock(side_effect=_click)),
        patch.object(hls, "POLL_INTERVAL", 0),
        patch.object(hls, "LEVEL_SCALING_STAGGER_DELAY", 0),
    ):
        result = await als.run(machine, [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    assert order == ["x1", "click1", "x2", "click2"]


async def test_a_prompt_that_will_not_clear_at_reset_fails_without_using_the_station():
    client = FakeClient()
    client.is_in_npc_range.return_value = True
    machine = FakeMachine(options={"Level Scaling Players": [1]})

    with (
        _patch_menu_visible(),
        _patch_menu_gone(),
        patch.object(hls, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.PROMPT_CLEAR_SETTLE", 0),
    ):
        result = await als.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()
