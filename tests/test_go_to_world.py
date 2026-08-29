"""Unit tests for the go_to_world node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.teleportation.go_to_world as gtw
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list
from tests.conftest import FakeClient, FakeMachine

_RAVENWOOD = "WizardCity/WC_Ravenwood"
_TREE = gtw.WORLD_TREE_ZONE


def _client(zone: str) -> FakeClient:
    c = FakeClient()
    c.zone_name.return_value = zone
    return c


def _route_ok():
    return patch.object(gtw.wizard_city, "go_to_ravenwood", new=AsyncMock(return_value=True))


def _enter_ok():
    return patch.object(enter_zone, "run", new=AsyncMock(return_value="SUCCESS"))


def _list_ok():
    return patch.object(enter_zone_via_list, "run", new=AsyncMock(return_value="SUCCESS"))


# ══════════════════════════════════════════════════════════════════════════════
# Happy path
# ══════════════════════════════════════════════════════════════════════════════


async def test_full_route_to_another_world():
    client = _client("Krokotopia/KT_Z00_Hub")

    with _route_ok() as route, _enter_ok() as enter, _list_ok() as door:
        result = await gtw.run(FakeMachine(), [client], {"world": "Darkmoor"})

    assert result == "SUCCESS"
    route.assert_awaited_once()
    assert enter.await_args.args[2]["to_zone"] == _TREE
    assert door.await_args.args[2]["zone"] == _TREE


async def test_the_door_is_asked_for_the_world_in_every_locale():
    """A client running in French lists Mornelande; the English name alone would miss it."""
    client = _client("Krokotopia/KT_Z00_Hub")

    with _route_ok(), _enter_ok(), _list_ok() as door:
        await gtw.run(FakeMachine(), [client], {"world": "Darkmoor"})

    params = door.await_args.args[2]
    assert params["zone_list_entry"] == "Darkmoor"
    assert "Mornelande" in params["zone_list_aliases"]


async def test_arrival_is_verified_against_the_world_not_a_named_zone():
    """A glob keeps the node working when KI moves a world's spiral chamber."""
    client = _client("WizardCity/WC_Hub")

    with _route_ok(), _enter_ok(), _list_ok() as door:
        await gtw.run(FakeMachine(), [client], {"world": "Azteca"})

    assert door.await_args.args[2]["to_zone"] == "Azteca/*"


# ══════════════════════════════════════════════════════════════════════════════
# Short circuits
# ══════════════════════════════════════════════════════════════════════════════


async def test_already_in_the_world_moves_nobody():
    client = _client("Darkmoor/DM_Z01_Graveholm")

    with _route_ok() as route, _enter_ok() as enter, _list_ok() as door:
        result = await gtw.run(FakeMachine(), [client], {"world": "Darkmoor"})

    assert result == "SUCCESS"
    route.assert_not_awaited()
    enter.assert_not_awaited()
    door.assert_not_awaited()


async def test_one_client_out_of_the_world_still_travels():
    """The short circuit is all-or-nothing: a straggler means everyone takes the trip."""
    clients = [_client("Darkmoor/DM_Z01_Graveholm"), _client("Krokotopia/KT_Z00_Hub")]

    with _route_ok() as route, _enter_ok(), _list_ok():
        result = await gtw.run(FakeMachine(), clients, {"world": "Darkmoor"})

    assert result == "SUCCESS"
    route.assert_awaited_once()


async def test_castle_darkmoor_is_not_darkmoor():
    """G14_DM is a side dungeon, so a client standing in it genuinely has to travel."""
    client = _client("G14_DM/DM_Z01_CastleDarkmoor")

    with _route_ok() as route, _enter_ok(), _list_ok():
        result = await gtw.run(FakeMachine(), [client], {"world": "Darkmoor"})

    assert result == "SUCCESS"
    route.assert_awaited_once()


async def test_wizard_city_stops_at_ravenwood_without_using_the_door():
    """Ravenwood is already in Wizard City, and the door cannot take you where you are."""
    client = _client("Krokotopia/KT_Z00_Hub")

    with _route_ok() as route, _enter_ok() as enter, _list_ok() as door:
        result = await gtw.run(FakeMachine(), [client], {"world": "WizardCity"})

    assert result == "SUCCESS"
    route.assert_awaited_once()
    # Neither the hop into the World Tree chamber nor the door itself.
    enter.assert_not_awaited()
    door.assert_not_awaited()


async def test_wizard_city_does_not_stop_in_the_dorm():
    """The dorm matches WizardCity/* but is not a place a bot can carry on from."""
    client = _client("Krokotopia/KT_Z00_Hub")
    seen: list[str] = []

    async def record(machine, clients):
        seen.append("ravenwood")
        return True

    with patch.object(gtw.wizard_city, "go_to_ravenwood", new=AsyncMock(side_effect=record)):
        result = await gtw.run(FakeMachine(), [client], {"world": "WizardCity"})

    assert result == "SUCCESS"
    assert seen == ["ravenwood"]


# ══════════════════════════════════════════════════════════════════════════════
# Failure paths
# ══════════════════════════════════════════════════════════════════════════════


async def test_failing_to_reach_ravenwood_stops_before_the_world_tree():
    client = _client("Krokotopia/KT_Z00_Hub")
    route_fail = patch.object(gtw.wizard_city, "go_to_ravenwood", new=AsyncMock(return_value=False))

    with route_fail, _enter_ok() as enter, _list_ok() as door:
        result = await gtw.run(FakeMachine(), [client], {"world": "Darkmoor"})

    assert result == "FAILURE"
    enter.assert_not_awaited()
    door.assert_not_awaited()


async def test_failing_to_enter_the_world_tree_stops_before_the_door():
    client = _client("Krokotopia/KT_Z00_Hub")
    enter_fail = patch.object(enter_zone, "run", new=AsyncMock(return_value="FAILURE"))

    with _route_ok(), enter_fail, _list_ok() as door:
        result = await gtw.run(FakeMachine(), [client], {"world": "Darkmoor"})

    assert result == "FAILURE"
    door.assert_not_awaited()


async def test_a_world_the_door_will_not_offer_fails():
    """The usual cause is a character that has not unlocked the world yet."""
    client = _client("Krokotopia/KT_Z00_Hub")
    door_fail = patch.object(enter_zone_via_list, "run", new=AsyncMock(return_value="FAILURE"))

    with _route_ok(), _enter_ok(), door_fail:
        result = await gtw.run(FakeMachine(), [client], {"world": "Darkmoor"})

    assert result == "FAILURE"


async def test_a_world_with_no_known_name_fails_before_moving_anyone():
    """No display text means nothing to search the door for — do not walk the route first."""
    client = _client("Krokotopia/KT_Z00_Hub")

    with _route_ok() as route, _enter_ok(), _list_ok():
        result = await gtw.run(FakeMachine(), [client], {"world": "NotAWorld"})

    assert result == "FAILURE"
    route.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════════
# Node spec
# ══════════════════════════════════════════════════════════════════════════════


def test_every_offered_world_is_one_the_node_can_reach():
    """The dropdown and the lookup table must not drift apart."""
    from bartlebot import worlds

    choices = gtw.PARAM_SCHEMA[0].choices
    assert [value for value, _label in choices] == list(worlds.SPIRAL_DOOR_WORLDS)
    assert all(worlds.display_names(value) for value, _label in choices)


def test_the_default_world_is_a_valid_choice():
    default = gtw.PARAM_SCHEMA[0].default

    assert default in [value for value, _label in gtw.PARAM_SCHEMA[0].choices]
