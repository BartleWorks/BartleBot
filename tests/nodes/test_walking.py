"""walk_clients: every client walked at once on core's route walker, on simulated clients.

The rest of the suite walks on ``walk_double`` (``tests/conftest.py``); these put the real
walk back and walk core's ``SimClient`` on planners built here, as core's own walker tests do.
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock

import pytest
from bartleby.runtime.movement.collision import MESH, WALKABLE, Solid
from bartleby.runtime.movement.route import ZoneFiles, build_planner
from bartleby.runtime.movement.walk import WalkEnd, WalkResult
from bartleby.runtime.testing.sim_client import SimClient
from wizwalker import XYZ

from bartlebot.nodes import walking
from bartlebot.options import bot_options
from tests.conftest import FakeClient, FakeMachine

ZONE = "Test/T_Walk"
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _floor(x0, y0, x1, y1, z=0.0):
    a, b, c, d = (x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)
    return [a, b, c, a, c, d]


def _planner():
    """A hall, and an island no floor joins to it."""
    vertices = [*_floor(0, 0, 4000, 2000), *_floor(6000, 0, 7000, 1000)]
    faces = [(k, k + 1, k + 2) for k in range(0, len(vertices), 3)]
    solid = Solid(MESH, WALKABLE, _IDENTITY, (0.0, 0.0, 0.0), 1.0, (), vertices, faces)
    return build_planner(ZoneFiles(ZONE, [solid], [], {}))


@pytest.fixture
def real_walks(monkeypatch):
    """The real walk, on this file's zone. Planners are built once: each walk gets a copy."""
    monkeypatch.setattr(walking, "walk_one", walking._walk_one)
    monkeypatch.setattr(walking, "has_map", lambda zone: zone == ZONE)
    built = _planner()

    async def _load(zone):
        import dataclasses  # noqa: PLC0415

        return dataclasses.replace(
            built, passable=built.passable.copy(), links=list(built.links), closed=[], blocks=[], _took={}
        )

    monkeypatch.setattr(walking, "load_planner", _load)
    monkeypatch.setattr(walking, "FIGHT_PREEMPT_WAIT", 0.05)


def _client(at=(200.0, 1000.0, 0.0)) -> SimClient:
    # A mount's +100%, so a walk goes 1,200 units a second; the walk never writes it.
    sim = SimClient(position=at, zone=ZONE, speed=100)
    sim.teleport = AsyncMock()
    return sim


def _machine(**options) -> FakeMachine:
    return FakeMachine(options={walking.AVOID_MOBS: True, **options})


async def test_every_client_walks_at_once_each_from_where_it_stands(real_walks):
    a, b = _client((200.0, 500.0, 0.0)), _client((200.0, 1500.0, 0.0))
    started = time.monotonic()

    walked = await walking.walk_clients(_machine(), [a, b], (1400.0, 1000.0, 0.0))

    assert [w.result.end for w in walked] == [WalkEnd.ARRIVED, WalkEnd.ARRIVED]
    assert all(w.ok for w in walked)
    # Each walks about 1,300 units at 1,200 a second: together, not one after the other.
    assert time.monotonic() - started < 2.0
    assert walked[0].result.start[1] == pytest.approx(500.0)
    assert walked[1].result.start[1] == pytest.approx(1500.0)
    a.teleport.assert_not_awaited()
    b.teleport.assert_not_awaited()


async def test_no_route_fails_with_the_zone_and_both_points_and_never_teleports(real_walks):
    client = _client()
    machine = _machine()

    walked = await walking.walk_clients(machine, [client], (6500.0, 500.0, 0.0), numbers=[3])

    assert walked[0].result.end is WalkEnd.NO_ROUTE
    assert not walked[0].ok
    client.teleport.assert_not_awaited()
    assert client.position == (200.0, 1000.0, 0.0)
    message = next(m for m, _ in machine.logs if m.startswith("P3:"))
    assert ZONE in message and "(200, 1000)" in message and "(6500, 500)" in message


async def test_a_zone_with_no_map_is_no_route_and_nothing_walks(real_walks):
    client = _client()
    client.zone = "Test/T_Unmapped"

    walked = await walking.walk_clients(_machine(), [client], (1400.0, 1000.0, 0.0))

    assert walked[0].result.end is WalkEnd.NO_ROUTE
    assert "no walking map" in walked[0].result.detail
    assert client.inputs == []


