"""Unit tests for the enter_dungeon node."""

import asyncio
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.zone_entry.enter_dungeon as ed
from bartlebot.nodes.paths import (
    ENTER_TEAMUP_BUTTON_PATH,
    MESSAGE_BOX_CENTER,
    TEAMUP_2_PLAYER_CHECKBOX,
    TEAMUP_3_PLAYER_CHECKBOX,
    TEAMUP_4_PLAYER_CHECKBOX,
)
from tests.conftest import FakeClient, FakeMachine


async def _center_button_visible(_client, path):
    """Only the dungeon warning's confirm button is showing."""
    return path == MESSAGE_BOX_CENTER


_PARAMS = {
    "from_zone": "Hub",
    "dungeon_zone": "Dungeon",
    "sigil_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}

# Challenge Mode is the author's statement that this dungeon has one; the option is
# only the user's answer to it, so every Challenge Mode test needs both. Team-Up is the
# same shape, and its option carries the team size the queue is asked for.
_CHALLENGE_PARAMS = {**_PARAMS, "offers_challenge_mode": True}
_TEAMUP_PARAMS = {**_PARAMS, ed.PARAM_OFFERS_TEAM_UP: True}
_TEAMUP_ON = {ed.OPTION_TEAM_UP: True}


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
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=["Dungeon", "Hub"])),
    ):
        result = await ed.run(FakeMachine(), [client_a, client_b], _PARAMS)

    assert result == "FAILURE"


async def test_stuck_client_reaches_recovery_aware_arrival_check_quickly():
    """A client whose transition never fires after the sigil is bounded by Safety Delay, not
    the full zone-change timeout, before falling through to the recovery-aware arrival check —
    and is not failed outright just because it never left from_zone."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"  # never leaves

    machine = FakeMachine(options={"Safety Delay": 0.01})

    loop = asyncio.get_event_loop()
    start = loop.time()
    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        # Deliberately huge — if the leave-zone wait still used this instead of Safety
        # Delay, the test would hang for (close to) this long.
        patch.object(ed, "ZONE_CHANGE_TIMEOUT", 100.0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=None)) as recovery,
    ):
        result = await ed.run(machine, [client], _PARAMS)
    elapsed = loop.time() - start

    assert result == "SUCCESS"
    recovery.assert_awaited_once()
    assert elapsed < 1.0


async def test_prompt_never_appears_retries_exhausted_fails():
    """FAILURE when the entrance prompt never shows after MAX_RETRIES attempts."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"
    # Entrance prompt never appears — FakeClient root_window has no children, so
    # is_visible_by_path → False → all_at_dungeon_entrance → False.

    with patch.object(ed, "POLL_INTERVAL", 0):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # Sigil teleport attempted MAX_RETRIES times; reset teleport after each failed attempt
    assert client.teleport.call_count == nh.MAX_RETRIES * 2


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
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_dungeon_warning_dialog_confirmed():
    """The dungeon warning dialog is clicked when it appears after pressing X."""
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "none_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=_center_button_visible)),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    # X for sigil interaction; no ENTER since we now click the button
    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls
    assert click_by_path_mock.called


async def test_dungeon_warning_dialog_not_present_no_enter():
    """The warning dialog button is not clicked when the dialog does not appear."""
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=False)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls


async def test_dungeon_warning_dialog_appears_late():
    """The dungeon warning dialog button is clicked even when it appears near the end of the safety window."""
    client = _happy_client()
    polls = 0

    async def late_visible(_c, path):
        nonlocal polls
        if path != MESSAGE_BOX_CENTER:
            return False
        polls += 1
        # Each poll takes real time so elapsed accumulates; dialog shows on the 3rd.
        # Three of these have to fit inside the Safety Delay below, and that is a race a
        # loaded machine can lose — it once got one poll into a 0.05s window. Keep the
        # window an order of magnitude wider than the polls nominally cost.
        await asyncio.sleep(0.015)
        return polls >= 3

    machine = FakeMachine(options={"Safety Delay": 0.5})
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=late_visible)),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    from wizwalker import Keycode

    calls = [call.args[0] for call in client.send_key.call_args_list]
    assert Keycode.X in calls
    assert polls >= 3
    click_by_path_mock.assert_awaited_with(client, MESSAGE_BOX_CENTER)


