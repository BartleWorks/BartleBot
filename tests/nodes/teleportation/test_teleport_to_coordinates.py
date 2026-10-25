"""Unit tests for the teleport_to_coordinates node."""

from wizwalker import XYZ

import bartlebot.nodes.teleportation.teleport_to_coordinates as gxyz
from tests.conftest import FakeClient, FakeMachine, zone_unreadable_at_first

_ZONE = "WizardCity/WC_Hub"
_TARGET = {"x": 100.0, "y": 200.0, "z": 0.0}
_PARAMS = {"zone": _ZONE, "xyz": _TARGET}
_TARGET_XYZ = XYZ(100.0, 200.0, 0.0)


async def test_wrong_zone_returns_failure():
    """Client not in the expected zone → FAILURE, teleport never called."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_partial_wrong_zone_returns_failure():
    """One client in the right zone, one not → FAILURE."""
    c1 = FakeClient()
    c1.zone_name.return_value = _ZONE
    c1.body.position.return_value = _TARGET_XYZ

    c2 = FakeClient()
    c2.zone_name.return_value = "WrongZone"

    result = await gxyz.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "FAILURE"
    c1.teleport.assert_not_called()
    c2.teleport.assert_not_called()


async def test_success_single_client():
    """Happy path: one client in zone, teleport succeeds, position verified."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    client.body.position.return_value = _TARGET_XYZ

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    arg = client.teleport.call_args[0][0]
    assert (arg.x, arg.y, arg.z) == (100.0, 200.0, 0.0)


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = FakeClient()
    zone_unreadable_at_first(client, _ZONE)
    client.body.position.return_value = _TARGET_XYZ

    assert await gxyz.run(FakeMachine(), [client], _PARAMS) == "SUCCESS"


async def test_success_multiple_clients():
    """Happy path: two clients both teleport and arrive at target."""
    clients = [FakeClient(), FakeClient()]
    for c in clients:
        c.zone_name.return_value = _ZONE
        c.body.position.return_value = _TARGET_XYZ

    result = await gxyz.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    for c in clients:
        c.teleport.assert_called_once()
        arg = c.teleport.call_args[0][0]
        assert (arg.x, arg.y, arg.z) == (100.0, 200.0, 0.0)


async def test_off_target_returns_failure_after_every_retry():
    """Client never reaches the target → FAILURE once MAX_RETRIES teleports have missed."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    client.body.position.return_value = XYZ(9999.0, 9999.0, 0.0)

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert client.teleport.call_count == gxyz.MAX_RETRIES


async def test_teleport_retried_after_landing_in_zone():
    """First teleport ignored (client still settling in after a zone change), second lands → SUCCESS."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), _TARGET_XYZ]

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.teleport.call_count == 2


async def test_retry_only_teleports_off_target_clients():
    """A client that arrived first time is not teleported again while another is retried."""
    c1 = FakeClient()
    c1.zone_name.return_value = _ZONE
    c1.body.position.return_value = _TARGET_XYZ

    c2 = FakeClient()
    c2.zone_name.return_value = _ZONE
    c2.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), _TARGET_XYZ]

    result = await gxyz.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "SUCCESS"
    c1.teleport.assert_called_once()
    assert c2.teleport.call_count == 2


async def test_retry_waits_the_safety_delay(monkeypatch):
    """The wait between attempts is the bot's Safety Delay."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(gxyz.asyncio, "sleep", fake_sleep)
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), _TARGET_XYZ]

    result = await gxyz.run(FakeMachine(options={"Safety Delay": 4.5}), [client], _PARAMS)

    assert result == "SUCCESS"
    assert slept == [4.5]


async def test_client_leaving_zone_during_retry_returns_failure():
    """A client that is no longer in the zone at retry time → FAILURE, no further teleport."""
    client = FakeClient()
    client.zone_name.side_effect = [_ZONE, "WrongZone"]
    client.body.position.return_value = XYZ(0.0, 0.0, 0.0)

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_called_once()


async def test_a_zone_unreadable_at_retry_time_is_not_read_as_leaving():
    """A None at the retry's zone read is waited out, not taken for a client that left (#282)."""
    client = FakeClient()
    client.zone_name.side_effect = [_ZONE, None, _ZONE]
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), _TARGET_XYZ]

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert client.teleport.call_count == 2


async def test_loading_client_is_not_teleported_on_retry():
    """A client on a loading screen at retry time waits for the next attempt instead (#70)."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    client.is_loading.side_effect = [True, False]
    client.body.position.side_effect = [XYZ(0.0, 0.0, 0.0), XYZ(0.0, 0.0, 0.0), _TARGET_XYZ]

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # The first teleport, then none while loading, then one on the third attempt.
    assert client.teleport.call_count == 2


async def test_position_within_margin_returns_success():
    """Position slightly off target but within POSITION_MARGIN → SUCCESS."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    # Place client just inside the margin boundary.
    client.body.position.return_value = XYZ(
        _TARGET_XYZ.x + gxyz.POSITION_MARGIN - 0.1,
        _TARGET_XYZ.y,
        _TARGET_XYZ.z,
    )

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_position_outside_margin_returns_failure():
    """Position just outside POSITION_MARGIN → FAILURE."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    client.body.position.return_value = XYZ(
        _TARGET_XYZ.x + gxyz.POSITION_MARGIN + 0.1,
        _TARGET_XYZ.y,
        _TARGET_XYZ.z,
    )

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_partial_off_target_returns_failure():
    """One client on target, one off → FAILURE."""
    c1 = FakeClient()
    c1.zone_name.return_value = _ZONE
    c1.body.position.return_value = _TARGET_XYZ

    c2 = FakeClient()
    c2.zone_name.return_value = _ZONE
    c2.body.position.return_value = XYZ(9999.0, 9999.0, 0.0)

    result = await gxyz.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "FAILURE"