async def test_a_zone_change_on_the_way_is_reported_and_is_not_arriving(real_walks):
    client = _client()

    async def _door():
        while client.position[0] < 600:
            await asyncio.sleep(0.01)
        client.zone = "Test/T_Other"

    door = asyncio.create_task(_door())
    walked = await walking.walk_clients(_machine(), [client], (3000.0, 1000.0, 0.0))
    await door

    assert walked[0].result.end is WalkEnd.ZONE_CHANGED
    assert not walked[0].ok


async def test_a_walk_into_an_exit_has_done_its_part_standing_there_or_once_moved():
    standing = walking.Walked(WalkResult(WalkEnd.ARRIVED, ZONE, (0, 0, 0), (1, 1, 0)), into_exit=True)
    moved = walking.Walked(WalkResult(WalkEnd.ZONE_CHANGED, ZONE, (0, 0, 0), (1, 1, 0)), into_exit=True)
    elsewhere = walking.Walked(WalkResult(WalkEnd.ZONE_CHANGED, ZONE, (0, 0, 0), (1, 1, 0)), into_exit=False)

    assert standing.ok and moved.ok
    assert not elsewhere.ok


async def test_a_fight_on_the_way_waits_for_ambient_combat_to_take_the_node_over(real_walks):
    client = _client()
    machine = _machine()

    async def _ambush():
        while client.position[0] < 600:
            await asyncio.sleep(0.01)
        client.battle = True

    ambush = asyncio.create_task(_ambush())
    node = asyncio.create_task(walking.walk_clients(machine, [client], (3000.0, 1000.0, 0.0)))
    await ambush
    # The watcher's preempt arrives while the node waits: the cancel ends it, with no failure logged.
    await asyncio.sleep(0.01)
    node.cancel()
    with pytest.raises(asyncio.CancelledError):
        await node
    assert not any(m.startswith("FAILURE") for m, _ in machine.logs)
    assert client.held == set()


async def test_a_fight_nothing_takes_over_fails_after_the_wait(real_walks):
    client = _client()
    client.battle = True
    machine = _machine()

    walked = await walking.walk_clients(machine, [client], (3000.0, 1000.0, 0.0))

    assert walked[0].result.end is WalkEnd.FIGHT
    assert any(m.startswith("FAILURE: a fight started on the walk of P1") for m, _ in machine.logs)


async def test_a_walk_writes_no_speed(real_walks):
    assert walking.WALKS_WRITE_SPEED is False
    client = _client()

    await walking.walk_clients(_machine(), [client], (1400.0, 1000.0, 0.0))

    assert [r for r in client.inputs if r.kind == "speed"] == []
    assert client.speed == 100


@pytest.mark.parametrize("avoid", [True, False])
async def test_avoid_mobs_plans_around_patrols_and_watches_mobs_only_when_on(real_walks, monkeypatch, avoid):
    made = []
    real = walking.RouteWalker

    def _walker(*args, **kwargs):
        made.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(walking, "RouteWalker", _walker)

    await walking.walk_clients(_machine(**{walking.AVOID_MOBS: avoid}), [_client()], (1400.0, 1000.0, 0.0))

    assert made[0]["avoid"] is avoid and made[0]["watch_mobs"] is avoid


async def test_each_walk_holds_a_movement_lease_on_the_runs_arbiter(real_walks):
    client = _client()
    machine = _machine()
    held = []
    machine.arbiter.subscribe(lambda key, lease: held.append(lease.owner.name if lease else None))

    await walking.walk_clients(machine, [client], (1400.0, 1000.0, 0.0))

    assert held == ["MOVEMENT", None]


def test_avoid_mobs_resolves_through_the_four_levels():
    """The user's override, the bot's declaration, the app setting, then the spec's True."""
    from bartlebot.bot.schema import BotConfig, BotOptionModel, StateModel  # noqa: PLC0415

    def _config(declared=None):
        options = [] if declared is None else [BotOptionModel(name=walking.AVOID_MOBS, type="bool", default=declared)]
        return BotConfig(
            id="b",
            name="B",
            initial="a",
            recovery_state="a",
            states=[StateModel(id="a", node_type="interact", params={})],
            transitions=[],
            options=options,
        )

    def resolve(config, **kw):
        return bot_options.resolve_for_config(config, walking.AVOID_MOBS, **kw).value

    assert bot_options.APP_SETTING_KEYS[walking.AVOID_MOBS] == "default_avoid_mobs"
    assert resolve(_config()) is True
    assert resolve(_config(), app_value=False) is False
    assert resolve(_config(declared=True), app_value=False) is True
    assert resolve(_config(declared=True), app_value=False, override=False) is False


