"""walk_clients porting instead of walking: a long walk to one of ours becomes a port (Stage 3 of #340).

Real walks on core's ``SimClient``, on ``test_walking``'s hall, with the port itself faked:
``walking.port`` puts the mover where its target stands, as the game's port does. The walks
here are short, so ``PORT_COST`` and ``PORT_MARGIN`` are scaled down by ten (:func:`_scaled`);
the decision at the real values is :func:`test_at_the_real_values_a_port_wins_only_by_the_margin`.
"""

import asyncio
import json
import logging
import time

import pytest
from bartleby.runtime import run_trace
from bartleby.runtime.clients import group_manager, sightings
from bartleby.runtime.clients.group_manager import GroupRecord
from bartleby.runtime.game import wizard_names
from bartleby.runtime.movement.walk import WalkEnd, WalkResult

from bartlebot.clients import port_map
from bartlebot.clients.authoring_tools import _HandWalk
from bartlebot.clients.port_map import Batch, PortMap
from bartlebot.nodes import walking
from bartlebot.options import player_realms
from tests.conftest import FakeMachine
from tests.nodes.test_walking import ZONE, _client, _planner, real_walks  # noqa: F401 - real_walks is a fixture

# Synthetic names from tools/name_key_samples.json -- never a real player's.
_NAMES = ("Luke SkullHunter", "Misha SkullSeer", "Michelle HistoryBond", "Naomi HexPaint")

GOAL = (3400.0, 1000.0, 0.0)


@pytest.fixture(autouse=True)
def _fresh_names():
    wizard_names.clear_cache()
    group_manager.forget_all_groups()
    yield
    wizard_names.clear_cache()
    group_manager.forget_all_groups()


@pytest.fixture
def _scaled(monkeypatch):
    """A port costs 0.3 s and must save 0.2 s, so a walk over 0.5 s asks: the real values over ten.
    At 1,200 units a second, 600 units is that 0.5 s."""
    monkeypatch.setattr(walking, "PORT_COST", 0.3)
    monkeypatch.setattr(walking, "PORT_MARGIN", 0.2)
    monkeypatch.setattr(walking, "PORT_LONGEST", 0.5)


class _Ports:
    """``walking.port`` faked: each port is recorded, and lands the mover on its target unless
    the test says the zone refuses it or it goes nowhere."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple[object, object]] = []
        self.outcome = "lands"
        monkeypatch.setattr(walking, "port", self)

    async def __call__(self, machine, mover, target, ports) -> list[Batch]:
        self.calls.append((mover, target))
        if self.outcome == "refused":
            return [Batch([mover], target, port_map.FRIEND, [], True, 0.4)]
        if self.outcome == "nowhere":
            return [Batch([mover], target, port_map.FRIEND, [], False, 0.4)]
        mover.place(target.position)
        return [Batch([mover], target, port_map.FRIEND, [mover], False, 0.4)]


@pytest.fixture
def ports(real_walks, _scaled, monkeypatch):  # noqa: F811 - the fixture imported above
    return _Ports(monkeypatch)


def _party(*spots) -> list:
    """A simulated client at each spot, each named, walking 1,200 units a second as
    ``test_walking``'s do."""
    clients = []
    for name, at in zip(_NAMES, spots):
        client = _client(at)
        wizard_names.remember(client, name)
        clients.append(client)
    return clients


def _friends(clients) -> PortMap:
    """Every one of *clients* has the friends-list route to every other."""
    names = [wizard_names.cached_name(c) for c in clients]
    return PortMap.from_survey(names, [names for _ in names], [() for _ in names])


def _run(clients, **options) -> FakeMachine:
    machine = FakeMachine(options={walking.AVOID_MOBS: True, **options})
    machine.clients = list(clients)
    machine.port_map = _friends(clients)
    return machine


def _moved(client) -> bool:
    """Whether any key moved *client*: a walk key held, pressed or let go."""
    return any(record.kind in ("down", "up", "press") for record in client.inputs)


def _no_names(machine) -> None:
    text = " ".join(str(m) for m, *_ in machine.logs)
    assert not any(name in text for name in _NAMES), text


async def test_a_long_walk_to_one_of_ours_at_the_goal_is_a_port_and_presses_no_key(ports):
    mover, anchor = _party((200.0, 1000.0, 0.0), GOAL)
    machine = _run([mover, anchor])

    (walked,) = await walking.walk_clients(machine, [mover], GOAL)

    assert walked.ok
    assert ports.calls == [(mover, anchor)]
    assert not _moved(mover)
    _no_names(machine)


