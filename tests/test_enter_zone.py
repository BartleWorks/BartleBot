"""Unit tests for the enter_zone node."""

import asyncio
import itertools
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.zone_entry.enter_zone as gtz
from bartlebot.nodes.paths import MESSAGE_BOX_CENTER
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
    # step 1 pre-check → "Src"; step 3 first poll → "Src" (still leaving,
    # so poll_each_until_seen re-teleports); step 3 second poll → "Dst" (left Src);
    # step 5 post-check → "Dst".
    client.zone_name.side_effect = ["Src", "Src", "Dst", "Dst"]

    with patch.object(gtz, "POLL_INTERVAL", 0):
        result = await gtz.run(machine=FakeMachine(), clients=[client], params=_PARAMS)

    assert result == "SUCCESS"
    # teleport(target) called once in step 2, once by poll_each_until_seen for the straggler.
    assert client.teleport.call_count == 2


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
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=lambda _c, p: p == MESSAGE_BOX_CENTER)),
        patch.object(nh, "click_by_path", click_by_path_mock),
    ):
        result = await gtz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # confirm button clicked; no keypresses
    client.send_key.assert_not_called()
    click_by_path_mock.assert_awaited_with(client, MESSAGE_BOX_CENTER)


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