async def test_a_hooked_clients_first_fight_check_and_key_read_are_made_ahead():
    client = _client()
    read = AsyncMock(return_value=False)
    client.in_battle = read

    await walking.warm_lookups(client)

    read.assert_awaited_once()


async def test_warming_a_client_that_cannot_be_read_raises_nothing():
    client = _client()
    client.in_battle = AsyncMock(side_effect=OSError("closed"))

    await walking.warm_lookups(client)


class _GateUnderGoal:
    """A walker whose first walk finds its goal on a closed gate's floor, as core's does for an
    interact point inside the object the client lists."""

    walks: list = []

    def __init__(self, client, planner, **kwargs):
        self.client = client

    async def walk(self, lease, goal, **_kw):
        from bartleby.runtime.movement.gates import Crossing, NoRoute  # noqa: PLC0415

        _GateUnderGoal.walks.append(tuple(goal))
        if len(_GateUnderGoal.walks) == 1:
            crossing = Crossing(before=(1300.0, 1000.0, 0.0), after=self.after(goal), length=40.0)
            why = NoRoute(ZONE, (200.0, 1000.0, 0.0), tuple(goal), gate="Pedestal", crossing=crossing)
            return WalkResult(WalkEnd.NO_ROUTE, ZONE, (200.0, 1000.0, 0.0), tuple(goal), no_route=why)
        return WalkResult(WalkEnd.ARRIVED, ZONE, (200.0, 1000.0, 0.0), tuple(goal), position=tuple(goal))

    @staticmethod
    def after(goal):
        return tuple(goal)


@pytest.fixture
def gate_under_goal(real_walks, monkeypatch):
    _GateUnderGoal.walks = []
    monkeypatch.setattr(walking, "RouteWalker", _GateUnderGoal)
    return _GateUnderGoal


async def test_an_interact_point_inside_its_object_is_walked_to_the_objects_edge(gate_under_goal):
    walked = await walking.walk_clients(_machine(), [_client()], (1340.0, 1000.0, 0.0), approach=True)

    assert walked[0].ok
    assert gate_under_goal.walks == [(1340.0, 1000.0, 0.0), (1300.0, 1000.0, 0.0)]


async def test_a_walk_that_is_no_approach_stops_at_the_gate(gate_under_goal):
    walked = await walking.walk_clients(_machine(), [_client()], (1340.0, 1000.0, 0.0))

    assert walked[0].result.end is WalkEnd.NO_ROUTE
    assert gate_under_goal.walks == [(1340.0, 1000.0, 0.0)]


async def test_a_gate_between_and_not_under_the_point_is_not_walked_up_to(gate_under_goal, monkeypatch):
    monkeypatch.setattr(gate_under_goal, "after", staticmethod(lambda goal: (900.0, 1000.0, 0.0)))

    walked = await walking.walk_clients(_machine(), [_client()], (1340.0, 1000.0, 0.0), approach=True)

    assert walked[0].result.end is WalkEnd.NO_ROUTE
    assert gate_under_goal.walks == [(1340.0, 1000.0, 0.0)]


async def test_each_walk_is_a_span_in_the_run_trace(real_walks, monkeypatch, tmp_path):
    import json  # noqa: PLC0415

    from bartleby.runtime import run_trace  # noqa: PLC0415

    monkeypatch.setenv("BARTLEBOT_DEV", "1")
    client = _client()
    path = run_trace.start(lambda c: 1, tmp_path)
    try:
        await walking.walk_clients(_machine(), [client], (1400.0, 1000.0, 0.0))
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    walk = next(r for r in records if r.get("kind") == "span" and r.get("name") == "walk")
    text = json.dumps(walk)
    for field in ("zone", "end", "distance", "seconds", "stalls", "detours"):
        assert field in text, field
    assert '"arrived"' in text


def test_every_node_that_walks_declares_avoid_mobs():
    """A node whose run reaches a walk offers the option that shapes it (nodes/CLAUDE.md)."""
    import inspect  # noqa: PLC0415

    from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS  # noqa: PLC0415

    walks = (
        "reach_prompt(",
        "reach_sigil_prompt(",
        "clear_stale_prompt(",
        "take_turns(",
        "walk_clients(",
        "walk_all(",
        "fire_trigger(",
        "navigate(",
        "enter_zone.run(",
        "navigate_to_potion_vendor(",
    )
    missing = []
    for node_type, run in NODE_REGISTRY.items():
        source = inspect.getsource(inspect.getmodule(run))
        if any(w in source for w in walks):
            if not any(o.name == walking.AVOID_MOBS for o in NODE_SPECS[node_type].required_bot_options):
                missing.append(node_type)
    assert missing == []


