"""Unit tests for the go_to_world_hub node."""

from unittest.mock import AsyncMock, patch

from wizwalker import Keycode

import bartlebot.nodes.action.go_to_world_hub as gwh
import bartlebot.nodes.node_helpers as nh
from tests.conftest import FakeClient, FakeMachine

_ZONE = "WizardCity/WC_Hub"
_PARAMS = {"zone": _ZONE}

# Patch is_enabled_by_path → True by default so tests don't hang waiting for the button.
_BUTTON_ENABLED = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=True))


def _client_not_in_hub() -> FakeClient:
    """Client starting outside the hub that successfully arrives after teleport."""
    client = FakeClient()
    client.zone_name.side_effect = ["OtherZone", _ZONE]
    # Trigger loop: loading starts immediately; none_loading check: loading finished.
    client.is_loading.side_effect = [True, False]
    return client


def _client_already_in_hub() -> FakeClient:
    """Client that is already in the hub zone and should not be teleported."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    return client


async def test_already_in_zone_returns_success():
    """Clients already in the hub zone → SUCCESS immediately, END key never sent."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE

    result = await gwh.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.send_key.assert_not_called()


async def test_already_in_zone_multiple_clients():
    """All clients already in hub → SUCCESS without pressing END."""
    clients = [FakeClient(), FakeClient()]
    for c in clients:
        c.zone_name.return_value = _ZONE

    result = await gwh.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.send_key.assert_not_called()


async def test_button_timeout_returns_failure():
    """Hub button never becomes enabled within the timeout → FAILURE."""
    client = FakeClient()
    client.zone_name.return_value = "OtherZone"

    with (
        patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=False)),
        patch.object(gwh, "COMPASS_TELEPORT_TIMEOUT", 0.01),
        patch.object(gwh, "POLL_INTERVAL", 0),
    ):
        result = await gwh.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


async def test_success_single_client():
    """Happy path: one client, button enabled, loads and arrives in hub zone."""
    client = _client_not_in_hub()

    with _BUTTON_ENABLED, patch.object(gwh, "POLL_INTERVAL", 0):
        result = await gwh.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert any(call.args[0] == Keycode.END for call in client.send_key.call_args_list)


async def test_success_multiple_clients():
    """Happy path: two clients both arrive in hub zone."""
    clients = [_client_not_in_hub(), _client_not_in_hub()]

    with _BUTTON_ENABLED, patch.object(gwh, "POLL_INTERVAL", 0):
        result = await gwh.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        assert any(call.args[0] == Keycode.END for call in c.send_key.call_args_list)


async def test_trigger_timeout_returns_failure():
    """Clients never enter a loading screen after pressing END → FAILURE."""
    client = FakeClient()
    client.zone_name.return_value = "OtherZone"
    client.is_loading.return_value = False  # never starts loading

    with (
        _BUTTON_ENABLED,
        patch.object(gwh, "TRIGGER_TIMEOUT", 0.01),
        patch.object(gwh, "POLL_INTERVAL", 0),
    ):
        result = await gwh.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_wrong_zone_after_loading_returns_failure():
    """Clients load but arrive in an unexpected zone → FAILURE."""
    client = FakeClient()
    # Pre-check only; wait_for_zone (post-load) is mocked to return wrong zone.
    client.zone_name.side_effect = ["OtherZone"]
    client.is_loading.side_effect = [True, False]

    with (
        _BUTTON_ENABLED,
        patch.object(gwh, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=["WrongZone"])),
    ):
        result = await gwh.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_loading_screen_awaited():
    """Node waits for is_loading to clear before checking the final zone."""
    client = FakeClient()
    client.zone_name.side_effect = ["OtherZone", _ZONE]
    # Loading takes two polls to finish.
    client.is_loading.side_effect = [True, True, False]

    with _BUTTON_ENABLED, patch.object(gwh, "POLL_INTERVAL", 0):
        result = await gwh.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.is_loading.call_count >= 3