# ---------------------------------------------------------------------------
# Challenge Mode
# ---------------------------------------------------------------------------


async def test_challenge_mode_toggles_until_title_says_challenge():
    """The sigil mode toggle is clicked until the prompt title reads Challenge, then X is pressed."""
    client = _happy_client()
    titles = ["Dungeon Entrance", "Dungeon Entrance", "Challenge Mode Entrance"]

    async def title_text(_c, path):
        if path != ed.SIGIL_ENTRANCE_TITLE_PATH:
            return ""
        return titles.pop(0) if len(titles) > 1 else titles[0]

    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=title_text)),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(options={"Challenge Mode": True}), [client], _CHALLENGE_PARAMS)

    assert result == "SUCCESS"
    toggles = [c for c in click_by_path_mock.call_args_list if c.args[1] == ed.SIGIL_MODE_TOGGLE_BUTTON_PATH]
    assert toggles, "sigil mode toggle was never clicked"
    assert Keycode.X in [call.args[0] for call in client.send_key.call_args_list]


async def test_challenge_mode_already_set_does_not_toggle():
    """A sigil already in Challenge Mode is left alone."""
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="<center>Challenge Mode Entrance")),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(options={"Challenge Mode": True}), [client], _CHALLENGE_PARAMS)

    assert result == "SUCCESS"
    toggles = [c for c in click_by_path_mock.call_args_list if c.args[1] == ed.SIGIL_MODE_TOGGLE_BUTTON_PATH]
    assert toggles == []


async def test_challenge_mode_missing_toggle_button_fails():
    """A dungeon with no mode toggle fails rather than entering in normal mode."""
    client = _happy_client()

    async def click(_c, path):
        return path != ed.SIGIL_MODE_TOGGLE_BUTTON_PATH

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="Dungeon Entrance")),
        patch.object(nh, "click_by_path", new=AsyncMock(side_effect=click)),
    ):
        result = await ed.run(FakeMachine(options={"Challenge Mode": True}), [client], _CHALLENGE_PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


async def test_challenge_mode_toggle_never_takes_fails():
    """Clicking a toggle that never changes the title exhausts MAX_MODE_TOGGLES and fails."""
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="Dungeon Entrance")),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(options={"Challenge Mode": True}), [client], _CHALLENGE_PARAMS)

    assert result == "FAILURE"
    toggles = [c for c in click_by_path_mock.call_args_list if c.args[1] == ed.SIGIL_MODE_TOGGLE_BUTTON_PATH]
    assert len(toggles) == ed.MAX_MODE_TOGGLES


async def test_challenge_mode_off_never_reads_or_toggles_the_sigil_mode():
    """With the option off, the mode toggle is not touched at all."""
    client = _happy_client()
    get_text_mock = AsyncMock(return_value="")
    click_by_path_mock = AsyncMock(return_value=False)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", get_text_mock),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    toggles = [c for c in click_by_path_mock.call_args_list if c.args[1] == ed.SIGIL_MODE_TOGGLE_BUTTON_PATH]
    assert toggles == []


async def test_the_option_alone_does_not_enter_challenge_mode():
    """A user override outliving the param -- or set on a bot that never offered it --
    must not send a client at a sigil toggle that is not there."""
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="Dungeon Entrance")),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(options={"Challenge Mode": True}), [client], _PARAMS)

    assert result == "SUCCESS"
    toggles = [c for c in click_by_path_mock.call_args_list if c.args[1] == ed.SIGIL_MODE_TOGGLE_BUTTON_PATH]
    assert toggles == []


# ---------------------------------------------------------------------------
# Team-Up
#
# The same node, past the point where the entrance prompt is showing. Every test
# here needs both halves of the switch: the author's ``offers_team_up`` param and
# the user's ``Team-Up`` option.
# ---------------------------------------------------------------------------


def _teamup_machine(**options) -> FakeMachine:
    return FakeMachine(options={**_TEAMUP_ON, **options})


async def _visible_for_teamup_window_only(_client, path):
    """True only for the team-up confirmation window; False for the stale-prompt check."""
    return path == ENTER_TEAMUP_BUTTON_PATH