# --- the hop (Stage 7 of #322) -------------------------------------------------------------------

_START = (1000.0, 1000.0, 0.0)
_END = (1250.0, 1000.0, 0.0)  # 250 past the start, inside HOP_SPAN


def _hopper(at=_START, zone="Test/Hop") -> FakeClient:
    """A client standing at *at*, whose position moves to wherever it is teleported."""
    client = FakeClient()
    client.zone_name.return_value = zone
    client.here = XYZ(*at)

    async def position():
        return client.here

    async def teleport(xyz, *args, **kwargs):
        client.here = XYZ(xyz.x, xyz.y, xyz.z)

    client.body.position = AsyncMock(side_effect=position)
    client.teleport = AsyncMock(side_effect=teleport)
    return client


@pytest.fixture
def quick_settle(monkeypatch):
    monkeypatch.setattr(walking, "HOP_SETTLE", 0.05)


def test_hop_max_and_the_start_tolerance_are_pinned():
    """The user's numbers of 2026-11-26. Read from the source too, so a change to either is a
    change to this test, seen in review."""
    import re  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    assert walking.HOP_MAX == 350.0
    assert walking.HOP_START_TOLERANCE == 50.0
    assert walking.HOP_SPAN == 300.0
    source = Path(walking.__file__).read_text(encoding="utf-8")
    assert re.search(r"^HOP_MAX = 350\.0$", source, re.M)


def test_a_chain_of_hops_never_outruns_walking():
    assert walking.HOP_SETTLE == 1.0
    assert walking.HOP_MAX / walking.HOP_SETTLE < walking.WALKING_PACE


async def test_a_hop_lands_with_one_write(quick_settle):
    client, machine = _hopper(), FakeMachine()

    assert await walking.hop(machine, client, "Test/Hop", _START, _END) is True

    client.teleport.assert_awaited_once()
    assert tuple(client.teleport.await_args.args[0]) == _END


async def test_a_hop_reads_back_only_once_it_has_settled(monkeypatch):
    """Right after the write the position reads as the end whether or not the server keeps it.
    The server puts this one back a moment later, and the hop has to see that."""
    monkeypatch.setattr(walking, "HOP_SETTLE", 0.2)
    client, machine = _hopper(), FakeMachine()

    async def teleport(xyz, *args, **kwargs):
        client.here = XYZ(xyz.x, xyz.y, xyz.z)

        async def snap_back():
            await asyncio.sleep(0.05)
            client.here = XYZ(*_START)

        asyncio.get_running_loop().create_task(snap_back())

    client.teleport.side_effect = teleport

    assert await walking.hop(machine, client, "Test/Hop", _START, _END) is False
    client.teleport.assert_awaited_once()  # never written again
    assert any("was undone" in m and lvl == logging.WARNING for m, lvl in machine.logs)


async def test_a_hop_the_server_undoes_fails_and_is_not_retried(quick_settle):
    client, machine = _hopper(), FakeMachine()
    client.teleport.side_effect = None  # the write lands nowhere: the wizard stays put

    assert await walking.hop(machine, client, "Test/Hop", _START, _END) is False
    client.teleport.assert_awaited_once()


@pytest.mark.parametrize(
    ("setup", "why"),
    [
        # Standing far from the end, whatever the start param says.
        (lambda c: setattr(c, "here", XYZ(600.0, 1000.0, 0.0)), "past HOP_MAX"),
        # Near enough to the end, but not at the node's start.
        (lambda c: setattr(c, "here", XYZ(1100.0, 1100.0, 0.0)), "from the start"),
        (lambda c: setattr(c.zone_name, "return_value", "Test/Elsewhere"), "Test/Elsewhere"),
        (lambda c: setattr(c.is_loading, "return_value", True), "loading"),
        (lambda c: setattr(c.in_battle, "return_value", True), "fight"),
    ],
)
async def test_a_hop_that_fails_a_check_writes_nothing(quick_settle, setup, why):
    client, machine = _hopper(), FakeMachine()
    setup(client)

    assert await walking.hop(machine, client, "Test/Hop", _START, _END) is False

    client.teleport.assert_not_awaited()
    assert any(why in m and lvl == logging.WARNING for m, lvl in machine.logs), machine.logs


