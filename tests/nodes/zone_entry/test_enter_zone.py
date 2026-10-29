"""Unit tests for the enter_zone node."""

import asyncio
import itertools
import logging
from unittest.mock import AsyncMock, patch

import pytest
from bartleby.runtime.paths import MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT
from wizwalker import XYZ

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.zone_entry.enter_zone as gtz
from tests.conftest import FakeClient, FakeMachine, ModalScreen
from tests.node_helper_patches import patch_helper

# Minimal params used across all tests.
_PARAMS = {"from_zone": "Src", "to_zone": "Dst", "xyz": {"x": 0.0, "y": 0.0, "z": 0.0}}

_REAL_WATCH_GROUP = gtz._watch_group


@pytest.fixture(autouse=True)
def _no_watcher():
    """The watcher reads zone_name too, which would eat the scripted zone sequences below."""
    with patch.object(gtz, "_watch_group", new=AsyncMock()):
        yield


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
    # step 1 pre-check → "Src"; step 3 first poll → "Src" (still leaving,
    # so poll_each_until_seen re-teleports); step 3 second poll → "Dst" (left Src);
    # step 5 post-check → "Dst".
    client.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(machine=FakeMachine(), clients=[client], params=_PARAMS)

    assert result == "SUCCESS"
    # teleport(target) called once in step 2, once by poll_each_until_seen for the straggler.
    assert client.teleport.call_count == 2


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = FakeClient()
    client.zone_name.side_effect = [None, "Src", "Src", "Dst", "Dst"]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(machine=FakeMachine(), clients=[client], params=_PARAMS)

    assert result == "SUCCESS"


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
        assert c.teleport.call_count == 2


async def test_a_client_in_the_challenge_mode_copy_of_from_zone_has_not_left_it():
    """It used to read as gone at once, so it was never sent to the trigger (#143)."""
    client = FakeClient()
    client.zone_name.side_effect = itertools.chain(
        ["Aquila/AQ_Z02_Atlantea_Hard"] * 2, itertools.repeat("Aquila/Interiors/AQ_Z02_ThroneRoom_Hard")
    )
    params = {**_PARAMS, "from_zone": "Aquila/AQ_Z02_Atlantea", "to_zone": "Aquila/Interiors/AQ_Z02_ThroneRoom"}

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    # The opening teleport, and the retry while it still stood in the copy.
    assert client.teleport.call_count == 2


async def test_timeout_leaving_source_zone_fails():
    """If clients never leave from_zone the node returns FAILURE after timeout."""
    client = FakeClient()
    client.zone_name.return_value = "Src"  # never changes

    with patch.object(gtz, "ZONE_CHANGE_TIMEOUT", 0.01), patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_trigger_retry_uses_safety_delay_not_zone_change_timeout():
    """Each trigger-detection attempt is bounded by Safety Delay, not the full zone-change
    timeout — that larger budget is reserved for the loading-screen wait that follows."""
    client = FakeClient()
    client.zone_name.return_value = "Src"  # never leaves
    machine = FakeMachine(options={"Safety Delay": 0.01})
    params = {**_PARAMS, "reset_xyz": {"x": 1.0, "y": 1.0, "z": 1.0}}

    loop = asyncio.get_event_loop()
    start = loop.time()
    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        # Deliberately huge — if the trigger wait still used this constant instead of
        # Safety Delay, the test would hang for (close to) this long.
        patch.object(gtz, "ZONE_CHANGE_TIMEOUT", 100.0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=["Src"])),
    ):
        result = await gtz.run(machine, [client], params)
    elapsed = loop.time() - start

    assert result == "FAILURE"
    assert elapsed < 1.0


async def test_stuck_client_still_gets_a_recovery_attempt():
    """A client whose trigger never fires still reaches the recovery-aware arrival check —
    it is not failed outright before wait_for_zone_with_recovery gets a chance to run."""
    stuck = FakeClient()
    stuck.zone_name.return_value = "Src"  # never leaves from_zone

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=None)) as recovery,
    ):
        result = await gtz.run(FakeMachine(), [stuck], _PARAMS)

    assert result == "SUCCESS"
    recovery.assert_awaited_once()
    # The group passed to the recovery-aware check includes the stuck client.
    assert recovery.await_args[0][1] == [stuck]


