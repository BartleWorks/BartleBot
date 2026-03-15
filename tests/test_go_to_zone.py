"""Unit tests for the go_to_zone node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.action.go_to_zone as gtz
import bartlebot.nodes.node_helpers as nh

from tests.conftest import FakeClient, FakeMachine

# Minimal params used across all tests.
_PARAMS = {"from_zone": "Src", "to_zone": "Dst", "xyz": {"x": 0.0, "y": 0.0, "z": 0.0}}


async def test_wrong_source_zone_fails():
    """Pre-condition failure: client is not in from_zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"
    machine = FakeMachine()

    result = await gtz.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_success_single_client():
    """Happy path: one client teleports from Src and arrives in Dst."""
    client = FakeClient()
    # step 1 pre-check → "Src"; step 3 first poll → "Src" (still leaving);
    # step 3 second poll → "Dst" (left Src); step 5 post-check → "Dst".
    client.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(machine=FakeMachine(), clients=[client], params=_PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()


async def test_success_multiple_clients():
    """All clients must teleport and arrive — verified with two clients."""
    def _make_client():
        c = FakeClient()
        c.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]
        return c

    clients = [_make_client(), _make_client()]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.teleport.assert_called_once()


async def test_timeout_leaving_source_zone_fails():
    """If clients never leave from_zone the node returns FAILURE after timeout."""
    client = FakeClient()
    client.zone_name.return_value = "Src"  # never changes

    with patch.object(gtz, "ZONE_CHANGE_TIMEOUT", 0.01):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_post_condition_wrong_zone_fails():
    """Clients leave from_zone but land in an unexpected zone → FAILURE."""
    client = FakeClient()
    # step 1 → "Src"; step 3 poll1 → "Src"; step 3 poll2 → "Detour" (left Src);
    # step 5 → "Detour" (not "Dst").
    client.zone_name.side_effect = ["Src", "Src", "Detour", "Detour"]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_loading_screen_awaited():
    """Node waits for is_loading to clear before checking the final zone."""
    client = FakeClient()
    client.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]
    # First loading poll → still loading; second → clear.
    client.is_loading.side_effect = [True, False]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.is_loading.call_count >= 2


async def test_exit_dungeon_dialog_dismissed():
    """When the dungeon exit warning appears after teleport, ENTER is pressed to dismiss it."""
    client = FakeClient()
    client.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # ENTER sent once (to dismiss the dungeon exit dialog)
    from wizwalker import Keycode
    client.send_key.assert_called_once_with(Keycode.ENTER, 0.1)
