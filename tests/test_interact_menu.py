"""Unit tests for the interact_menu node."""

from itertools import chain, repeat
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.interaction.interact_menu as im
from bartlebot.nodes import node_helpers
from tests.conftest import FakeClient, FakeMachine

_MENU_PATH = ["WorldView", "main"]

_PARAMS = {
    "zone": "TestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
    "menu_path": _MENU_PATH,
    "menu_timeout": 0.1,
}


def _ready_client() -> FakeClient:
    """Client with no stale prompt and interact prompt showing immediately at interact_xyz."""
    c = FakeClient()
    c.is_in_npc_range.side_effect = chain([False], repeat(True))
    return c


async def test_success_menu_appears():
    """Happy path: prompt appears, X pressed, menu visible → SUCCESS."""
    client = _ready_client()

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_menu_never_appears_fails():
    """Menu path never becomes visible → FAILURE."""
    client = _ready_client()

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=False)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_prompt_never_appears_fails():
    """NPC prompt never shows after MAX_RETRIES → FAILURE; menu check never reached."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert client.teleport.call_count == im.MAX_RETRIES * 2


async def test_wrong_zone_fails():
    """Pre-condition failure: client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_one_client_wrong_zone_fails():
    """Pre-condition failure: one of two clients is in the wrong zone."""
    good = FakeClient()
    bad = FakeClient()
    bad.zone_name.return_value = "WrongZone"

    result = await im.run(FakeMachine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    good.teleport.assert_not_called()
    bad.teleport.assert_not_called()


async def test_stale_prompt_cleared():
    """A pre-existing prompt is dismissed via reset_xyz before the interaction loop."""
    client = FakeClient()
    call_count = 0

    async def _npc_range():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True  # stale prompt on entry
        if call_count == 2:
            return False  # cleared after teleport to reset_xyz
        return True  # correct prompt at interact_xyz

    client.is_in_npc_range.side_effect = _npc_range

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    first_tp, second_tp = client.teleport.call_args_list[:2]
    assert first_tp.args[0].x == 1.0  # reset_xyz
    assert second_tp.args[0].x == 0.0  # interact_xyz


async def test_prompt_retry_then_success():
    """Prompt times out on first attempt; appears after reset on second attempt → SUCCESS."""
    client = FakeClient()
    reset_visited: list[bool] = []

    async def tracking_teleport(xyz):
        if xyz.x == 1.0:
            reset_visited.append(True)

    client.teleport.side_effect = tracking_teleport

    async def npc_range():
        return bool(reset_visited)

    client.is_in_npc_range.side_effect = npc_range

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 1
    assert client.teleport.call_count == 3  # interact, reset, interact


async def test_interact_clients_subset():
    """Only the specified client interacts; the other is not touched."""
    c1 = FakeClient()
    c2 = _ready_client()
    params = {**_PARAMS, "interact_clients": [2]}

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await im.run(FakeMachine(), [c1, c2], params)

    assert result == "SUCCESS"
    c1.teleport.assert_not_called()
    c1.send_key.assert_not_called()
    c2.teleport.assert_called()
    from wizwalker import Keycode

    assert any(call.args[0] == Keycode.X for call in c2.send_key.call_args_list)


async def test_all_clients_filtered_succeeds():
    """interact_clients resolves to empty → SUCCESS immediately, no interaction."""
    client = FakeClient()
    params = {**_PARAMS, "interact_clients": [5]}

    result = await im.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()
    client.send_key.assert_not_called()


async def test_multiple_clients_both_must_see_menu():
    """Both active clients must have menu_path visible before SUCCESS."""
    c1 = _ready_client()
    c2 = _ready_client()
    seen: list[object] = []

    async def _visible(client, path):
        seen.append(client)
        return len(seen) >= 2

    with (
        patch.object(im, "POLL_INTERVAL", 0),
        patch.object(node_helpers, "is_visible_by_path", side_effect=_visible),
    ):
        result = await im.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    assert len(seen) >= 2
