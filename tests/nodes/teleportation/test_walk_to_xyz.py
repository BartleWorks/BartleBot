"""Unit tests for the walk_to_xyz node: every client walks there, on the suite's walking double."""

import logging

from bartleby.runtime.movement.walk import WalkEnd

import bartlebot.nodes.teleportation.walk_to_xyz as wxyz
from bartlebot.nodes import walking
from tests.conftest import FakeClient, FakeMachine, moves, zone_unreadable_at_first

_ZONE = "WizardCity/WC_Hub"
_PARAMS = {"zone": _ZONE, "xyz": {"x": 100.0, "y": 200.0, "z": 0.0}}


def _client(zone=_ZONE) -> FakeClient:
    client = FakeClient()
    client.zone_name.return_value = zone
    return client


async def test_every_client_walks_to_the_spot():
    clients = [_client(), _client()]

    assert await wxyz.run(FakeMachine(), clients, _PARAMS) == "SUCCESS"

    for c in clients:
        assert moves(c) == [(100.0, 200.0, 0.0)]
        c.teleport.assert_not_awaited()


async def test_a_client_in_another_zone_fails_the_node_and_nobody_walks():
    clients = [_client(), _client("Elsewhere/Z")]

    assert await wxyz.run(FakeMachine(), clients, _PARAMS) == "FAILURE"

    assert all(moves(c) == [] for c in clients)


async def test_a_walk_that_does_not_arrive_fails_the_node():
    client = _client()
    client.walk_to.return_value = WalkEnd.NO_ROUTE
    machine = FakeMachine()

    assert await wxyz.run(machine, [client], _PARAMS) == "FAILURE"
    assert ("FAILURE: not every player could walk to XYZ", logging.WARNING) in machine.logs
    client.teleport.assert_not_awaited()


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = FakeClient()
    zone_unreadable_at_first(client, _ZONE)

    assert await wxyz.run(FakeMachine(), [client], _PARAMS) == "SUCCESS"


def test_it_walks_and_so_declares_avoid_mobs():
    assert walking.AVOID_MOBS_OPTION in wxyz.NODE_SPEC.required_bot_options