async def test_end_resent_to_lagging_client():
    """One client starts loading immediately; the other needs a second END press."""
    c1 = FakeClient()
    c1.zone_name.side_effect = ["OtherZone", _ZONE]
    # Trigger loop iter1: loading; iter2: still loading; none_loading: done.
    c1.is_loading.side_effect = [True, True, False]

    c2 = FakeClient()
    c2.zone_name.side_effect = ["OtherZone", _ZONE]
    # Trigger loop iter1: not loading yet; iter2: loading; none_loading: done.
    c2.is_loading.side_effect = [False, True, False]

    with _BUTTON_ENABLED, patch.object(gwh, "POLL_INTERVAL", 0):
        result = await gwh.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    # c2 received at least two END presses (initial + re-press); c1 only the initial one.
    c1_end_presses = [call for call in c1.send_key.call_args_list if call.args[0] == Keycode.END]
    c2_end_presses = [call for call in c2.send_key.call_args_list if call.args[0] == Keycode.END]
    assert len(c1_end_presses) == 1
    assert len(c2_end_presses) >= 2


async def test_fast_client_finishes_before_lagging_client_starts():
    """Client 1 finishes loading before client 2 even starts.

    The old all(loading) break condition could never be satisfied when client 1's
    is_loading() flips back to False (done) while client 2 is still False (not
    started). The seen_loading approach handles this correctly.
    """
    c1 = FakeClient()
    c1.zone_name.side_effect = ["OtherZone", _ZONE]
    # iter1: loading (seen); iter2: already done; iter3: done; none_loading: done
    c1.is_loading.side_effect = [True, False, False, False]

    c2 = FakeClient()
    c2.zone_name.side_effect = ["OtherZone", _ZONE]
    # iter1: not started; iter2: not started; iter3: loading (seen); none_loading: done
    c2.is_loading.side_effect = [False, False, True, False]

    with _BUTTON_ENABLED, patch.object(gwh, "POLL_INTERVAL", 0):
        result = await gwh.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c2_end_presses = [call for call in c2.send_key.call_args_list if call.args[0] == Keycode.END]
    assert len(c2_end_presses) >= 2


async def test_partial_already_in_zone_skips_teleport():
    """One client already in hub, one not — the hub client is skipped entirely → SUCCESS."""
    c_hub = _client_already_in_hub()
    c_other = _client_not_in_hub()

    with _BUTTON_ENABLED, patch.object(gwh, "POLL_INTERVAL", 0):
        result = await gwh.run(FakeMachine(), [c_hub, c_other], _PARAMS)

    assert result == "SUCCESS"
    c_hub.send_key.assert_not_called()
    assert any(call.args[0] == Keycode.END for call in c_other.send_key.call_args_list)


async def test_partial_already_in_zone_wrong_arrival_returns_failure():
    """One client already in hub, the other teleports but arrives in the wrong zone → FAILURE."""
    c_hub = _client_already_in_hub()
    c_wrong = FakeClient()
    # Pre-check only; wait_for_zone (post-load) is mocked to return wrong zone.
    c_wrong.zone_name.side_effect = ["OtherZone"]
    c_wrong.is_loading.side_effect = [True, False]

    with (
        _BUTTON_ENABLED,
        patch.object(gwh, "POLL_INTERVAL", 0),
        patch.object(nh, "wait_for_zone", new=AsyncMock(return_value=[_ZONE, "WrongZone"])),
    ):
        result = await gwh.run(FakeMachine(), [c_hub, c_wrong], _PARAMS)

    assert result == "FAILURE"
    c_hub.send_key.assert_not_called()


async def test_partial_already_in_zone_with_lagging_client():
    """One client already in hub; the other needs a re-press before loading → SUCCESS."""
    c_hub = _client_already_in_hub()

    c_lag = FakeClient()
    c_lag.zone_name.side_effect = ["OtherZone", _ZONE]
    # iter1: not loading yet (gets re-press); iter2: loading; none_loading: done.
    c_lag.is_loading.side_effect = [False, True, False]

    with _BUTTON_ENABLED, patch.object(gwh, "POLL_INTERVAL", 0):
        result = await gwh.run(FakeMachine(), [c_hub, c_lag], _PARAMS)

    assert result == "SUCCESS"
    c_hub.send_key.assert_not_called()
    c_lag_end_presses = [call for call in c_lag.send_key.call_args_list if call.args[0] == Keycode.END]
    assert len(c_lag_end_presses) >= 2
