"""Unit tests for the teleport_to_coordinates node."""

from wizwalker import XYZ

import bartlebot.nodes.teleportation.teleport_to_coordinates as gxyz
from tests.conftest import FakeClient, FakeMachine

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


async def test_off_target_returns_failure():
    """Client teleports but position check shows it is far from target → FAILURE."""
    client = FakeClient()
    client.zone_name.return_value = _ZONE
    client.body.position.return_value = XYZ(9999.0, 9999.0, 0.0)

    result = await gxyz.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_called_once()


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