async def test_only_stragglers_are_reteleported_on_retry():
    """A client that already left from_zone is not disturbed by a later retry attempt —
    only the client(s) still stuck get sent back to the trigger and reset."""
    left = FakeClient()
    left.zone_name = AsyncMock(side_effect=itertools.chain(["Src"], itertools.repeat("Dst")))
    stuck = FakeClient()
    stuck.zone_name = AsyncMock(return_value="Src")  # never leaves, forces every retry

    params = {**_PARAMS, "reset_xyz": {"x": 1.0, "y": 1.0, "z": 1.0}}

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=None)),
    ):
        await gtz.run(FakeMachine(), [left, stuck], params)

    # `left` is teleported into the trigger box once, on the first attempt, and never
    # re-teleported (to the trigger or to reset_xyz) on any later retry.
    assert left.teleport.call_count == 1


async def test_a_loading_client_is_not_re_teleported_to_the_trigger():
    """A client on a loading screen still reads from_zone, so the leave-check cannot see it
    go. Re-teleporting it there lands the teleport when it arrives and drops it back at the
    bounding box in the zone it just reached (#70)."""
    loading = FakeClient()
    # pre-check -> "Src"; still reading "Src" while the screen is up; then in Dst.
    loading.zone_name = AsyncMock(side_effect=itertools.chain(["Src", "Src", "Src"], itertools.repeat("Dst")))
    loading.is_loading = AsyncMock(side_effect=itertools.chain([True, True], itertools.repeat(False)))

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=None)),
    ):
        result = await gtz.run(FakeMachine(), [loading], _PARAMS)

    assert result == "SUCCESS"
    # Only the opening teleport into the bounding box; no retry fired during the load.
    assert loading.teleport.call_count == 1


async def test_confirmed_arrival_stops_remaining_trigger_retries():
    """As soon as one client is confirmed in to_zone, a stuck groupmate is not chased through
    the remaining reset_xyz retries — recovery is attempted immediately instead."""
    arrived = FakeClient()
    # pre-check -> "Src"; check_fn first poll -> "Dst" (already left); stop_fn confirms arrival.
    arrived.zone_name = AsyncMock(side_effect=itertools.chain(["Src"], itertools.repeat("Dst")))
    arrived.is_loading = AsyncMock(return_value=False)

    stuck = FakeClient()
    stuck.zone_name = AsyncMock(return_value="Src")  # never leaves from_zone

    params = {**_PARAMS, "reset_xyz": {"x": 1.0, "y": 1.0, "z": 1.0}}

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=None)) as recovery,
    ):
        result = await gtz.run(FakeMachine(), [arrived, stuck], params)

    assert result == "SUCCESS"
    recovery.assert_awaited_once()
    # Only the initial teleport into the trigger box — no reset_xyz retry was ever attempted.
    assert stuck.teleport.call_count == 1


async def test_post_condition_wrong_zone_fails():
    """Clients leave from_zone but land in an unexpected zone → FAILURE."""
    client = FakeClient()
    # step 1 → "Src"; step 3 poll1 → "Src"; step 3 poll2 → "Detour" (left Src).
    # wait_for_zone_with_recovery (step 5) is mocked to return the wrong-zone list immediately.
    client.zone_name.side_effect = ["Src", "Src", "Detour"]

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=["Detour"])),
    ):
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
    """When the dungeon exit warning appears after teleport, the confirm button is clicked."""
    client = FakeClient()
    client.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]
    click_by_path_mock = AsyncMock(return_value=True)

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch_helper("is_visible_by_path", new=AsyncMock(side_effect=lambda _c, p: p == MESSAGE_BOX_CENTER)),
        patch_helper("click_by_path", click_by_path_mock),
    ):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # confirm button clicked; no keypresses
    client.send_key.assert_not_called()
    click_by_path_mock.assert_awaited_with(client, MESSAGE_BOX_CENTER)


_EXIT_CAPTION = (
    "If you leave a Dungeon you will lose all your progress in the Dungeon and will have to start over at the "
    "beginning.<br>Are you sure you want to leave now?"
)


async def test_a_prompt_the_first_answer_missed_is_confirmed_while_waiting_to_leave():
    """#281: the exit prompt sat open with the client at the exit, and only a new attempt answered it."""
    screen = ModalScreen((_EXIT_CAPTION, [MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT]))
    client = FakeClient()
    client.zone_name = AsyncMock(side_effect=lambda: "Src" if screen.modals else "Dst")

    with (
        screen.apply(),
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "answer_instance_prompts", new=AsyncMock(return_value=None)),
    ):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert screen.clicked == [MESSAGE_BOX_CENTER]
    # Answering stood in for a re-teleport: the opening one is the only one.
    assert client.teleport.call_count == 1


