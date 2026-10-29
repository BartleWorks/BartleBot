"""Unit tests for the enter_dungeon node."""

import asyncio
import contextlib
import itertools
from unittest.mock import AsyncMock, patch

from bartleby.runtime.paths import (
    ENTER_TEAMUP_BUTTON_PATH,
    MESSAGE_BOX_CENTER,
    TEAMUP_2_PLAYER_CHECKBOX,
    TEAMUP_3_PLAYER_CHECKBOX,
    TEAMUP_4_PLAYER_CHECKBOX,
    TEAMUP_CONFIRMATION_WINDOW_PATH,
    TEAMUP_QUEUED_PATH,
)
from wizwalker import Keycode

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.zone_entry.enter_dungeon as ed
from tests.conftest import FakeClient, FakeGidEntity, FakeMachine
from tests.node_helper_patches import patch_helper


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
    # Reset first, whether or not a prompt showed (#152), then the one teleport to the sigil.
    assert [call.args[0].x for call in client.teleport.call_args_list] == [1.0, 0.0]
    client.send_key.assert_called()


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = FakeClient()
    client.zone_name.side_effect = [None, "Hub", "Hub", "Dungeon", "Dungeon"]

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


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
        assert [call.args[0].x for call in c.teleport.call_args_list] == [1.0, 0.0]


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
    # The first reset, then a sigil teleport and a reset teleport for each failed attempt
    assert client.teleport.call_count == 1 + nh.MAX_RETRIES * 2


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
        # Once the client has been reset twice: the reset every run starts with, and the
        # one after the first miss.
        return len(reset_visited) >= 2

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", side_effect=entrance_prompt),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 2  # the first reset, and one after the miss
    # teleport(reset), teleport(sigil) attempt 1, teleport(reset), teleport(sigil) attempt 2
    assert client.teleport.call_count == 4


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
        patch_helper("is_visible_by_path", new=AsyncMock(side_effect=_center_button_visible)),
        patch_helper("click_by_path", click_by_path_mock),
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
        patch_helper("click_by_path", click_by_path_mock),
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
        patch_helper("POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch_helper("is_visible_by_path", new=AsyncMock(side_effect=late_visible)),
        patch_helper("click_by_path", click_by_path_mock),
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
        patch_helper("POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch_helper("get_text_by_path", new=AsyncMock(side_effect=title_text)),
        patch_helper("click_by_path", click_by_path_mock),
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
        patch_helper("POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="<center>Challenge Mode Entrance")),
        patch_helper("click_by_path", click_by_path_mock),
    ):
        result = await ed.run(FakeMachine(options={"Challenge Mode": True}), [client], _CHALLENGE_PARAMS)

    assert result == "SUCCESS"
    toggles = [c for c in click_by_path_mock.call_args_list if c.args[1] == ed.SIGIL_MODE_TOGGLE_BUTTON_PATH]
    assert toggles == []


async def test_challenge_mode_arrives_in_the_copy_of_the_recorded_dungeon_zone():
    """The copy is its own zone, and the bot records the ordinary one (#143)."""
    client = FakeClient()
    client.zone_name.side_effect = itertools.chain(
        ["Aquila/AQ_Z00_Hub"] * 2, itertools.repeat("Aquila/AQ_Z02_Atlantea_Hard")
    )
    params = {**_CHALLENGE_PARAMS, "from_zone": "Aquila/AQ_Z00_Hub", "dungeon_zone": "Aquila/AQ_Z02_Atlantea"}

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch_helper("POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="Challenge Mode Entrance")),
    ):
        result = await ed.run(FakeMachine(options={"Challenge Mode": True}), [client], params)

    assert result == "SUCCESS"


async def test_challenge_mode_missing_toggle_button_fails():
    """A dungeon with no mode toggle fails rather than entering in normal mode."""
    client = _happy_client()

    async def click(_c, path):
        return path != ed.SIGIL_MODE_TOGGLE_BUTTON_PATH

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch_helper("POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="Dungeon Entrance")),
        patch_helper("click_by_path", new=AsyncMock(side_effect=click)),
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
        patch_helper("POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="Dungeon Entrance")),
        patch_helper("click_by_path", click_by_path_mock),
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
        patch_helper("get_text_by_path", get_text_mock),
        patch_helper("click_by_path", click_by_path_mock),
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
        patch_helper("POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="Dungeon Entrance")),
        patch_helper("click_by_path", click_by_path_mock),
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


class _TeamUpScreen:
    """The sigil's Team-Up flow as the node sees it, for any number of clients.

    Team-Up opens the confirmation window, and an Enter click that lands closes it; the
    first `dropped` per client are lost. The player then either waits in the queue, with
    the HUD's label showing, until the dungeon loads after `queue_reads` zone reads, or,
    as the last player to make a team, is in the dungeon at once (`instant`). With
    `ignored`, a landed click closes the window and nothing else happens. The dungeon is
    `lands_in`, and `loads` puts one loading screen in front of it.

    Like every control, the Enter button keeps reading visible once its window has closed.
    """

    def __init__(
        self,
        *,
        window_appears=True,
        instant=False,
        dropped=0,
        queue_reads=2,
        ignored=False,
        lands_in="Dungeon",
        loads=False,
    ):
        self.window_appears = window_appears
        self.lands_in = lands_in
        self.loads = loads
        self.instant = instant
        self.dropped = dropped
        self.queue_reads = queue_reads
        self.ignored = ignored
        self.state: dict[FakeClient, dict] = {}
        self.clicked: list = []

    def client(self) -> FakeClient:
        c = FakeClient()
        self.state[c] = {
            "window": False,
            "opened": False,
            "queued": False,
            "zone": "Hub",
            "reads": 0,
            "dropped": self.dropped,
            "loading": False,
        }
        c.zone_name.side_effect = lambda: self._zone(c)
        c.is_loading.side_effect = lambda: self._loading(c)
        return c

    def _arrive(self, st) -> None:
        st["queued"], st["zone"], st["loading"] = False, self.lands_in, self.loads

    def _loading(self, client) -> bool:
        st = self.state[client]
        loading, st["loading"] = st["loading"], False
        return loading

    def _zone(self, client) -> str:
        st = self.state[client]
        if st["queued"]:
            st["reads"] += 1
            if st["reads"] > self.queue_reads:
                self._arrive(st)
        return st["zone"]

    async def visible(self, client, path):
        st = self.state[client]
        if path == ENTER_TEAMUP_BUTTON_PATH:
            return st["opened"]
        if path == TEAMUP_CONFIRMATION_WINDOW_PATH:
            return st["window"]
        if path == TEAMUP_QUEUED_PATH:
            return st["queued"]
        return False  # no stale prompt

    async def click(self, client, path):
        st = self.state[client]
        self.clicked.append((client, path, st["window"]))
        if path == ed.DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH and self.window_appears:
            st["window"] = st["opened"] = True
        elif path == ENTER_TEAMUP_BUTTON_PATH and st["window"]:
            if st["dropped"]:
                st["dropped"] -= 1
            else:
                st["window"] = False
                if self.ignored:
                    pass
                elif self.instant:
                    self._arrive(st)
                else:
                    st["queued"] = True
        return True

    async def run(self, clients, machine=None, patches=(), **options) -> str:
        with contextlib.ExitStack() as stack:
            for p in (
                patch.object(ed, "POLL_INTERVAL", 0),
                patch_helper("WINDOW_CLOSE_RETRY_INTERVAL", 0.01),
                patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
                patch_helper("is_visible_by_path", side_effect=self.visible),
                patch_helper("click_by_path", side_effect=self.click),
                *patches,
            ):
                stack.enter_context(p)
            return await ed.run(machine or _teamup_machine(**options), clients, _TEAMUP_PARAMS)

    def paths_clicked(self) -> list:
        return [path for _c, path, _window in self.clicked]


async def test_teamup_success_single_client():
    """Happy path: one client queues at the sigil and lands in the dungeon."""
    screen = _TeamUpScreen()
    client = screen.client()

    assert await screen.run([client]) == "SUCCESS"
    # Reset first, whether or not a prompt showed (#152), then the one teleport to the sigil.
    assert [call.args[0].x for call in client.teleport.call_args_list] == [1.0, 0.0]


async def test_teamup_success_multiple_clients():
    """All clients queue independently — verified with two."""
    screen = _TeamUpScreen()
    clients = [screen.client(), screen.client()]

    assert await screen.run(clients) == "SUCCESS"
    for c in clients:
        assert [call.args[0].x for call in c.teleport.call_args_list] == [1.0, 0.0]


async def test_teamup_window_never_appears_fails():
    """FAILURE when the team-up confirmation window never opens."""
    screen = _TeamUpScreen(window_appears=False)

    assert await screen.run([screen.client()]) == "FAILURE"


async def _clicked_paths_for(team_size):
    """Run a team-up entry with *team_size* and return every path that was clicked."""
    screen = _TeamUpScreen()

    assert await screen.run([screen.client()], **{ed.OPTION_MINIMUM_TEAM_SIZE: team_size}) == "SUCCESS"
    return screen.paths_clicked()


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

    assert clicked.count(ENTER_TEAMUP_BUTTON_PATH) == 1


async def test_a_dropped_enter_click_is_sent_again_until_the_player_is_queued():
    """One dropped click used to leave the node waiting an hour in a queue nobody had joined."""
    screen = _TeamUpScreen(dropped=2)

    assert await screen.run([screen.client()]) == "SUCCESS"
    assert screen.paths_clicked().count(ENTER_TEAMUP_BUTTON_PATH) == 3


async def test_the_last_player_to_make_a_team_goes_straight_in():
    """The final joiner never waits in the queue, so its label never shows."""
    screen = _TeamUpScreen(instant=True)

    assert await screen.run([screen.client()]) == "SUCCESS"
    assert screen.paths_clicked().count(ENTER_TEAMUP_BUTTON_PATH) == 1


async def test_enter_is_never_clicked_once_the_window_has_gone():
    """Its button still reads visible then, and a click on it would land on the world."""
    screen = _TeamUpScreen(dropped=1)

    await screen.run([screen.client()])

    assert all(window for _c, path, window in screen.clicked if path == ENTER_TEAMUP_BUTTON_PATH)


async def test_a_player_who_never_joins_fails_without_waiting_out_the_queue():
    machine = _teamup_machine()
    screen = _TeamUpScreen(ignored=True)

    assert await screen.run([screen.client()], machine=machine) == "FAILURE"
    assert any("did not join the Team-Up queue" in msg for msg, _level in machine.logs)
    assert not any("Waiting for team-up zone change" in msg for msg, _level in machine.logs)


async def test_invalid_team_size_fails_without_queueing():
    """An out-of-range team size fails rather than queueing with whatever is ticked.

    The option is bounded at 2–4 in its spec, so this is a hand-edited settings file or
    bot file rather than anything the UI can produce — which is the case ``minimum`` and
    ``maximum`` cannot catch on their own.
    """
    screen = _TeamUpScreen()

    assert await screen.run([screen.client()], **{ed.OPTION_MINIMUM_TEAM_SIZE: 5}) == "FAILURE"
    assert screen.clicked == []


async def test_antiafk_sent_during_queue_wait():
    """The anti-AFK keypress fires while the queue is still matching."""
    screen = _TeamUpScreen(queue_reads=3)
    client = screen.client()

    result = await screen.run([client], patches=(patch.object(ed, "AFK_INTERVAL", 0.0),))

    assert result == "SUCCESS"
    a_key_calls = [c for c in client.send_key.call_args_list if c.args[0] == Keycode.A]
    assert len(a_key_calls) >= 1


async def test_teamup_queue_timeout_fails():
    """FAILURE when the queue never matches within TEAMUP_QUEUE_TIMEOUT."""
    screen = _TeamUpScreen(queue_reads=float("inf"))

    result = await screen.run([screen.client()], patches=(patch.object(ed, "TEAMUP_QUEUE_TIMEOUT", 0.01),))

    assert result == "FAILURE"


async def test_teamup_loading_screen_awaited():
    """The loading screen is waited out before the final zone is checked."""
    screen = _TeamUpScreen(loads=True)
    client = screen.client()

    assert await screen.run([client]) == "SUCCESS"
    assert client.is_loading.call_count >= 2


async def test_teamup_wrong_destination_zone_fails():
    """Post-condition failure: the queue matched, but into the wrong zone."""
    screen = _TeamUpScreen(lands_in="WrongZone")

    result = await screen.run(
        [screen.client()],
        patches=(patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=["WrongZone"])),),
    )

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
        patch_helper("click_by_path", click_by_path_mock),
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
        patch_helper("click_by_path", click_by_path_mock),
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


# ---------------------------------------------------------------------------
# Strangers (#279)
#
# An instance holds only the players who pressed X in one countdown, so anyone loaded there
# who is not ours came in with the team. The node fails, and recovery takes the team out.
# ---------------------------------------------------------------------------


def _stranger_in_the_dungeon(client: FakeClient) -> None:
    client.get_base_entity_list = AsyncMock(return_value=[FakeGidEntity(client.gid), FakeGidEntity(7)])


async def test_a_stranger_who_entered_with_the_team_fails_the_node():
    client = _happy_client()
    _stranger_in_the_dungeon(client)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_a_stranger_through_team_up_is_expected():
    screen = _TeamUpScreen()
    count = AsyncMock(return_value=1)

    result = await screen.run([screen.client()], patches=(patch.object(nh, "count_strangers", new=count),))

    assert result == "SUCCESS"
    count.assert_not_awaited()


async def test_a_sigil_offering_team_up_entered_normally_is_still_checked():
    client = _happy_client()
    _stranger_in_the_dungeon(client)

    with (
        patch.object(ed, "POLL_INTERVAL", 0),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
    ):
        result = await ed.run(FakeMachine(), [client], _TEAMUP_PARAMS)

    assert result == "FAILURE"


async def test_a_separate_bot_counts_as_together_in_the_dungeon():
    machine = FakeMachine(options={"Player Realms": "Separate"})
    a, b = FakeClient(), FakeClient()
    assert await ed._all_seen(machine, [a, b])
    a.get_base_entity_list.assert_not_awaited()
