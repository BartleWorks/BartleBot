"""Unit tests for the enter_zone_via_object node."""

import itertools
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from wizwalker import Keycode

import bartlebot.nodes.zone_entry.enter_zone_via_object as itz
from tests.conftest import FakeClient, FakeMachine, prompt_at
from tests.node_helper_patches import patch_helper

_FROM_ZONE = "TestZone"  # matches FakeClient zone_name default
_TO_ZONE = "TestZone/SnowGlobe"

_PARAMS = {
    "from_zone": _FROM_ZONE,
    "to_zone": _TO_ZONE,
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
}


def _ready_client() -> FakeClient:
    """Client with no stale prompt, whose interact prompt shows at interact_xyz and nowhere else."""
    c = FakeClient()
    prompt_at(c, _PARAMS["interact_xyz"])
    # zone_name: from_zone check → from_zone; final verify → to_zone
    c.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE]
    # is_loading: trigger loop → loading; none_loading wait → done
    c.is_loading.side_effect = [True, False]
    return c


async def test_wrong_from_zone_fails():
    """FAILURE when client is not in from_zone; no teleports attempted."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_one_client_wrong_from_zone_fails():
    """FAILURE when one of two clients is in the wrong zone."""
    good = FakeClient()  # zone_name defaults to "TestZone" == _FROM_ZONE
    bad = FakeClient()
    bad.zone_name.return_value = "WrongZone"

    result = await itz.run(FakeMachine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    good.teleport.assert_not_called()
    bad.teleport.assert_not_called()


async def test_success_single_client():
    """Happy path: prompt appears, X pressed, client loads and arrives in to_zone."""
    client = _ready_client()

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # Reset first, whether or not a prompt showed (#152), then the one approach.
    assert [call.args[0].x for call in client.teleport.call_args_list] == [1.0, 0.0]
    assert any(call.args[0] == Keycode.X for call in client.send_key.call_args_list)


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = _ready_client()
    client.zone_name.side_effect = [None, _FROM_ZONE, _TO_ZONE]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_success_multiple_clients():
    """Both clients get prompt, press X, load, and arrive in to_zone."""
    clients = [_ready_client(), _ready_client()]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        assert any(call.args[0] == Keycode.X for call in c.send_key.call_args_list)


async def test_stale_prompt_cleared_before_loop():
    """Stale prompt on node entry → first teleport is to reset_xyz, then to interact_xyz."""
    client = FakeClient()
    prompt_at(client, _PARAMS["interact_xyz"], initially=True)  # stale on node entry
    client.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE]
    client.is_loading.side_effect = [True, False]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # First teleport must be to reset_xyz (x=1.0), second to interact_xyz (x=0.0).
    first_tp, second_tp = client.teleport.call_args_list[:2]
    assert first_tp.args[0].x == 1.0  # reset_xyz
    assert second_tp.args[0].x == 0.0  # interact_xyz


async def test_prompt_never_appears_fails():
    """FAILURE when the interact prompt never appears after MAX_RETRIES."""
    client = FakeClient()
    client.zone_name.return_value = _FROM_ZONE
    client.is_in_npc_range.return_value = False  # prompt never shows

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    # The first reset, then interact + reset for each attempt but the last, which stays at
    # interact_xyz so the node can press X for whoever did get the prompt.
    assert client.teleport.call_count == itz.MAX_RETRIES * 2
    assert client.teleport.call_args_list[-1].args[0].x == 0.0
    client.send_key.assert_not_called()


async def test_partial_prompt_presses_x_only_for_the_ready_client():
    """One client never raises the prompt; the other does — X is pressed only for the one
    that's ready, and the stuck one is left for the recovery-aware arrival check rather than
    failing the whole group."""
    ready = FakeClient()
    ready.zone_name.side_effect = [_FROM_ZONE, _FROM_ZONE, _TO_ZONE, _TO_ZONE]
    prompt_at(ready, _PARAMS["interact_xyz"])
    ready.is_loading.side_effect = [True, False]

    stuck = FakeClient()
    stuck.zone_name.return_value = _FROM_ZONE
    stuck.is_in_npc_range.return_value = False  # prompt never shows

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.wait_for_zone_with_recovery", new=AsyncMock(return_value=None)) as recovery,
    ):
        result = await itz.run(FakeMachine(), [ready, stuck], _PARAMS)

    assert result == "SUCCESS"
    recovery.assert_awaited_once()
    ready.send_key.assert_called()
    stuck.send_key.assert_not_called()


async def test_prompt_retry_then_success():
    """Prompt times out on first attempt; appears after reset on second attempt."""
    client = FakeClient()
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
    client.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE]
    client.is_loading.side_effect = [True, False]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert len(reset_visited) == 2
    assert client.teleport.call_count == 4  # reset, interact, reset, interact


async def test_trigger_timeout_still_reaches_recovery_aware_arrival_check():
    """A client that never enters a loading screen after pressing X is not failed outright —
    it still reaches the recovery-aware arrival check, which is what ultimately fails here."""
    client = FakeClient()
    prompt_at(client, _PARAMS["interact_xyz"])
    client.zone_name.return_value = _FROM_ZONE
    client.is_loading.return_value = False  # never starts loading

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch.object(itz, "TRIGGER_TIMEOUT", 0.01),
        patch(
            "bartlebot.nodes.node_helpers.wait_for_zone_with_recovery", new=AsyncMock(return_value=[_FROM_ZONE])
        ) as recovery,
    ):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    recovery.assert_awaited_once()


async def test_wrong_to_zone_after_loading_fails():
    """FAILURE when clients load but arrive in an unexpected zone."""
    client = FakeClient()
    prompt_at(client, _PARAMS["interact_xyz"])
    # Pre-check only; wait_for_zone_with_recovery (post-load) is mocked to return wrong zone.
    client.zone_name.side_effect = [_FROM_ZONE]
    client.is_loading.side_effect = [True, False]

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.wait_for_zone_with_recovery", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await itz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_x_resent_to_lagging_client():
    """One client loads immediately; the lagging client gets X re-pressed."""
    c1 = _ready_client()
    # iter1: loading (seen); iter2: done (_loading_or_arrived checks zone_name); none_loading: done.
    c1.is_loading.side_effect = [True, False, False]
    # zone_name: zone_check → iter2 loading_or_arrived (_TO_ZONE, c1 already seen) → wait_for_zone.
    c1.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE, _TO_ZONE]

    c2 = _ready_client()
    # iter1: not loading (_loading_or_arrived checks zone_name, not yet arrived); iter2: loading (seen); none_loading: done.
    c2.is_loading.side_effect = [False, True, False]
    # zone_name: zone_check → iter1 loading_or_arrived (still _FROM_ZONE, not arrived yet) → wait_for_zone.
    c2.zone_name.side_effect = [_FROM_ZONE, _FROM_ZONE, _TO_ZONE]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c1_x_presses = [call for call in c1.send_key.call_args_list if call.args[0] == Keycode.X]
    c2_x_presses = [call for call in c2.send_key.call_args_list if call.args[0] == Keycode.X]
    assert len(c1_x_presses) == 1  # only the initial press
    assert len(c2_x_presses) >= 2  # initial press + at least one re-press


async def test_fast_client_finishes_before_lagging_client_starts():
    """Client 1 finishes its loading screen before client 2 even starts loading.

    This is the bug case: is_loading() returns False for client 1 (done) AND
    False for client 2 (not started), so the old all(loading) condition could
    never be True. The seen_loading approach handles it correctly.
    """
    c1 = _ready_client()
    # iter1: loading (seen); iter2+iter3: done (_loading_or_arrived calls zone_name each time); none_loading: done.
    c1.is_loading.side_effect = [True, False, False, False]
    # zone_name: zone_check → iter2 loading_or_arrived → iter3 loading_or_arrived → wait_for_zone.
    c1.zone_name.side_effect = [_FROM_ZONE, _TO_ZONE, _TO_ZONE, _TO_ZONE]

    c2 = _ready_client()
    # iter1+iter2: not started (_loading_or_arrived calls zone_name, not yet arrived); iter3: loading (seen); none_loading: done.
    c2.is_loading.side_effect = [False, False, True, False]
    # zone_name: zone_check → iter1 loading_or_arrived → iter2 loading_or_arrived → wait_for_zone.
    c2.zone_name.side_effect = [_FROM_ZONE, _FROM_ZONE, _FROM_ZONE, _TO_ZONE]

    with patch.object(itz, "POLL_INTERVAL", 0):
        result = await itz.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c2_x_presses = [call for call in c2.send_key.call_args_list if call.args[0] == Keycode.X]
    assert len(c2_x_presses) >= 2  # initial press + re-presses until loading


# ---------------------------------------------------------------------------
# Sequential mode
# ---------------------------------------------------------------------------


def _sequential_client() -> FakeClient:
    """Client that raises the prompt at interact_xyz and transitions when it presses X."""
    c = FakeClient()
    state = {"at_interact": False, "gone": False}

    async def _teleport(xyz):
        state["at_interact"] = xyz.x == 0.0

    async def _in_range():
        return state["at_interact"] and not state["gone"]

    async def _send_key(key, delay):
        if key == Keycode.X:
            state["gone"] = True

    async def _zone():
        return _TO_ZONE if state["gone"] else _FROM_ZONE

    c.teleport.side_effect = _teleport
    c.is_in_npc_range.side_effect = _in_range
    c.send_key.side_effect = _send_key
    c.zone_name.side_effect = _zone
    # Stays False throughout: this stands in for a client whose loading screen has already
    # finished by the time it is read, which is the case _loading_or_arrived exists to
    # handle — arriving in to_zone counts as having committed just as much as loading does.
    c.is_loading.return_value = False
    c.state = state
    return c


async def test_sequential_clients_transition_one_at_a_time():
    """sequential=True: each client presses X alone, and all arrive in to_zone."""
    c1, c2 = _sequential_client(), _sequential_client()
    clients = [c1, c2]
    params = {**_PARAMS, "sequential": True}
    overlaps: list[bool] = []

    base_send = {id(c): c.send_key.side_effect for c in clients}

    def _bind(client):
        async def _press(key, delay):
            overlaps.append(sum(c.state["at_interact"] and not c.state["gone"] for c in clients) > 1)
            await base_send[id(client)](key, delay)

        client.send_key.side_effect = _press

    for c in clients:
        _bind(c)

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch_helper("POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.OBJECT_USE_DELAY", 0),
        patch_helper("is_visible_by_path", AsyncMock(return_value=False)),
        patch_helper("get_text_by_path", AsyncMock(return_value="")),
    ):
        result = await itz.run(FakeMachine(), clients, params)

    assert result == "SUCCESS"
    assert overlaps == [False, False]


def _swallow_keypresses(client: FakeClient) -> None:
    async def _no_transition(key, delay):
        client.state["at_interact"] = client.state["at_interact"]  # keypress swallowed

    client.send_key.side_effect = _no_transition


def _sequential_patches():
    return (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch.object(itz, "TRIGGER_TIMEOUT", 0.05),
        patch_helper("POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.OBJECT_USE_DELAY", 0),
        patch_helper("is_visible_by_path", AsyncMock(return_value=False)),
        patch_helper("get_text_by_path", AsyncMock(return_value="")),
    )


async def test_a_sequential_client_that_never_triggers_is_left_to_the_arrival_check():
    """One player's failed turn is not the group's: the one that went through is already across,
    so the arrival check -- not the turn -- decides, and the stuck player is named."""
    good = _sequential_client()
    stuck = _sequential_client()
    _swallow_keypresses(stuck)
    machine = FakeMachine()
    arrival = AsyncMock(return_value=[_TO_ZONE, _FROM_ZONE])

    with ExitStack() as stack:
        for p in _sequential_patches():
            stack.enter_context(p)
        stack.enter_context(patch("bartlebot.nodes.node_helpers.wait_for_zone_with_recovery", new=arrival))
        result = await itz.run(machine, [good, stuck], {**_PARAMS, "sequential": True})

    assert result == "FAILURE"
    arrival.assert_awaited_once()
    assert any("did not go through" in msg and "[2]" in msg for msg, _ in machine.logs)


async def test_sequential_clients_that_all_fail_to_trigger_fail_the_step():
    clients = [_sequential_client(), _sequential_client()]
    for client in clients:
        _swallow_keypresses(client)
    machine = FakeMachine()

    with ExitStack() as stack:
        for p in _sequential_patches():
            stack.enter_context(p)
        result = await itz.run(machine, clients, {**_PARAMS, "sequential": True})

    assert result == "FAILURE"
    assert any("did not enter a loading screen" in msg and "[1, 2]" in msg for msg, _ in machine.logs)


async def test_sequential_does_not_park_a_departing_client():
    """park_after_turn is off here — no teleport is aimed at a client already leaving."""
    client = _sequential_client()
    params = {**_PARAMS, "sequential": True}

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch_helper("POLL_INTERVAL", 0),
        patch("bartlebot.nodes.node_helpers.OBJECT_USE_DELAY", 0),
        patch_helper("is_visible_by_path", AsyncMock(return_value=False)),
        patch_helper("get_text_by_path", AsyncMock(return_value="")),
    ):
        result = await itz.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    # Park at reset, then the approach to interact — and nothing after the keypress.
    assert [call.args[0].x for call in client.teleport.call_args_list] == [1.0, 0.0]


# ---------------------------------------------------------------------------
# A parallel use that leaves some clients behind
# ---------------------------------------------------------------------------


def _goes_through() -> FakeClient:
    """Raises the prompt, loads on its first keypress, and arrives -- however often it is read."""
    c = FakeClient()
    prompt_at(c, _PARAMS["interact_xyz"])
    c.zone_name.side_effect = itertools.chain([_FROM_ZONE], itertools.repeat(_TO_ZONE))
    c.is_loading.side_effect = itertools.chain([True], itertools.repeat(False))
    return c


def _left_behind() -> FakeClient:
    """Raises the prompt with everyone else, but its keypress never starts a transition."""
    c = FakeClient()
    prompt_at(c, _PARAMS["interact_xyz"])
    c.zone_name.return_value = _FROM_ZONE
    return c


async def test_clients_a_parallel_use_left_behind_get_turns_at_the_object():
    """One went through and one's keypress was swallowed: the object serves one player at a time."""
    through, behind = _goes_through(), _left_behind()
    machine = FakeMachine()
    turns = AsyncMock(return_value=None)

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch.object(itz, "TRIGGER_TIMEOUT", 0.01),
        patch.object(itz, "_trigger_sequentially", turns),
        patch("bartlebot.nodes.node_helpers.wait_for_zone_with_recovery", new=AsyncMock(return_value=None)),
    ):
        result = await itz.run(machine, [through, behind], _PARAMS)

    assert result == "SUCCESS"
    turns.assert_awaited_once()
    assert turns.await_args.args[1] == [behind]
    assert any("Sequential Interaction" in msg for msg, _ in machine.logs)