async def test_a_player_short_of_the_goal_is_ported_to_and_the_rest_walked(ports):
    # 3,200 units at 1,200 a second is 2.7 s; a port and the 400 on from the anchor, 0.6 s.
    mover, anchor = _party((200.0, 1000.0, 0.0), (3000.0, 1000.0, 0.0))
    machine = _run([mover, anchor])

    (walked,) = await walking.walk_clients(machine, [mover], GOAL)

    assert walked.ok
    assert ports.calls == [(mover, anchor)]
    assert _moved(mover), "it walks the 400 from where it landed"
    assert walked.result.start[0] == pytest.approx(3000.0)


async def test_a_walk_a_port_does_not_beat_by_the_margin_is_walked(ports):
    # 800 units is 0.67 s, over the 0.5 that asks; the port and walk on, 0.63 s, saves under 0.2.
    mover, anchor = _party((2600.0, 1000.0, 0.0), (3000.0, 1000.0, 0.0))

    (walked,) = await walking.walk_clients(_run([mover, anchor]), [mover], GOAL)

    assert walked.ok
    assert ports.calls == []
    assert walked.result.start[0] == pytest.approx(2600.0)


async def test_at_the_real_values_a_port_wins_only_by_the_margin():
    # A player 400 units from the goal, at 600 a second: a port and the walk on take 5.7 s.
    trip = walking._Target(object(), walking.PORT_COST + 400 / 600)

    assert walking.PORT_COST == 5.0  # the user's pick from the run trace's port spans (2026-11-28)
    assert walking.PORT_LONGEST == walking.PORT_COST + walking.PORT_MARGIN == 7.0
    assert walking._quickest([trip], 12.0) is trip
    assert walking._quickest([trip], 7.5) is None
    assert walking._quickest([], 12.0) is None


async def test_of_two_targets_the_quicker_trip_wins_not_the_nearer_to_the_mover(ports):
    mover, nearer, quicker = _party((200.0, 1000.0, 0.0), (2600.0, 1000.0, 0.0), (3300.0, 1000.0, 0.0))

    await walking.walk_clients(_run([mover, nearer, quicker]), [mover], GOAL)

    assert ports.calls == [(mover, quicker)]


async def test_a_short_walk_is_walked_with_no_search_from_any_target(ports, monkeypatch):
    mover, anchor = _party((3000.0, 1000.0, 0.0), GOAL)
    searched = []
    real = walking.plan_in_thread

    async def spy(fn, *args):
        searched.append(getattr(fn, "__name__", ""))
        return await real(fn, *args)

    monkeypatch.setattr(walking, "plan_in_thread", spy)

    (walked,) = await walking.walk_clients(_run([mover, anchor]), [mover], GOAL)

    assert walked.ok
    assert ports.calls == []
    assert "seconds_to" not in searched


async def test_a_target_whose_straight_line_misses_the_margin_is_never_searched(ports, monkeypatch):
    mover, far = _party((2600.0, 1000.0, 0.0), (1000.0, 1000.0, 0.0))
    searched = []
    real = walking.plan_in_thread

    async def spy(fn, *args):
        searched.append(getattr(fn, "__name__", ""))
        return await real(fn, *args)

    monkeypatch.setattr(walking, "plan_in_thread", spy)

    await walking.walk_clients(_run([mover, far]), [mover], GOAL)

    assert ports.calls == []
    assert "seconds_to" not in searched


async def test_a_walk_into_a_fight_never_ports(ports):
    mover, anchor = _party((2600.0, 1000.0, 0.0), GOAL)

    await walking.walk_clients(_run([mover, anchor]), [mover], GOAL, fight=True)

    assert ports.calls == []
    assert _moved(mover)


async def test_a_walk_into_an_exit_never_ports(ports, monkeypatch):
    mover, anchor = _party((2600.0, 1000.0, 0.0), GOAL)
    load = walking.load_planner

    async def with_an_exit(zone):
        planner = await load(zone)
        planner.exits_at = lambda *at: [object()] if tuple(at) == GOAL else []
        return planner

    monkeypatch.setattr(walking, "load_planner", with_an_exit)

    await walking.walk_clients(_run([mover, anchor]), [mover], GOAL)

    assert ports.calls == []


