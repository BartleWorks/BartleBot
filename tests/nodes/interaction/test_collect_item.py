"""Unit tests for the collect_item node."""

from unittest.mock import AsyncMock, patch

from wizwalker import XYZ, Keycode

import bartlebot.nodes.interaction.collect_item as ci
from bartlebot.content.bot_tags import suggested_tags
from tests.conftest import FakeClient, FakeEntity, FakeMachine, zone_unreadable_at_first

_ITEM = "AQ-TART-UrnPiece1-Maze"
_PARAMS = {"zone": "TestZone", "entity_names": [_ITEM], "collect_client": 1}

_NEAR = XYZ(100.0, 0.0, 0.0)
_FAR = XYZ(4000.0, 0.0, 0.0)


def _entity(xyz: XYZ = _NEAR, name: str = _ITEM) -> FakeEntity:
    e = FakeEntity(name)
    e.location = AsyncMock(return_value=xyz)
    return e


def _spawn(client: FakeClient, entities: list[FakeEntity]) -> None:
    """Load *entities* where *client* can see them; each search keeps those its predicate accepts."""

    async def _search(predicate):
        return [e for e in entities if await predicate(e)]

    client.get_base_entities_with_predicate.side_effect = _search


def _quick():
    return patch.object(ci, "POLL_INTERVAL", 0)


async def test_picks_up_the_item_and_succeeds():
    client = FakeClient()
    _spawn(client, [_entity()])
    client.is_in_npc_range.side_effect = [True, False]

    with _quick():
        result = await ci.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once_with(_NEAR)
    client.send_key.assert_called_once_with(Keycode.X, 0.1)


async def test_no_item_in_range_fails_after_the_wait():
    """For a quest item, none showing is the route's to handle: search the next area."""
    client = FakeClient()
    _spawn(client, [])

    with _quick():
        result = await ci.run(FakeMachine(options={"Safety Delay": 0.05}), [client], _PARAMS)

    assert result == "FAILURE"
    client.send_key.assert_not_called()


async def test_an_item_that_shows_late_is_still_found():
    client = FakeClient()
    item = _entity()
    shown = iter([[], [], [item]])

    async def _search(predicate):
        return [e for e in next(shown, [item]) if await predicate(e)]

    client.get_base_entities_with_predicate.side_effect = _search
    client.is_in_npc_range.side_effect = [True, False]

    with _quick():
        result = await ci.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_the_nearest_item_is_taken_first():
    client = FakeClient()
    client.body.position = AsyncMock(return_value=XYZ(0.0, 0.0, 0.0))
    _spawn(client, [_entity(_FAR), _entity(_NEAR)])
    client.is_in_npc_range.side_effect = [True, False]

    with _quick():
        result = await ci.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once_with(_NEAR)


async def test_an_item_that_will_not_pick_up_moves_on_to_the_next():
    client = FakeClient()
    _spawn(client, [_entity(_NEAR), _entity(_FAR)])
    # The first shows no prompt in the whole wait; the second picks up.
    prompts = {_NEAR: False, _FAR: True}
    where = {"at": None}

    async def _teleport(xyz):
        where["at"] = xyz

    picked = {"done": False}

    async def _in_range():
        return prompts[where["at"]] and not picked["done"]

    async def _send_key(key, seconds):
        picked["done"] = True

    client.teleport.side_effect = _teleport
    client.is_in_npc_range.side_effect = _in_range
    client.send_key.side_effect = _send_key

    with _quick():
        result = await ci.run(FakeMachine(options={"Safety Delay": 0.05}), [client], _PARAMS)

    assert result == "SUCCESS"
    assert [c.args[0] for c in client.teleport.call_args_list] == [_NEAR, _FAR]


async def test_a_pickup_that_never_takes_fails():
    client = FakeClient()
    _spawn(client, [_entity()])
    client.is_in_npc_range.return_value = True  # the prompt never clears after X

    with _quick():
        result = await ci.run(FakeMachine(options={"Safety Delay": 0.05}), [client], _PARAMS)

    assert result == "FAILURE"


async def test_only_the_collecting_player_moves():
    stays, collects = FakeClient(), FakeClient()
    _spawn(collects, [_entity()])
    collects.is_in_npc_range.side_effect = [True, False]
    start = {"x": 1.0, "y": 2.0, "z": 3.0}

    with _quick():
        result = await ci.run(FakeMachine(), [stays, collects], {**_PARAMS, "collect_client": 2, "xyz": start})

    assert result == "SUCCESS"
    stays.teleport.assert_not_called()
    first = collects.teleport.call_args_list[0].args[0]
    assert (first.x, first.y, first.z) == (1.0, 2.0, 3.0)


async def test_a_collecting_player_who_is_not_connected_fails():
    client = FakeClient()

    result = await ci.run(FakeMachine(), [client], {**_PARAMS, "collect_client": 3})

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_wrong_zone_fails():
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    result = await ci.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = FakeClient()
    zone_unreadable_at_first(client, _PARAMS["zone"])
    machine = FakeMachine()

    # Blank names fail the step after the zone check, which is what shows the check passed.
    await ci.run(machine, [client], {**_PARAMS, "entity_names": [" "]})

    assert [msg for msg, _ in machine.logs if msg.startswith("FAILURE")] == ["FAILURE: entity_names is empty"]


async def test_blank_entity_names_fails():
    client = FakeClient()

    result = await ci.run(FakeMachine(), [client], {**_PARAMS, "entity_names": [" "]})

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_a_pickup_records_no_drop():
    client = FakeClient()
    _spawn(client, [_entity()])
    client.is_in_npc_range.side_effect = [True, False]
    machine = FakeMachine()

    with _quick():
        await ci.run(machine, [client], _PARAMS)

    assert machine.drops == []


def test_suggests_no_tag():
    """A quest item is not a reward: it suggests neither Reagents nor Collecting."""

    class _State:
        node_type = "collect_item"
        params = _PARAMS

    assert suggested_tags([_State()]) == []
