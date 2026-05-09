"""Unit tests for the enter_gauntlet node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.zone_entry.enter_gauntlet as eg
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {"dungeon_zone": "Housing/Gauntlet/Interior"}


async def test_prompt_never_appears():
    """FAILURE when the gauntlet entrance prompt never shows within Safety Delay."""
    client = FakeClient()

    with (
        patch.object(eg, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=False)),
    ):
        result = await eg.run(FakeMachine(options={"Safety Delay": 0}), [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


async def test_success():
    """Happy path: all clients enter the gauntlet and arrive in dungeon_zone."""
    client = FakeClient()

    with (
        patch.object(eg, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=None)),
    ):
        result = await eg.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.send_key.assert_called()


async def test_wrong_zone_after_entry():
    """FAILURE when clients land in a zone other than dungeon_zone after entering."""
    client = FakeClient()

    with (
        patch.object(eg, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await eg.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_multiple_clients_all_enter():
    """X is sent to every client; all must arrive in dungeon_zone."""
    clients = [FakeClient(), FakeClient()]

    with (
        patch.object(eg, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=None)),
    ):
        result = await eg.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.send_key.assert_called()


async def test_loading_screen_awaited():
    """Node waits for loading screens to clear before checking the final zone."""
    client = FakeClient()
    client.is_loading.side_effect = [True, False]

    with (
        patch.object(eg, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=None)),
    ):
        result = await eg.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.is_loading.call_count >= 2