async def test_teamup_success_single_client():
    """Happy path: one client queues at the sigil and lands in the dungeon."""
    client = _happy_client()

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(_teamup_machine(), [client], _TEAMUP_PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()  # only sigil_xyz; no reset needed


async def test_teamup_success_multiple_clients():
    """All clients queue independently — verified with two."""
    clients = [_happy_client(), _happy_client()]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(_teamup_machine(), clients, _TEAMUP_PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.teleport.assert_called_once()


async def test_teamup_window_never_appears_fails():
    """FAILURE when the team-up confirmation window never opens."""
    client = _happy_client()

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False)),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(_teamup_machine(), [client], _TEAMUP_PARAMS)

    assert result == "FAILURE"


async def _clicked_paths_for(team_size):
    """Run a team-up entry with *team_size* and return every path that was clicked."""
    client = _happy_client()
    clicked: list = []

    async def record_click(_c, path):
        clicked.append(path)
        return True

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", side_effect=record_click),
    ):
        result = await ed.run(_teamup_machine(**{ed.OPTION_MINIMUM_TEAM_SIZE: team_size}), [client], _TEAMUP_PARAMS)

    assert result == "SUCCESS"
    return clicked


async def test_team_size_2_checkbox_clicked():
    """The 2-player checkbox is ticked when Minimum Team Size is 2."""
    clicked = await _clicked_paths_for(2)

    assert TEAMUP_2_PLAYER_CHECKBOX in clicked


async def test_team_size_3_checkbox_clicked():
    """The 3-player checkbox is ticked when Minimum Team Size is 3, and only that one."""
    clicked = await _clicked_paths_for(3)

    assert TEAMUP_3_PLAYER_CHECKBOX in clicked
    assert TEAMUP_2_PLAYER_CHECKBOX not in clicked


async def test_team_size_4_checkbox_clicked():
    """The 4-player checkbox is ticked when Minimum Team Size is 4, and only that one."""
    clicked = await _clicked_paths_for(4)

    assert TEAMUP_4_PLAYER_CHECKBOX in clicked
    assert TEAMUP_2_PLAYER_CHECKBOX not in clicked


async def test_enter_teamup_button_clicked():
    """The queue is actually joined, not merely configured."""
    clicked = await _clicked_paths_for(2)

    assert ENTER_TEAMUP_BUTTON_PATH in clicked


async def test_invalid_team_size_fails_without_queueing():
    """An out-of-range team size fails rather than queueing with whatever is ticked.

    The option is bounded at 2–4 in its spec, so this is a hand-edited settings file or
    bot file rather than anything the UI can produce — which is the case ``minimum`` and
    ``maximum`` cannot catch on their own.
    """
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(_teamup_machine(**{ed.OPTION_MINIMUM_TEAM_SIZE: 5}), [client], _TEAMUP_PARAMS)

    assert result == "FAILURE"
    click_by_path_mock.assert_not_awaited()


async def test_antiafk_sent_during_queue_wait():
    """The anti-AFK keypress fires while the queue is still matching."""
    client = FakeClient()
    call_count = 0

    async def zone_sequence():
        nonlocal call_count
        call_count += 1
        # First call is the pre-check; the rest are the queue wait loop. Stay in Hub
        # long enough for the AFK timer to fire, then leave.
        return "Hub" if call_count <= 3 else "Dungeon"

    client.zone_name.side_effect = zone_sequence

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(ed, "AFK_INTERVAL", 0.0),  # trigger immediately
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=None)),
    ):
        result = await ed.run(_teamup_machine(), [client], _TEAMUP_PARAMS)

    assert result == "SUCCESS"
    a_key_calls = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.A]
    assert len(a_key_calls) >= 1


