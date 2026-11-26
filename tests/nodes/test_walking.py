"""walk_clients: every client walked at once on core's route walker, on simulated clients.

The rest of the suite walks on ``walk_double`` (``tests/conftest.py``); these put the real
walk back and walk core's ``SimClient`` on planners built here, as core's own walker tests do.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from bartleby.runtime.movement.collision import MESH, WALKABLE, Solid
from bartleby.runtime.movement.route import ZoneFiles, build_planner
from bartleby.runtime.movement.walk import WalkEnd, WalkResult
from bartleby.runtime.testing.sim_client import SimClient

from bartlebot.nodes import walking
from bartlebot.options import bot_options
from tests.conftest import FakeMachine

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

    async def walk(self, lease, goal):
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


# --- nothing teleports ---------------------------------------------------------------------------


def test_teleports_are_off_in_the_app():
    """The user's call (2026-11-25): no teleport at all until Stage 7 confirms the short radius.
    Read from the source, since the suite turns it on (``teleports_enabled``)."""
    import re  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    source = Path(walking.__file__).read_text(encoding="utf-8")
    assert re.search(r"^TELEPORTS_ENABLED = False$", source, re.M)


async def test_a_teleport_while_they_are_off_is_refused_and_writes_nothing(no_teleports):
    client = _client()
    client.tp_to = AsyncMock()

    with pytest.raises(walking.TeleportRefused, match=r"teleport to \(10, 20, 30\) was refused"):
        await walking.teleport(client, (10.0, 20.0, 30.0))
    with pytest.raises(walking.TeleportRefused):
        await walking.teleport(client, (10.0, 20.0, 30.0), how="tp_to")

    client.teleport.assert_not_awaited()
    client.tp_to.assert_not_awaited()


async def test_a_teleport_while_they_are_on_goes_through(monkeypatch):
    client = _client()

    await walking.teleport(client, (10.0, 20.0, 30.0), wait_on_inuse=True)

    client.teleport.assert_awaited_once_with((10.0, 20.0, 30.0), wait_on_inuse=True)


def test_every_position_write_goes_through_the_guard():
    """No ``.teleport(`` or ``.tp_to(`` call anywhere in bartlebot/ but walking.teleport's own."""
    import ast  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path(walking.__file__).resolve().parents[1]
    found = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("teleport", "tp_to") and not (
                    isinstance(node.func.value, ast.Name) and node.func.value.id == "walking"
                ):
                    found.append(f"{path.relative_to(root)}:{node.lineno}")
    # walking.teleport's getattr(client, how) is the one write, and no attribute call.
    assert found == []