async def test_long_walkers_port_to_the_first_short_walker_of_the_call_to_arrive(ports):
    long_a, short, slower, long_b = _party(
        (200.0, 1000.0, 0.0), (3200.0, 1000.0, 0.0), (3000.0, 1000.0, 0.0), (300.0, 1200.0, 0.0)
    )
    machine = _run([long_a, short, slower, long_b])

    walked = await walking.walk_clients(machine, [long_a, short, slower, long_b], GOAL)

    assert all(w.ok for w in walked)
    assert sorted(ports.calls, key=lambda c: id(c[0])) == sorted(
        [(long_a, short), (long_b, short)], key=lambda c: id(c[0])
    )
    assert not _moved(long_a) and not _moved(long_b)
    _no_names(machine)


async def test_a_short_walker_that_fails_is_no_anchor_and_the_long_walker_walks(ports):
    mover, short = _party((2400.0, 1000.0, 0.0), (3100.0, 1000.0, 0.0))
    short.battle = True  # every walk of it ends at once in a fight

    walked = await walking.walk_clients(_run([mover, short]), [mover, short], GOAL)

    assert ports.calls == []
    assert walked[0].ok


async def test_a_refused_port_walks_marks_the_zone_and_no_later_walk_there_asks(ports):
    mover, anchor = _party((2600.0, 1000.0, 0.0), GOAL)
    machine = _run([mover, anchor])
    ports.outcome = "refused"

    (walked,) = await walking.walk_clients(machine, [mover], GOAL)

    assert walked.ok and _moved(mover)
    assert machine.no_port_zones == {ZONE}
    mover.place((2600.0, 1000.0, 0.0))
    await walking.walk_clients(machine, [mover], GOAL)
    assert ports.calls == [(mover, anchor)]
    refusals = [m for m, *_ in machine.logs if "refuses ports" in m]
    assert len(refusals) == 1
    _no_names(machine)


async def test_a_port_that_lands_nowhere_walks_and_marks_nothing(ports):
    mover, anchor = _party((2600.0, 1000.0, 0.0), GOAL)
    machine = _run([mover, anchor])
    ports.outcome = "nowhere"

    (walked,) = await walking.walk_clients(machine, [mover], GOAL)

    assert walked.ok and _moved(mover)
    assert machine.no_port_zones == set()


async def test_a_player_with_no_route_from_the_mover_is_no_target(ports):
    mover, anchor = _party((2600.0, 1000.0, 0.0), GOAL)
    machine = _run([mover, anchor])
    machine.port_map = PortMap()

    await walking.walk_clients(machine, [mover], GOAL)

    assert ports.calls == []


@pytest.mark.parametrize("busy", ["fighting", "leased", "elsewhere"])
async def test_a_player_fighting_moving_or_in_another_zone_is_no_target(ports, busy):
    mover, anchor = _party((2600.0, 1000.0, 0.0), GOAL)
    machine = _run([mover, anchor])
    if busy == "fighting":
        anchor.battle = True
    elif busy == "elsewhere":
        anchor.zone = "Test/T_Other"

    if busy == "leased":
        async with machine.arbiter.acquire(anchor, walking.Owner.MOVEMENT):
            await walking.walk_clients(machine, [mover], GOAL)
    else:
        await walking.walk_clients(machine, [mover], GOAL)

    assert ports.calls == []


async def test_with_no_port_map_or_on_a_walk_by_hand_it_walks_as_before(ports):
    mover, anchor = _party((2600.0, 1000.0, 0.0), GOAL)
    machine = _run([mover, anchor])
    machine.port_map = None

    await walking.walk_clients(machine, [mover], GOAL)
    mover.place((2600.0, 1000.0, 0.0))
    by_hand = _HandWalk(machine.arbiter, None)
    by_hand.get_option = lambda name, default=None: True
    await walking.walk_clients(by_hand, [mover], GOAL, wait_for_preempt=False)

    assert ports.calls == []