async def test_teamup_queue_timeout_fails():
    """FAILURE when the queue never matches within TEAMUP_QUEUE_TIMEOUT."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(ed, "TEAMUP_QUEUE_TIMEOUT", 0.01),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(_teamup_machine(), [client], _TEAMUP_PARAMS)

    assert result == "FAILURE"


async def test_teamup_loading_screen_awaited():
    """The loading screen is waited out before the final zone is checked."""
    client = _happy_client()
    client.is_loading.side_effect = [True, False]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(_teamup_machine(), [client], _TEAMUP_PARAMS)

    assert result == "SUCCESS"
    assert client.is_loading.call_count >= 2


async def test_teamup_wrong_destination_zone_fails():
    """Post-condition failure: the queue matched, but into the wrong zone."""
    client = FakeClient()
    client.zone_name.side_effect = ["Hub", "Hub", "WrongZone"]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "is_visible_by_path", side_effect=_visible_for_teamup_window_only),
        patch.object(nh, "click_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await ed.run(_teamup_machine(), [client], _TEAMUP_PARAMS)

    assert result == "FAILURE"


async def test_the_option_alone_does_not_queue():
    """The mirror of the Challenge Mode guard: a leftover override on a bot whose author
    never said the sigil offers Team-Up enters normally, rather than clicking a Team-Up
    button that is not there."""
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(_teamup_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert Keycode.X in [call.args[0] for call in client.send_key.call_args_list]
    teamup_clicks = [c for c in click_by_path_mock.call_args_list if c.args[1] == ENTER_TEAMUP_BUTTON_PATH]
    assert teamup_clicks == []


async def test_the_param_alone_does_not_queue():
    """And the other way round: a sigil that offers Team-Up still enters normally until
    the user turns the option on. Team-Up is opt-in, never the default for a bot that
    merely happens to be able to."""
    client = _happy_client()
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(), [client], _TEAMUP_PARAMS)

    assert result == "SUCCESS"
    assert Keycode.X in [call.args[0] for call in client.send_key.call_args_list]
    teamup_clicks = [c for c in click_by_path_mock.call_args_list if c.args[1] == ENTER_TEAMUP_BUTTON_PATH]
    assert teamup_clicks == []


# ---------------------------------------------------------------------------
# One realm, one instance (#130)
#
# A Together bot enters as one party. The steps around the two checks are stubbed, so what
# is under test is what each check decides.
# ---------------------------------------------------------------------------


async def _run_checks(together_outcomes, *, all_seen=True):
    ensure = AsyncMock(side_effect=list(together_outcomes))
    reach = AsyncMock(return_value=True)
    enter = AsyncMock(return_value=True)
    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(ed, "SIGHTING_SETTLE_TIMEOUT", 0.01),
        patch.object(nh, "ensure_together", ensure),
        patch.object(nh, "reach_sigil_prompt", reach),
        patch.object(ed, "_enter_normal", enter),
        patch.object(nh, "wait_for_zone_with_recovery", AsyncMock(return_value=None)),
        patch.object(ed, "_all_seen", AsyncMock(return_value=all_seen)),
    ):
        result = await ed.run(FakeMachine(), [FakeClient(), FakeClient()], _PARAMS)
    return result, ensure, reach, enter


async def test_players_together_on_the_sigil_enter_without_a_port():
    result, ensure, reach, _enter = await _run_checks([nh.TogetherOutcome.ALREADY])
    assert result == "SUCCESS"
    assert ensure.await_args.kwargs["where"] == "on the sigil"
    reach.assert_awaited_once()


async def test_players_gathered_on_the_sigil_reach_the_prompt_again():
    result, _ensure, reach, _enter = await _run_checks([nh.TogetherOutcome.GATHERED])
    assert result == "SUCCESS"
    assert reach.await_count == 2


async def test_players_who_cannot_be_gathered_on_the_sigil_never_press_x():
    result, _ensure, _reach, enter = await _run_checks([nh.TogetherOutcome.FAILED])
    assert result == "FAILURE"
    enter.assert_not_awaited()


async def test_players_in_separate_instances_are_ported_together():
    result, ensure, _reach, _enter = await _run_checks(
        [nh.TogetherOutcome.ALREADY, nh.TogetherOutcome.GATHERED], all_seen=False
    )
    assert result == "SUCCESS"
    assert ensure.await_args.kwargs["where"] == "in the dungeon"


async def test_players_left_in_separate_instances_fail():
    result, _ensure, _reach, _enter = await _run_checks(
        [nh.TogetherOutcome.ALREADY, nh.TogetherOutcome.FAILED], all_seen=False
    )
    assert result == "FAILURE"


async def test_a_separate_bot_counts_as_together_in_the_dungeon():
    machine = FakeMachine(options={"Player Realms": "Separate"})
    a, b = FakeClient(), FakeClient()
    assert await ed._all_seen(machine, [a, b])
    a.get_base_entity_list.assert_not_awaited()
