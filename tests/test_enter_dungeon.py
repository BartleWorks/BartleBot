"""Unit tests for the enter_dungeon node."""

import asyncio
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.action.enter_dungeon as ed
import bartlebot.nodes.node_helpers as nh
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {
    "from_zone": "Hub",
    "dungeon_zone": "Dungeon",
    "sigil_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}


def _happy_client() -> FakeClient:
    """Client that starts in Hub and enters Dungeon."""
    c = FakeClient()
    # pre-check; zone-change poll1 (still in Hub); zone-change poll2 (left Hub); post-check
    c.zone_name.side_effect = ["Hub", "Hub", "Dungeon", "Dungeon"]
    return c


async def test_wrong_source_zone_fails():
    """Pre-condition failure: client is not in from_zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_success_single_client():
    """Happy path: one client teleports to sigil, prompt appears, enters dungeon."""
    client = _happy_client()

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()  # only sigil_xyz; no reset needed
    client.send_key.assert_called()


async def test_success_multiple_clients():
    """All clients must go through the full flow — verified with two clients."""
    clients = [_happy_client(), _happy_client()]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.teleport.assert_called_once()


async def test_one_client_stuck_in_source_zone_fails():
    """Post-condition failure: client A enters the dungeon, client B ends up back in Hub."""
    # Client A: enters successfully
    client_a = FakeClient()
    client_a.zone_name.side_effect = ["Hub", "Hub", "Dungeon"]

    # Client B: appears to leave Hub during zone-change wait, but post-check shows Hub
    client_b = FakeClient()
    client_b.zone_name.side_effect = ["Hub", "Hub", "Dungeon"]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=["Dungeon", "Hub"])),
    ):
        result = await ed.run(FakeMachine(), [client_a, client_b], _PARAMS)

    assert result == "FAILURE"


async def test_prompt_never_appears_retries_exhausted_fails():
    """FAILURE when the entrance prompt never shows after MAX_RETRIES attempts."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"
    # Entrance prompt never appears — FakeClient root_window has no children, so
    # is_visible_by_path → False → all_at_dungeon_entrance → False.

    with patch.object(ed, "POLL_INTERVAL", 0), patch.object(ed, "INTERACT_TIMEOUT", 0.01):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # Sigil teleport attempted MAX_RETRIES times; reset teleport after each failed attempt
    assert client.teleport.call_count == ed.MAX_RETRIES * 2


async def test_prompt_retry_then_success():
    """Prompt missing on first attempt (timeout fires); appears on second attempt after reset."""
    client = FakeClient()
    client.zone_name.side_effect = ["Hub", "Hub", "Dungeon", "Dungeon"]

    # Track whether the reset teleport has happened yet.
    # Entrance prompt returns False until reset_xyz is visited, then True.
    reset_visited: list[bool] = []

    async def tracking_teleport(xyz):
        if xyz.x == 1.0:  # reset_xyz has x=1.0, sigil_xyz has x=0.0
            reset_visited.append(True)

    client.teleport.side_effect = tracking_teleport

    async def entrance_prompt(clients_list):
        return bool(reset_visited)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(ed, "INTERACT_TIMEOUT", 0.01),
        patch.object(nh, "all_at_dungeon_entrance", side_effect=entrance_prompt),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 1  # exactly one reset before success
    # teleport(sigil) attempt 1, teleport(reset), teleport(sigil) attempt 2
    assert client.teleport.call_count == 3


async def test_loading_screen_awaited():
    """Node waits for loading screen to clear before checking the final zone."""
    client = _happy_client()
    client.is_loading.side_effect = [True, False]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.is_loading.call_count >= 2


async def test_wrong_dungeon_zone_fails():
    """Post-condition failure: zone changes but clients land in the wrong zone."""
    client = FakeClient()
    # pre-check; zone-change poll1 (still Hub); zone-change poll2 (left Hub)
    client.zone_name.side_effect = ["Hub", "Hub", "WrongZone"]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_dungeon_warning_dialog_confirmed():
    """ENTER is pressed when the dungeon warning dialog appears after pressing X."""
    client = _happy_client()

    # is_visible_by_path returns True for all paths (including the entrance pre-check in
    # step 2), so also patch none_at_dungeon_entrance to True so that the stale-prompt
    # wait resolves immediately instead of spinning until INTERACT_TIMEOUT.
    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "none_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    # X for sigil interaction, ENTER for the warning dialog
    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls
    assert Keycode.ENTER in calls


async def test_dungeon_warning_dialog_not_present_no_enter():
    """ENTER is NOT pressed when the dungeon warning dialog does not appear."""
    client = _happy_client()

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False)),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls
    assert Keycode.ENTER not in calls


async def test_dungeon_warning_dialog_appears_late():
    """ENTER is pressed even when the dungeon warning dialog appears near the end of the safety window."""
    client = _happy_client()
    call_count = 0

    async def late_visible(*args):
        nonlocal call_count
        call_count += 1
        # Each call takes real time so elapsed accumulates; dialog appears on 3rd poll.
        # Calls: 1 = step-2 entrance pre-check (False), 2-3 = warning dialog polls (True on 3).
        await asyncio.sleep(0.007)
        return call_count == 3

    machine = FakeMachine()
    machine.get_option = lambda name, default=None: 0.1 if name == "Safety Delay" else 0

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=late_visible)),
    ):
        result = await ed.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls
    assert Keycode.ENTER in calls