async def test_a_refusal_found_while_waiting_to_leave_fails_the_node():
    screen = ModalScreen(("Your friend is busy right now.", [MESSAGE_BOX_RIGHT]))
    client = FakeClient()
    client.zone_name = AsyncMock(side_effect=lambda: "Src")
    machine = FakeMachine()

    with (
        screen.apply(),
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "answer_instance_prompts", new=AsyncMock(return_value=None)),
    ):
        result = await gtz.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    assert any("refused the zone change" in msg for msg, _ in machine.logs)


def _make_client():
    c = FakeClient()
    c.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]
    return c


async def test_stagger_disabled_by_default():
    """With 4 clients and stagger not set, all clients transition in a single phase."""
    clients = [_make_client() for _ in range(4)]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        assert c.teleport.call_count == 2


async def test_stagger_with_four_clients_sequences_lead_first():
    """With stagger=True and 4 clients, the lead client fully transitions before the rest teleport."""
    lead = _make_client()
    rest = [_make_client(), _make_client(), _make_client()]
    clients = [lead, *rest]
    params = {**_PARAMS, "stagger": True}

    def _rest_teleport_side_effect(*args, **kwargs):
        # As soon as any follower is teleported, the lead must already be done.
        assert lead.zone_name.call_count == 4

    for c in rest:
        c.teleport.side_effect = _rest_teleport_side_effect

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), clients, params)

    assert result == "SUCCESS"
    assert lead.teleport.call_count == 2
    for c in rest:
        assert c.teleport.call_count == 2


async def test_stagger_ignored_with_three_clients():
    """stagger=True has no effect when only 3 clients are connected."""
    clients = [_make_client(), _make_client(), _make_client()]
    params = {**_PARAMS, "stagger": True}

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(FakeMachine(), clients, params)

    assert result == "SUCCESS"
    for c in clients:
        assert c.teleport.call_count == 2


async def test_stagger_lead_failure_short_circuits():
    """If the lead client never leaves from_zone, the rest are never teleported."""
    lead = FakeClient()
    lead.zone_name.return_value = "Src"  # never changes, so lead times out
    rest = [_make_client(), _make_client(), _make_client()]
    clients = [lead, *rest]
    params = {**_PARAMS, "stagger": True}

    with (
        patch.object(gtz, "ZONE_CHANGE_TIMEOUT", 0.01),
        patch.object(gtz, "POLL_INTERVAL", 0),
    ):
        result = await gtz.run(FakeMachine(), clients, params)

    assert result == "FAILURE"
    for c in rest:
        c.teleport.assert_not_called()


# ---------------------------------------------------------------------------
# Every call retries (#101)
#
# reset_xyz used to be optional, and a node without one tried the trigger once.
# A bot now has to set it; a caller in code that does not resets each client
# to where it stood when the node started, which is clear of the trigger.
# ---------------------------------------------------------------------------


def _stuck_client(start):
    client = FakeClient()
    client.zone_name = AsyncMock(return_value="Src")  # the trigger never fires
    client.body.position = AsyncMock(return_value=start)
    return client


async def _run_stuck(clients, params):
    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(return_value=None)),
    ):
        return await gtz.run(FakeMachine(), clients, params)


def _teleport_targets(client):
    """Where the client was teleported, as tuples: XYZ has no __eq__, so two equal points compare unequal."""
    return [tuple(call.args[0]) for call in client.teleport.call_args_list]


async def test_without_a_reset_each_client_retries_from_where_it_started():
    a = _stuck_client(XYZ(10.0, 20.0, 30.0))
    b = _stuck_client(XYZ(-5.0, 6.0, 7.0))

    await _run_stuck([a, b], _PARAMS)

    assert (10.0, 20.0, 30.0) in _teleport_targets(a)
    assert (-5.0, 6.0, 7.0) in _teleport_targets(b)
    assert (10.0, 20.0, 30.0) not in _teleport_targets(b)


async def test_a_trigger_is_retried_up_to_max_retries_without_a_reset():
    """The single-attempt path is gone: no reset used to mean one try and no retry."""
    client = _stuck_client(XYZ(10.0, 20.0, 30.0))

    await _run_stuck([client], _PARAMS)

    assert _teleport_targets(client).count((10.0, 20.0, 30.0)) == gtz.MAX_RETRIES - 1