async def test_a_client_that_starts_loading_after_the_press_window_gets_no_turn():
    """It is already on its way; a turn would teleport it back out of where it lands (#70)."""
    through, behind = _goes_through(), _left_behind()
    turns = AsyncMock(return_value=None)

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch.object(itz, "_trigger_sequentially", turns),
        patch.object(itz, "_loading_or_arrived", AsyncMock(return_value=True)),
        patch("bartlebot.nodes.node_helpers.poll_each_until_seen", AsyncMock(return_value={0})),
        patch("bartlebot.nodes.node_helpers.wait_for_zone_with_recovery", new=AsyncMock(return_value=None)),
    ):
        await itz.run(FakeMachine(), [through, behind], _PARAMS)

    turns.assert_not_awaited()


async def test_nobody_going_through_gives_no_turns():
    """If nobody went through, the object is not being shared badly: it is not working."""
    clients = [_left_behind(), _left_behind()]
    turns = AsyncMock(return_value=None)

    with (
        patch.object(itz, "POLL_INTERVAL", 0),
        patch.object(itz, "_trigger_sequentially", turns),
        patch("bartlebot.nodes.node_helpers.poll_each_until_seen", AsyncMock(return_value=set())),
        patch(
            "bartlebot.nodes.node_helpers.wait_for_zone_with_recovery",
            new=AsyncMock(return_value=[_FROM_ZONE, _FROM_ZONE]),
        ),
    ):
        result = await itz.run(FakeMachine(), clients, _PARAMS)

    assert result == "FAILURE"
    turns.assert_not_awaited()


async def test_a_prompt_that_will_not_clear_at_reset_fails_before_any_keypress():
    """This node presses X for whichever clients have a prompt, so a prompt stuck on at
    reset_xyz would be pressed — on whatever raised it (#152). It fails instead."""
    client = FakeClient()
    client.is_in_npc_range.return_value = True
    machine = FakeMachine()

    with patch.object(itz, "POLL_INTERVAL", 0), patch("bartlebot.nodes.node_helpers.PROMPT_CLEAR_SETTLE", 0):
        result = await itz.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()
    assert [call.args[0].x for call in client.teleport.call_args_list] == [1.0]
