"""Unit tests for the teleport_to_xyz node: walk every client to the start, then hop each to the end."""

import pytest
from bartleby.runtime.movement.walk import WalkEnd
from wizwalker import XYZ

import bartlebot.nodes.teleportation.teleport_to_xyz as txyz
from bartlebot.nodes import walking
from tests.conftest import FakeClient, FakeMachine, moves

_ZONE = "Aquila/AQ_Atlantea"
_START = {"x": 1000.0, "y": 1000.0, "z": 0.0}
_END = {"x": 1200.0, "y": 1000.0, "z": 0.0}
_PARAMS = {"zone": _ZONE, "start_xyz": _START, "end_xyz": _END}


@pytest.fixture(autouse=True)
def quick_settle(monkeypatch):
    monkeypatch.setattr(walking, "HOP_SETTLE", 0.01)


def _client(zone=_ZONE) -> FakeClient:
    """A client the walk double puts where it walks to, and a teleport puts where it is sent."""
    client = FakeClient()
    client.zone_name.return_value = zone
    client.here = XYZ(0.0, 0.0, 0.0)

    async def position():
        return client.here

    async def walk_to(goal):
        client.here = XYZ(goal.x, goal.y, goal.z)

    async def teleport(xyz, *args, **kwargs):
        client.here = XYZ(xyz.x, xyz.y, xyz.z)

    client.body.position.side_effect = position
    client.walk_to.side_effect = walk_to
    client.teleport.side_effect = teleport
    return client


async def test_every_client_walks_to_the_start_then_hops_to_the_end():
    clients = [_client(), _client()]

    assert await txyz.run(FakeMachine(), clients, _PARAMS) == "SUCCESS"

    for c in clients:
        assert moves(c) == [(1000.0, 1000.0, 0.0), (1200.0, 1000.0, 0.0)]  # the walk, then the one write
        c.teleport.assert_awaited_once()


async def test_a_client_in_another_zone_fails_the_node_and_nobody_moves():
    clients = [_client(), _client("Elsewhere/Z")]

    assert await txyz.run(FakeMachine(), clients, _PARAMS) == "FAILURE"

    assert all(moves(c) == [] for c in clients)


async def test_a_walk_to_the_start_that_fails_means_no_hop():
    client = _client()
    client.walk_to.side_effect = None
    client.walk_to.return_value = WalkEnd.NO_ROUTE

    assert await txyz.run(FakeMachine(), [client], _PARAMS) == "FAILURE"
    client.teleport.assert_not_awaited()


async def test_a_hop_the_game_undoes_fails_the_node():
    client = _client()
    client.teleport.side_effect = None  # the server keeps the wizard where it was

    assert await txyz.run(FakeMachine(), [client], _PARAMS) == "FAILURE"
    client.teleport.assert_awaited_once()


def test_its_help_names_the_limits():
    assert "300" in txyz.NODE_SPEC.usage_notes and "350" in txyz.NODE_SPEC.usage_notes
    assert walking.AVOID_MOBS_OPTION in txyz.NODE_SPEC.required_bot_options
