"""combat_runner's wait for a turn to join: where on its route a player waits, short of the fight."""

import asyncio
import math
from unittest.mock import AsyncMock

import numpy as np
import pytest
from bartleby.runtime.movement.collision import MESH, WALKABLE, Solid
from bartleby.runtime.movement.patrols import Patrol
from bartleby.runtime.movement.route import Leg, ZoneFiles, build_planner
from bartleby.runtime.movement.walk import WalkEnd
from wizwalker import XYZ

from bartlebot.nodes import walking
from bartlebot.nodes.combat_runner import FIGHT_HOLD, fight_patrols, hold_point, wait_turn_near
from tests.conftest import FakeClient, FakeMachine

ZONE = "Test/T_Hold"
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _patrol(*points, proximity=200.0) -> Patrol:
    return Patrol("spawner", "Mob", proximity, 1, np.asarray(points, dtype=float))


def _away(point, mob) -> float:
    return math.dist(point[:2], mob[:2])


# ---------------------------------------------------------------------------
# hold_point
# ---------------------------------------------------------------------------


def test_it_waits_at_the_last_point_still_the_hold_from_the_mob():
    mob = (3000.0, 0.0, 0.0)
    hold = hold_point([Leg([(0.0, 0.0, 0.0), mob])], mob, [])

    assert hold is not None
    assert FIGHT_HOLD <= _away(hold, mob) < FIGHT_HOLD + 30
    assert hold[1] == pytest.approx(0.0)


def test_one_already_that_near_waits_where_it_stands():
    mob = (3000.0, 0.0, 0.0)

    assert hold_point([Leg([(2500.0, 0.0, 0.0), mob])], mob, []) is None


def test_it_keeps_the_hold_from_everywhere_the_mob_patrols():
    mob = (3000.0, 0.0, 0.0)
    patrol = _patrol(mob, (2500.0, 0.0, 0.0))

    hold = hold_point([Leg([(0.0, 0.0, 0.0), mob])], mob, [patrol])

    # The patrol reaches 500 nearer, so the wait is 500 farther back.
    assert hold is not None
    assert FIGHT_HOLD <= 2500.0 - hold[0] < FIGHT_HOLD + 30


def test_it_never_waits_on_a_link_that_lands_it_near():
    mob = (3000.0, 0.0, 0.0)
    legs = [Leg([(0.0, 0.0, 0.0), (300.0, 0.0, 0.0)]), Leg([(2500.0, 0.0, 0.0), mob])]

    assert hold_point(legs, mob, []) is None


def test_it_waits_short_of_the_fight_on_a_later_leg():
    mob = (3000.0, 0.0, 0.0)
    legs = [Leg([(0.0, 0.0, 0.0), (300.0, 0.0, 0.0)]), Leg([(800.0, 0.0, 0.0), mob])]

    hold = hold_point(legs, mob, [])

    assert hold is not None
    assert hold[0] > 800.0
    assert FIGHT_HOLD <= _away(hold, mob) < FIGHT_HOLD + 30


def test_the_fights_patrols_are_those_that_reach_the_mob():
    near = _patrol((3000.0, 0.0, 0.0), (2600.0, 0.0, 0.0))
    far = _patrol((0.0, 0.0, 0.0), (100.0, 0.0, 0.0))

    assert fight_patrols([near, far], (3100.0, 0.0, 0.0)) == [near]


# ---------------------------------------------------------------------------
# wait_turn_near
# ---------------------------------------------------------------------------


def _floor(x0, y0, x1, y1, z=0.0):
    a, b, c, d = (x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)
    return [a, b, c, a, c, d]


@pytest.fixture
def hall(monkeypatch):
    """A hall 4,000 units long, mapped for walking."""
    vertices = _floor(0, 0, 4000, 2000)
    faces = [(k, k + 1, k + 2) for k in range(0, len(vertices), 3)]
    solid = Solid(MESH, WALKABLE, _IDENTITY, (0.0, 0.0, 0.0), 1.0, (), vertices, faces)
    planner = build_planner(ZoneFiles(ZONE, [solid], [], {}))
    monkeypatch.setattr(walking, "has_map", lambda zone: zone == ZONE)
    monkeypatch.setattr(walking, "load_planner", AsyncMock(return_value=planner))


def _client(at) -> FakeClient:
    client = FakeClient()
    client.zone_name = AsyncMock(return_value=ZONE)
    client.body.position = AsyncMock(return_value=XYZ(*at))
    return client


class _Mob:
    def __init__(self, at):
        self.location = AsyncMock(return_value=XYZ(*at))
        self.global_id_full = AsyncMock(return_value=77)


def _machine() -> FakeMachine:
    return FakeMachine(options={walking.AVOID_MOBS: True})


async def test_a_player_walks_to_wait_its_turn_short_of_the_mob(hall):
    client = _client((200.0, 1000.0, 0.0))
    mob = (3800.0, 1000.0, 0.0)

    await wait_turn_near(_machine(), client, 1, _Mob(mob))

    client.walk_to.assert_awaited_once()
    goal = client.walk_to.await_args.args[0]
    assert FIGHT_HOLD <= _away((goal.x, goal.y), mob) < FIGHT_HOLD + 60
    assert goal.x < mob[0]


async def test_a_player_already_near_walks_nowhere(hall):
    client = _client((3000.0, 1000.0, 0.0))

    await wait_turn_near(_machine(), client, 1, _Mob((3800.0, 1000.0, 0.0)))

    client.walk_to.assert_not_called()


async def test_a_zone_with_no_map_walks_nowhere():
    client = FakeClient()  # TestZone, which no map holds

    await wait_turn_near(_machine(), client, 1, _Mob((3800.0, 1000.0, 0.0)))

    client.walk_to.assert_not_called()


async def test_a_gone_target_walks_nowhere(hall):
    client = _client((200.0, 1000.0, 0.0))

    await wait_turn_near(_machine(), client, 1, None)

    client.walk_to.assert_not_called()


async def test_a_failed_read_only_leaves_it_where_it_is(hall):
    client = _client((200.0, 1000.0, 0.0))
    client.body.position = AsyncMock(side_effect=RuntimeError("freed"))

    await wait_turn_near(_machine(), client, 1, _Mob((3800.0, 1000.0, 0.0)))

    client.walk_to.assert_not_called()


async def test_a_fight_met_on_the_way_is_not_waited_out(hall, monkeypatch):
    # handle_combat is never preempted, so a wait for ambient combat would only be dead time.
    monkeypatch.setattr(walking, "FIGHT_PREEMPT_WAIT", 30.0)
    client = _client((200.0, 1000.0, 0.0))
    client.walk_to.return_value = WalkEnd.FIGHT

    await asyncio.wait_for(wait_turn_near(_machine(), client, 1, _Mob((3800.0, 1000.0, 0.0))), 5)

    client.walk_to.assert_awaited_once()


async def test_a_cancel_is_not_swallowed(hall, monkeypatch):
    client = _client((200.0, 1000.0, 0.0))
    walking_started = asyncio.Event()

    async def _walk(_goal):
        walking_started.set()
        await asyncio.Event().wait()

    client.walk_to.side_effect = _walk
    task = asyncio.create_task(wait_turn_near(_machine(), client, 1, _Mob((3800.0, 1000.0, 0.0))))
    await asyncio.wait_for(walking_started.wait(), 5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
