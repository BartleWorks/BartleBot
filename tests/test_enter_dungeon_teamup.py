"""Unit tests for the enter_dungeon_teamup node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.zone_entry.enter_dungeon_teamup as edt
from bartlebot.nodes.paths import (
    ENTER_TEAMUP_BUTTON_PATH,
    TEAMUP_2_PLAYER_CHECKBOX,
    TEAMUP_3_PLAYER_CHECKBOX,
    TEAMUP_4_PLAYER_CHECKBOX,
)
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {
    "from_zone": "Hub",
    "dungeon_zone": "Dungeon",
    "sigil_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
    "minimum_team_size": 2,
}


def _happy_client() -> FakeClient:
    """Client that starts in Hub, waits in queue, then enters Dungeon."""
    c = FakeClient()
    # pre-check; zone-change queue poll 1 (still in Hub); poll 2 (left Hub); post-check
    c.zone_name.side_effect = ["Hub", "Hub", "Dungeon", "Dungeon"]
    return c


async def test_wrong_source_zone_fails():
    """Pre-condition failure: client is not in from_zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_invalid_team_size_fails():
    """FAILURE immediately when minimum_team_size is not 2, 3, or 4."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"
    params = {**_PARAMS, "minimum_team_size": 5}

    result = await edt.run(FakeMachine(), [client], params)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def _visible_for_teamup_window_only(c, path):
    """Return True only for the team-up confirmation window check, False for stale-prompt check."""
    return path == ENTER_TEAMUP_BUTTON_PATH


async def test_success_single_client():
    """Happy path: one client queues and enters the dungeon with team size 2."""
    client = _happy_client()

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()  # only sigil_xyz; no reset needed


async def test_success_multiple_clients():
    """All clients flow through independently — verified with two clients."""
    clients = [_happy_client(), _happy_client()]

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await edt.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.teleport.assert_called_once()


async def test_prompt_never_appears_fails():
    """FAILURE when the entrance prompt never shows after MAX_RETRIES attempts."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"

    with patch.object(edt, "POLL_INTERVAL", 0):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # Sigil teleport attempted MAX_RETRIES times; reset teleport after each failed attempt
    assert client.teleport.call_count == edt.MAX_RETRIES * 2


async def test_teamup_window_never_appears_fails():
    """FAILURE when the team-up confirmation window never opens."""
    client = _happy_client()

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        # is_visible_by_path returns False → ENTER_TEAMUP_BUTTON_PATH never visible
        patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False)),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_team_size_2_checkbox_clicked():
    """The 2-player checkbox path is used when minimum_team_size=2."""
    client = _happy_client()
    clicked_paths: list = []

    async def record_click(c, path):
        clicked_paths.append(path)
        return True

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", side_effect=record_click),
    ):
        result = await edt.run(FakeMachine(), [client], {**_PARAMS, "minimum_team_size": 2})

    assert result == "SUCCESS"
    assert TEAMUP_2_PLAYER_CHECKBOX in clicked_paths


async def test_team_size_3_checkbox_clicked():
    """The 3-player checkbox path is used when minimum_team_size=3."""
    client = _happy_client()
    clicked_paths: list = []

    async def record_click(c, path):
        clicked_paths.append(path)
        return True

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", side_effect=record_click),
    ):
        result = await edt.run(FakeMachine(), [client], {**_PARAMS, "minimum_team_size": 3})

    assert result == "SUCCESS"
    assert TEAMUP_3_PLAYER_CHECKBOX in clicked_paths
    assert TEAMUP_2_PLAYER_CHECKBOX not in clicked_paths


async def test_team_size_4_checkbox_clicked():
    """The 4-player checkbox path is used when minimum_team_size=4."""
    client = _happy_client()
    clicked_paths: list = []

    async def record_click(c, path):
        clicked_paths.append(path)
        return True

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", side_effect=record_click),
    ):
        result = await edt.run(FakeMachine(), [client], {**_PARAMS, "minimum_team_size": 4})

    assert result == "SUCCESS"
    assert TEAMUP_4_PLAYER_CHECKBOX in clicked_paths
    assert TEAMUP_2_PLAYER_CHECKBOX not in clicked_paths


async def test_enter_teamup_button_clicked():
    """The ENTER_TEAMUP_BUTTON_PATH is clicked to join the queue."""
    client = _happy_client()
    clicked_paths: list = []

    async def record_click(c, path):
        clicked_paths.append(path)
        return True

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", side_effect=record_click),
    ):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert ENTER_TEAMUP_BUTTON_PATH in clicked_paths


async def test_antifk_sent_during_wait():
    """Anti-AFK A keypress is sent when the AFK interval elapses during queue wait."""
    client = FakeClient()
    call_count = 0

    # Zone sequence: still in Hub for first few polls, then leaves
    async def zone_sequence():
        nonlocal call_count
        call_count += 1
        # First call is the pre-check. Subsequent calls are the queue wait loop.
        # Stay in Hub for a while to let the AFK timer fire, then leave.
        if call_count <= 3:
            return "Hub"
        return "Dungeon"

    client.zone_name.side_effect = zone_sequence

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(edt, "TEAMUP_AFK_INTERVAL", 0.0),  # trigger immediately
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=None)),
    ):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # A keypress should have been sent at least once during the queue wait
    from wizwalker import Keycode

    a_key_calls = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.A]
    assert len(a_key_calls) >= 1


async def test_zone_change_timeout_fails():
    """FAILURE when zone never changes within the queue timeout."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(edt, "TEAMUP_QUEUE_TIMEOUT", 0.01),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_loading_screen_awaited():
    """Node waits for loading screen to clear before verifying the final zone."""
    client = _happy_client()
    client.is_loading.side_effect = [True, False]

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.is_loading.call_count >= 2


async def test_wrong_destination_zone_fails():
    """Post-condition failure: zone changes but clients land in the wrong zone."""
    client = FakeClient()
    client.zone_name.side_effect = ["Hub", "Hub", "WrongZone"]

    with (
        patch.object(edt, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await edt.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