async def test_a_hop_measures_from_where_the_wizard_stands_not_the_start_param(quick_settle):
    """A start param close to the end cannot stretch a hop from somewhere else."""
    client, machine = _hopper(at=(700.0, 1000.0, 0.0)), FakeMachine()

    assert await walking.hop(machine, client, "Test/Hop", (1240.0, 1000.0, 0.0), _END) is False
    client.teleport.assert_not_awaited()


async def test_a_hop_writes_nothing_while_another_lease_holds_the_client(quick_settle):
    from bartleby.runtime.arbiter import Owner  # noqa: PLC0415

    client, machine = _hopper(), FakeMachine()
    held, release = asyncio.Event(), asyncio.Event()

    async def hold():
        async with machine.arbiter.acquire(client, Owner.COMBAT):
            held.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await held.wait()
    try:
        assert await walking.hop(machine, client, "Test/Hop", _START, _END) is False
    finally:
        release.set()
        await holder
    client.teleport.assert_not_awaited()


async def test_a_failed_write_fails_the_hop_and_is_not_retried(quick_settle):
    client, machine = _hopper(), FakeMachine()
    client.teleport.side_effect = ValueError("Tried to teleport while should update bool is set")

    assert await walking.hop(machine, client, "Test/Hop", _START, _END) is False
    client.teleport.assert_awaited_once()


def _hop_state(start, end):
    from bartlebot.bot.schema import StateModel  # noqa: PLC0415

    def xyz(p):
        return {"x": p[0], "y": p[1], "z": p[2]}

    return StateModel(id="h", node_type="teleport_to_xyz", params={"start_xyz": xyz(start), "end_xyz": xyz(end)})


def test_a_teleport_to_xyz_spanning_past_hop_span_is_an_error():
    assert walking.span_error(_hop_state(_START, (1300.0, 1000.0, 0.0))) is None
    too_far = walking.span_error(_hop_state(_START, (1301.0, 1000.0, 0.0)))
    assert too_far is not None and "301 apart" in too_far
    # In three dimensions, as hop measures.
    assert walking.span_error(_hop_state(_START, (1250.0, 1000.0, 200.0))) is not None


def test_only_a_teleport_to_xyz_with_both_points_has_a_span():
    from bartlebot.bot.schema import StateModel  # noqa: PLC0415

    assert walking.span_error(StateModel(id="w", node_type="walk_to_xyz", params={})) is None
    assert walking.span_error(StateModel(id="h", node_type="teleport_to_xyz", params={"start_xyz": None})) is None


async def test_a_fight_met_on_a_walk_by_hand_waits_for_nothing(monkeypatch):
    """Outside a run nothing preempts a walk, so walk_clients does not wait for one."""

    async def fought(machine, client, number, goal, *args, **kwargs):
        return walking.Walked(WalkResult(WalkEnd.FIGHT, ZONE, (0.0, 0.0, 0.0), tuple(goal)))

    monkeypatch.setattr(walking, "walk_one", fought)
    monkeypatch.setattr(walking, "FIGHT_PREEMPT_WAIT", 5.0)
    machine = _machine()
    started = time.monotonic()

    await walking.walk_clients(machine, [object()], (1.0, 2.0, 3.0), wait_for_preempt=False)

    assert time.monotonic() - started < 1.0
    assert any("a fight started on the walk" in m for m, _ in machine.logs)


# --- a walk into a fight (Stage 5 of #322) ---------------------------------------------------


def _two_patrols():
    """The hall with two ghosts on patrol: one at x 3000 across y 300 to 700, the fight's, and
    one at x 1500 across y 800 to 1200, on the straight way there."""
    import numpy as np  # noqa: PLC0415
    from bartleby.runtime.movement.patrols import Patrol  # noqa: PLC0415

    vertices = _floor(0, 0, 4000, 2000)
    solid = Solid(MESH, WALKABLE, _IDENTITY, (0.0, 0.0, 0.0), 1.0, (), vertices, [(0, 1, 2), (3, 4, 5)])
    ghosts = [
        Patrol("Ghosts 01", "Ghost", 200, 1, np.array([(3000, 300, 0), (3000, 700, 0)], dtype=float)),
        Patrol("Ghosts 02", "Ghost", 200, 1, np.array([(1500, 800, 0), (1500, 1200, 0)], dtype=float)),
    ]
    return build_planner(ZoneFiles(ZONE, [solid], ghosts, {}))