async def test_a_configured_reset_is_used_for_every_client():
    a = _stuck_client(XYZ(10.0, 20.0, 30.0))
    b = _stuck_client(XYZ(-5.0, 6.0, 7.0))

    await _run_stuck([a, b], {**_PARAMS, "reset_xyz": {"x": 1.0, "y": 2.0, "z": 3.0}})

    for client in (a, b):
        assert (1.0, 2.0, 3.0) in _teleport_targets(client)
    assert (10.0, 20.0, 30.0) not in _teleport_targets(a)


async def test_an_all_zero_reset_is_never_teleported_to():
    """A reset stored as (0, 0, 0) is an unset one, not the zone's origin."""
    client = _stuck_client(XYZ(10.0, 20.0, 30.0))
    params = {**_PARAMS, "xyz": {"x": 4.0, "y": 5.0, "z": 6.0}, "reset_xyz": {"x": 0.0, "y": 0.0, "z": 0.0}}

    await _run_stuck([client], params)

    assert (0.0, 0.0, 0.0) not in _teleport_targets(client)
    assert (10.0, 20.0, 30.0) in _teleport_targets(client)


def test_reset_xyz_is_required_in_the_editor():
    field = next(f for f in gtz.PARAM_SCHEMA if f.key == "reset_xyz")

    assert field.required
    assert field.default is None


# ---------------------------------------------------------------------------
# Who was left behind, by player number
#
# With Stagger on, a follower sometimes never fires the trigger. The log has to say which
# one, and where each player stood when it changed zone, or the cause stays a guess.
# ---------------------------------------------------------------------------


def _door_client(opens: bool):
    """A client that moves to Dst when teleported to the trigger, if `opens`, and otherwise stays in Src."""
    client = FakeClient()
    state = {"zone": "Src"}
    client.zone_name.side_effect = lambda: state["zone"]

    async def _teleport(xyz):
        if opens:
            state["zone"] = "Dst"

    client.teleport.side_effect = _teleport
    return client


async def test_the_player_left_behind_is_named_by_number():
    clients = [_door_client(True), _door_client(True), _door_client(False), _door_client(True)]
    machine = FakeMachine()

    with (
        patch.object(gtz, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone_with_recovery", new=AsyncMock(side_effect=[None, ["Dst", "Src", "Dst"]])),
    ):
        result = await gtz.run(machine, clients, {**_PARAMS, "stagger": True})

    assert result == "FAILURE"
    logged = [message for message, _ in machine.logs]
    assert "Left 'Src': P2, P4; still in it: P3" in logged
    assert "Zones after transition: P2 Dst, P3 Src, P4 Dst" in logged


async def test_the_watcher_logs_a_players_change_once_with_where_it_stood():
    stays, moves = FakeClient(), FakeClient()
    stays.zone_name.return_value = "World/Src"
    moves.zone_name.side_effect = itertools.chain(["World/Src"] * 2, itertools.repeat("World/Dst"))
    moves.body.position.return_value = XYZ(1.4, -2.6, 3.0)
    machine = FakeMachine()

    def logged_the_move() -> bool:
        return any("zone=Dst" in message for message, _ in machine.logs)

    with patch.object(gtz, "POLL_INTERVAL", 0.001):
        task = asyncio.create_task(_REAL_WATCH_GROUP(machine, [stays, moves], [1, 3]))
        # Until the move is logged, not a fixed 50 ms: under -n auto the polls can miss one.
        for _ in range(2000):
            if logged_the_move():
                break
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.02)  # a few polls more, which must log nothing new
        task.cancel()

    logged = [message for message, level in machine.logs if level == logging.DEBUG]
    assert logged.count("P1: zone=Src loading=False at (0, 0, 0)") == 1
    assert logged.count("P3: zone=Src loading=False at (1, -3, 3)") == 1
    assert logged.count("P3: zone=Dst loading=False at (1, -3, 3)") == 1


async def test_the_watcher_reports_a_client_it_cannot_read_instead_of_raising():
    loading = FakeClient()
    loading.zone_name.side_effect = RuntimeError("loading")
    loading.is_loading.side_effect = RuntimeError("loading")
    loading.body.position.side_effect = RuntimeError("loading")
    machine = FakeMachine()

    with patch.object(gtz, "POLL_INTERVAL", 0.001):
        task = asyncio.create_task(_REAL_WATCH_GROUP(machine, [loading], [2]))
        await asyncio.sleep(0.02)
        task.cancel()

    assert [m for m, _ in machine.logs] == ["P2: zone=unreadable loading=None at unreadable"]
