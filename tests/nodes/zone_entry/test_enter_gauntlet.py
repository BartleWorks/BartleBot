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


def _on_the_sigil(presses_needed: list[int], clients: list):
    """Clients on a gauntlet sigil, each going in on its nth press of X, and is_visible_by_path to match.

    The entrance prompt shows until a client goes in, and its zone flips from the house to
    the gauntlet then, so the node's own prompt, zone and arrival reads run unpatched.
    """
    needed = {id(c): n for c, n in zip(clients, presses_needed)}

    def entered(client) -> bool:
        return client.send_key.await_count >= needed[id(client)]

    for client in clients:
        client.zone_name.side_effect = lambda c=client: _PARAMS["dungeon_zone"] if entered(c) else "Housing/Dorm"

    async def prompt_up(client, path):
        return list(path) == list(nh.GAUNTLET_ENTRANCE_PATH) and not entered(client)

    return prompt_up


async def test_a_player_whose_press_was_dropped_is_pressed_again_and_goes_in():
    """2026-11-07: one player left on the sigil four times running, and the run was stopped."""
    clients = [FakeClient(), FakeClient()]
    prompt_up = _on_the_sigil([1, 2], clients)
    machine = FakeMachine(options={"Safety Delay": 0.01})

    with patch.object(eg, "POLL_INTERVAL", 0), patch.object(nh, "is_visible_by_path", new=prompt_up):
        result = await eg.run(machine, clients, _PARAMS)

    assert result == "SUCCESS"
    assert [c.send_key.await_count for c in clients] == [1, 2]
    assert any("P2 is still at the prompt; pressing X again" in line[0] for line in machine.logs)


async def test_x_is_pressed_once_when_everyone_goes_in():
    clients = [FakeClient(), FakeClient()]
    prompt_up = _on_the_sigil([1, 1], clients)

    with patch.object(eg, "POLL_INTERVAL", 0), patch.object(nh, "is_visible_by_path", new=prompt_up):
        result = await eg.run(FakeMachine(options={"Safety Delay": 0.01}), clients, _PARAMS)

    assert result == "SUCCESS"
    assert [c.send_key.await_count for c in clients] == [1, 1]