@pytest.fixture
def two_patrols(real_walks, monkeypatch):
    built = _two_patrols()

    async def _load(zone):
        import dataclasses  # noqa: PLC0415

        return dataclasses.replace(built, passable=built.passable.copy(), closed=[], blocks=[], _took={})

    monkeypatch.setattr(walking, "load_planner", _load)
    return built


FIGHT_MOB, OTHER_MOB = (3000.0, 500.0, 0.0), (1500.0, 1000.0, 0.0)


def _ghosts(client):
    """The two ghosts standing on their paths, and a watch that starts a fight with whichever
    the wizard comes within its trigger radius of, as the server would."""
    from bartleby.runtime.testing.sim_client import SimEntity  # noqa: PLC0415

    fight, other = SimEntity("Ghost", FIGHT_MOB, mob=True), SimEntity("Ghost", OTHER_MOB, mob=True)
    client.entities = [fight, other]
    started: list[tuple[float, float, float]] = []

    async def _trigger():
        while not started:
            for mob in (FIGHT_MOB, OTHER_MOB):
                if ((client.position[0] - mob[0]) ** 2 + (client.position[1] - mob[1]) ** 2) ** 0.5 < 200:
                    started.append(mob)
                    client.battle = True
            await asyncio.sleep(0.01)

    return fight, started, asyncio.create_task(_trigger())


async def test_a_walk_into_a_fight_starts_that_fight_and_ends_there_with_no_wait(two_patrols):
    client = _client()
    fight, started, trigger = _ghosts(client)
    machine = _machine()

    walked = await walking.walk_clients(machine, [client], FIGHT_MOB, fight=True, spare=[fight.gid])
    trigger.cancel()

    assert walked[0].result.end is WalkEnd.FIGHT and walked[0].ok
    assert started == [FIGHT_MOB]
    assert not any("waiting for ambient combat" in m or m.startswith("FAILURE") for m, _ in machine.logs)
    assert client.held == set()


async def test_with_avoid_mobs_on_a_walk_to_anything_else_starts_no_fight(two_patrols):
    client = _client()
    _fight, started, trigger = _ghosts(client)

    walked = await walking.walk_clients(_machine(), [client], (3800.0, 1800.0, 0.0))
    trigger.cancel()

    assert walked[0].result.arrived
    assert started == []


async def test_a_fight_met_during_ambient_combat_waits_for_no_preempt(real_walks):
    client = _client()
    client.battle = True
    machine = _machine()
    machine.is_ambient_combat_active = True

    began = time.monotonic()
    walked = await walking.walk_clients(machine, [client], (3000.0, 1000.0, 0.0))

    assert walked[0].result.end is WalkEnd.FIGHT and not walked[0].ok
    assert time.monotonic() - began < walking.FIGHT_PREEMPT_WAIT
    assert any("during ambient combat" in m for m, _ in machine.logs)


@pytest.mark.parametrize("avoid, clear", [(True, True), (False, False)])
async def test_a_walk_is_clear_of_patrols_only_when_its_planned_route_stays_outside_every_trigger(
    two_patrols, avoid, clear
):
    client = _client(at=(200.0, 1000.0, 0.0))

    # Straight across is through the second ghost's path; avoiding, the plan goes around it.
    assert (
        await walking.passes_clear_of_patrols(_machine(**{walking.AVOID_MOBS: avoid}), client, (2800, 1000, 0)) is clear
    )


async def test_a_walk_with_no_map_counts_as_clear_of_patrols(real_walks):
    client = _client()
    client.zone = "Test/T_Unmapped"

    assert await walking.passes_clear_of_patrols(_machine(), client, (2800, 1000, 0))


async def test_a_walks_span_says_whether_it_was_into_a_fight_and_what_watching_mobs_cost(
    two_patrols, monkeypatch, tmp_path
):
    import json  # noqa: PLC0415

    from bartleby.runtime import run_trace  # noqa: PLC0415

    monkeypatch.setenv("BARTLEBOT_DEV", "1")
    client = _client()
    _fight, _started, trigger = _ghosts(client)
    path = run_trace.start(lambda c: 1, tmp_path)
    try:
        await walking.walk_clients(_machine(), [client], (3800.0, 1800.0, 0.0))
    finally:
        run_trace.stop()
        trigger.cancel()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    walk = next(r for r in records if r.get("kind") == "span" and r.get("name") == "walk")
    assert walk["attrs"]["fight"] is False
    assert walk["attrs"]["mob_reads"] > 0
    for field in ("mob_read_s", "mob_read_max", "list_read_s"):
        assert walk["attrs"][field] >= 0, field