def test_a_leader_has_no_teleport_to_leader_route_to_its_own_member(monkeypatch):
    leader, member = _party((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    record = GroupRecord(leader=leader, members=(leader, member), names=())
    monkeypatch.setattr(group_manager, "group_record", lambda c: record if c in (leader, member) else None)

    assert not walking._reaches(PortMap(), leader, member), "its leader is itself"
    assert walking._reaches(PortMap(), member, leader), "a member reaches its leader"
    assert walking._reaches(_friends([leader, member]), leader, member), "by the friends list"


async def test_a_port_is_port_waves_with_the_mover_waiting_and_the_target_arrived(monkeypatch):
    mover, target, other = _party((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))
    machine = _run([other, mover, target])
    seen = []

    async def waves(m, clients, waiting, arrived, pm, *, batches=None):
        seen.append((clients, waiting, arrived, pm))
        batches.append(Batch(list(waiting), arrived[0], port_map.FRIEND, list(waiting), False, 1.0))
        return []

    monkeypatch.setattr(port_map, "port_waves", waves)

    batches = await walking.port(machine, mover, target, machine.port_map)

    assert seen == [([other, mover, target], [mover], [target], machine.port_map)]
    assert batches[0].landed == [mover]


async def test_each_port_is_a_span_in_the_run_trace(ports, monkeypatch, tmp_path):
    monkeypatch.setenv("BARTLEBOT_DEV", "1")
    mover, anchor = _party((200.0, 1000.0, 0.0), GOAL)
    path = run_trace.start(lambda c: 1, tmp_path)
    try:
        await walking.walk_clients(_run([mover, anchor]), [mover], GOAL)
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    span = next(r for r in records if r.get("kind") == "span" and r.get("name") == "port")
    text = json.dumps(span)
    for field in ("zone", "route", "seconds", "landed", "refused"):
        assert field in text, field
    walk = next(r for r in records if r.get("kind") == "span" and r.get("name") == "walk")
    assert '"ported": true' in json.dumps(walk)


async def test_a_too_long_first_walk_is_walked_on_its_kept_plan(ports, monkeypatch, caplog):
    mover, far = _party((2600.0, 1000.0, 0.0), (1000.0, 1000.0, 0.0))
    ends = []
    real = walking.RouteWalker.walk

    async def spy(self, lease, goal, **kwargs):
        result = await real(self, lease, goal, **kwargs)
        ends.append((kwargs.get("longest"), result.end))
        return result

    monkeypatch.setattr(walking.RouteWalker, "walk", spy)

    with caplog.at_level(logging.DEBUG, logger="bartleby.runtime.movement.walk"):
        await walking.walk_clients(_run([mover, far]), [mover], GOAL)

    assert ends == [(0.5, WalkEnd.TOO_LONG), (None, WalkEnd.ARRIVED)]
    assert "walking the plan kept from the walk before" in caplog.text


async def test_a_long_walk_to_a_moving_goal_asks_once_where_it_is_ports_and_walks_on_to_it(ports):
    mover, anchor = _party((200.0, 1000.0, 0.0), GOAL)
    machine = _run([mover, anchor])
    reads = []

    async def drifting():
        reads.append(len(reads))
        return (GOAL[0], GOAL[1] + 40.0 * len(reads), GOAL[2])  # a little further on each read

    [walked] = await walking.walk_clients(machine, [mover], drifting, reach=300.0)

    assert walked.ok, walked.describe()
    assert ports.calls == [(mover, anchor)]
    assert len(reads) >= 2  # once for the port question, then by the walks
    _no_names(machine)


async def test_a_target_still_walking_long_after_it_began_never_beats_one_that_has_arrived(_scaled):
    mover, arrived, walking_still = _party((200.0, 1000.0, 0.0), (3380.0, 1000.0, 0.0), (3000.0, 1000.0, 0.0))
    machine = _run([mover, arrived, walking_still])
    call = walking._Call(machine.port_map, [mover, arrived, walking_still], [1, 2, 3])
    call.short(walking_still)
    call._short[id(walking_still)] -= 10.0  # far past PORT_LONGEST: a target still walking is waited for not at all
    call.ended(arrived, True)

    targets = await walking._port_targets(machine, call, mover, ZONE, _planner(), GOAL, True, 2.7, 1200.0)
    best = walking._quickest(targets, 2.7)

    assert {t.client for t in targets} == {arrived, walking_still}
    assert best.client is arrived and not best.waits


# --- Waiting on the call's other movers (found live on 2026-11-27) ---


@pytest.fixture
def real_ports(monkeypatch):
    """``walking.port`` faked, at the real PORT_COST and PORT_MARGIN, with no walk at all: these
    drive ``_port_or_walk`` itself, so a wait that runs to its bound costs the real 5 s."""
    return _Ports(monkeypatch)


def _too_long(mover, planned: float) -> WalkResult:
    """The first walk of *mover* ended TOO_LONG, planned at *planned* seconds, at 1,200 a second."""
    result = WalkResult(WalkEnd.TOO_LONG, ZONE, mover.position, GOAL)
    result.planned, result.speed = planned, 1200.0
    return result


async def _decide(machine, call, mover, planned: float, *also) -> tuple[bool, float]:
    """``_port_or_walk`` for *mover*, its first walk just gone long, beside the coroutines in
    *also*: whether it ported, and how many seconds it took to decide."""
    call.long(mover)
    began = time.monotonic()
    ported, *_ = await asyncio.wait_for(
        asyncio.gather(
            walking._port_or_walk(machine, call, mover, 1, ZONE, _planner(), GOAL, True, _too_long(mover, planned)),
            *also,
        ),
        timeout=15.0,
    )
    return ported, time.monotonic() - began


async def _after_turns(turns: int, then) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)
    then()


@pytest.mark.parametrize("turns", [0, 1, 2, 3, 5])
async def test_a_peer_arriving_while_the_mover_costs_its_targets_is_ported_to_at_once(real_ports, turns):
    # The lost wakeup: a peer whose walk changed in the loop turns between the mover costing it and
    # the mover starting to wait was slept through to the deadline, and the mover then walked
    # the whole way (P4 18.5 s behind in Bale Flotilla, P3 about 38 s in Dark Water Trench).
    mover, peer = _party((200.0, 1000.0, 0.0), (3000.0, 1000.0, 0.0))
    machine = _run([mover, peer])
    call = walking._Call(machine.port_map, [mover, peer], [1, 2])
    call.short(peer)

    def arrive():
        peer.place(GOAL)
        call.ended(peer, True)

    ported, took = await _decide(machine, call, mover, 20.0, _after_turns(turns, arrive))

    assert ported
    assert real_ports.calls == [(mover, peer)]
    assert took < 2.0, f"slept {took:.1f} s through the peer's arrival"


async def test_a_peer_still_planning_is_no_target_until_its_walk_is_known_short(_scaled):
    mover, peer = _party((200.0, 1000.0, 0.0), (3000.0, 1000.0, 0.0))
    machine = _run([mover, peer])
    call = walking._Call(machine.port_map, [mover, peer], [1, 2])

    planning = await walking._port_targets(machine, call, mover, ZONE, _planner(), GOAL, True, 2.7, 1200.0)
    call.short(peer)
    walking_short = await walking._port_targets(machine, call, mover, ZONE, _planner(), GOAL, True, 2.7, 1200.0)

    assert call.planning(peer) is False and call.short_since(peer) is not None
    assert planning == []
    assert [(t.client, t.waits) for t in walking_short] == [(peer, True)]


async def test_a_peer_whose_plan_turns_out_long_too_is_waited_on_only_until_it_says_so(real_ports):
    mover, peer = _party((200.0, 1000.0, 0.0), (210.0, 1000.0, 0.0))
    machine = _run([mover, peer])
    call = walking._Call(machine.port_map, [mover, peer], [1, 2])

    async def plans_long():
        await asyncio.sleep(0.05)
        call.long(peer)

    ported, took = await _decide(machine, call, mover, 20.0, plans_long())

    assert not ported and real_ports.calls == []
    assert took < 2.0
    logged = [m for m, *_ in machine.logs]
    assert "P1: waiting for P2 to plan its walk, to port to it" in logged
    assert "P1: no port beats its 20.0 s walk; walking" in logged


async def test_movers_whose_plans_are_all_long_from_one_spot_walk_at_once_none_waiting_on_another(ports, monkeypatch):
    # Bale Flotilla and Eastern Sea, 2026-11-27: four plans from one spot, all too long, came back
    # together, and one or three movers waited on a peer's plan that was as long as their own.
    party = _party(*[(200.0, 1000.0 + 20.0 * k, 0.0) for k in range(4)])
    machine = _run(party)
    took = []
    real = walking._port_or_walk

    async def timed(*args):
        began = time.monotonic()
        try:
            return await real(*args)
        finally:
            took.append(time.monotonic() - began)

    monkeypatch.setattr(walking, "_port_or_walk", timed)

    walked = await walking.walk_clients(machine, party, GOAL)

    assert all(w.ok for w in walked)
    assert ports.calls == []
    assert len(took) == 4
    assert max(took) < 1.0, took  # a wait slept through would last the bound, 2.2 s here


def test_the_wait_bound_is_mover_wait_longest_at_most_and_never_past_what_a_port_could_save():
    assert walking.MOVER_WAIT_LONGEST == 5.0  # the user's pick, after movers waited up to 18.5 s (2026-11-27)
    assert walking.PORT_LONGEST == 7.0  # costing a port never moves the wait
    assert walking._wait_bound(55.2) == walking.MOVER_WAIT_LONGEST
    assert walking._wait_bound(12.0) == pytest.approx(5.0)
    assert walking._wait_bound(8.0) == pytest.approx(1.0)
    assert walking._wait_bound(walking.PORT_LONGEST) == pytest.approx(0.0)


@pytest.mark.parametrize("peer_is", ["walking, and never arrives", "planning, and never finishes"])
async def test_a_wait_that_comes_to_nothing_ends_at_the_bound_and_the_mover_walks(ports, peer_is, monkeypatch):
    # The bound is the ceiling under test, so it is compressed here alone, to 0.5 s. Waiting until
    # a port no longer saved the margin, as before, was 19.5 s of this 20 s walk.
    monkeypatch.setattr(walking, "MOVER_WAIT_LONGEST", 0.5)
    mover, peer = _party((200.0, 1000.0, 0.0), (3000.0, 1000.0, 0.0))
    machine = _run([mover, peer])
    call = walking._Call(machine.port_map, [mover, peer], [1, 2])
    if peer_is.startswith("walking"):
        call.short(peer)

    ported, took = await _decide(machine, call, mover, 20.0)

    assert not ported and ports.calls == []
    assert 0.45 <= took < 3.0
    assert any(m == "P1: no port beats its 20.0 s walk; walking" for m, *_ in machine.logs)


# --- Player Realms ---


def _seen_together(machine, *clients) -> None:
    """Note one check in which *clients* each see the others: one zone, realm and instance."""
    gids = [9000 + k for k in range(len(clients))]
    readings = [sightings.Reading(g, ZONE, None, False, frozenset(gids) - {g}) for g in gids]
    machine.note_sightings(sightings.Sightings(list(clients), readings))


def _separate(clients) -> FakeMachine:
    return _run(clients, **{player_realms.OPTION: player_realms.SEPARATE})


@pytest.mark.parametrize("seen", [False, True])
async def test_separate_ports_only_to_a_player_seen_on_the_movers_realm(ports, seen):
    mover, anchor = _party((200.0, 1000.0, 0.0), GOAL)
    machine = _separate([mover, anchor])
    if seen:
        _seen_together(machine, mover, anchor)

    (walked,) = await walking.walk_clients(machine, [mover], GOAL)

    assert walked.ok
    if seen:
        assert ports.calls == [(mover, anchor)] and not _moved(mover)
    else:
        assert ports.calls == [] and _moved(mover), "a player not seen on its realm may be on another"


@pytest.mark.parametrize("seen", [False, True])
async def test_separate_waits_only_for_a_short_walker_seen_on_the_movers_realm(ports, seen):
    long_walker, short = _party((200.0, 1000.0, 0.0), (3200.0, 1000.0, 0.0))
    machine = _separate([long_walker, short])
    if seen:
        _seen_together(machine, long_walker, short)

    walked = await walking.walk_clients(machine, [long_walker, short], GOAL)

    assert all(w.ok for w in walked)
    waited = [m for m, *_ in machine.logs if m.startswith("P1: waiting for P2")]
    if seen:
        assert ports.calls == [(long_walker, short)]
    else:
        assert ports.calls == [] and waited == []
        assert _moved(long_walker)


async def test_separate_with_no_record_of_who_is_together_ports_nobody():
    mover, anchor = _party((200.0, 1000.0, 0.0), GOAL)

    class _NoRecord:
        def get_option(self, name, default=None):
            return player_realms.SEPARATE if name == player_realms.OPTION else default

    assert not walking._on_my_realm(_NoRecord(), mover, anchor)
    assert walking._on_my_realm(FakeMachine(), mover, anchor), "Together ports to anyone with a route"
