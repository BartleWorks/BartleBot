"""Walking every client of a run to a point, on core's route walker.

Every move a node makes inside a zone goes through :func:`walk_clients`: each client walks
from where it stands, all at once, each under a movement lease on the run's one arbiter
(``machine.arbiter``), on a planner of its own from the zone maps core ships. A walk never
teleports, and a walk that fails says where (``WalkResult.describe``).

:func:`hop` is the one position write in BartleBot: Teleport to XYZ's few steps past a closed
gate, never farther than :data:`HOP_MAX` from where the wizard stands
(``test_nothing_writes_a_position_but_the_hop``).

A walk too long to be worth walking, with one of ours a port away, is a port instead
(:func:`walk_clients`): the port itself is the game's, made through ``port_map``, and writes
nothing.

Core's ``docs/WALKING.md`` is how a route is planned and walked; its *A bot run* section is
what this module adds on top of it, and ``bartlebot/nodes/CLAUDE.md``, *Moving a client*, the
rule a node keeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from bartleby.runtime import run_trace
from bartleby.runtime.arbiter import Busy, LeaseLost, Owner
from bartleby.runtime.clients import game_keys, group_manager
from bartleby.runtime.movement import zone_maps
from bartleby.runtime.movement.patrols import Patrol, distance_to_segments, segments
from bartleby.runtime.movement.route import WIZARD_SPEED
from bartleby.runtime.movement.walk import (
    ARRIVE_GOAL,
    ClientRead,
    GoalRead,
    Point,
    RouteWalker,
    WalkEnd,
    WalkResult,
    own_speed,
    plan_in_thread,
)
from wizwalker import XYZ

from bartlebot.nodes.param_schema import BotOptionSpec
from bartlebot.options import player_realms
from bartlebot.options.option_groups import GROUP_GENERAL

logger = logging.getLogger(__name__)

#: Whether a walk may raise the wizard's speed. Off until the user's go-ahead (Stage 0 of
#: #322): every wizard walks at its own speed, and nothing here writes ``speed_multiplier``.
WALKS_WRITE_SPEED = False

#: How long a node whose walk ran into a fight waits for ambient combat to take it over. The
#: watcher reads every client's battle flag each poll interval, so it preempts the node well
#: inside this; one that never comes means no watcher is running, and the node fails.
FIGHT_PREEMPT_WAIT = 10.0

#: How near its goal a walk that arrived stands: within core's ARRIVE_GOAL, not on the spot.
ARRIVED_WITHIN = ARRIVE_GOAL

AVOID_MOBS = "Avoid Mobs"

AVOID_MOBS_OPTION = BotOptionSpec(
    name=AVOID_MOBS,
    group=GROUP_GENERAL,
    type="bool",
    default=True,
    description=(
        "Walks plan around the mobs' patrols, and wait for or step around a mob in the way, so a "
        "walk starts no fight. Turn it off to take the shortest way and fight whatever it meets."
    ),
)

#: The farthest a hop may move a wizard, in units, measured in three dimensions from where it
#: stands to where it lands. A sixth of the 2,000 units Deimos's wizwalker fork has sent in one
#: jump since 2026-11-12 as unlikely to be undone (``teleport_distance_snapback_unlikely``), and
#: enough for every gate crossing the shipped bots need, up to 283 (``check_bot_routes.py``,
#: 2026-11-26). Settled by the user on 2026-11-26 (Stage 7 of #322). A constant on purpose, as
#: the restricted zones are: no setting, option, param or override raises it, and a test pins it.
HOP_MAX = 350.0

#: How far from a Teleport to XYZ's start a wizard may stand when it hops: the walk to the start
#: stops anywhere within core's ARRIVE_GOAL of it. Measured in three dimensions.
HOP_START_TOLERANCE = ARRIVE_GOAL

#: The farthest a Teleport to XYZ's own start and end may lie apart, so that a wizard anywhere
#: within :data:`HOP_START_TOLERANCE` of the start is never more than :data:`HOP_MAX` from the end.
HOP_SPAN = HOP_MAX - HOP_START_TOLERANCE

#: Seconds a hop waits before reading where the wizard is. Right after a write the position reads
#: as the end whether or not the server keeps it (found walking in October 2026), and the server
#: puts a wizard it refuses back some 0.7 s later (a mount's rubber-banding, core's
#: ``docs/WIZARD101.md``); Deimos waits 0.3 to 0.5 s for its snapback hook. So a chain of hops moves
#: at most ``HOP_MAX / HOP_SETTLE`` units a second, under walking's 600.
HOP_SETTLE = 1.0

#: How near the end a hop that held stands once it has settled.
HOP_LANDED = ARRIVE_GOAL

#: Units a second a wizard walks, at its own speed: the median core's walker measured in Unicorn Way.
WALKING_PACE = 600.0

#: Seconds a port to one of ours within a zone takes, from its first click to landing, as the user
#: chose it from the run trace's port spans (2026-11-28): 11 group ports took 3.9 to 11.8 s, a
#: median of 6.6; two ports within a zone, 3.9 and 5.1 s; and the friends-list clicks alone, 3.4
#: to 3.9 s. A constant, not a setting: nobody can choose it better than a measurement.
PORT_COST = 5.0

#: Seconds a port, and the walk on from where it lands, must save over walking straight there.
PORT_MARGIN = 2.0

#: The longest walk never asked whether a port would be quicker, since no port can beat it by
#: :data:`PORT_MARGIN`: a walk planned at no more than this is walked.
PORT_LONGEST = PORT_COST + PORT_MARGIN

#: The longest a long walker waits on the other movers of its call, all told, before it walks
#: (:func:`_wait_bound`). As long as :data:`PORT_LONGEST`, since a mover is waited for only while
#: it plans or walks a plan no longer than that. Its own constant, so a test that scales the
#: port's costs down keeps this ceiling.
PORT_WAIT_LONGEST = PORT_LONGEST

#: Planners come from here, so a test can hand the walk a zone of its own.
load_planner = zone_maps.load
has_map = zone_maps.has


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _at(p) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f})"


async def hop(machine, client, zone: str, start, end, *, number: int = 1) -> bool:
    """Move *client* from *start* to *end* by one written position: the one position write in
    BartleBot, and the only way a node gets past a closed gate. Returns whether it landed.

    It writes nothing unless every check passes, each made right before the write under the
    client's movement lease, so nothing else moves the wizard between the read and the write:
    no other lease holds the client; it is in *zone*, not loading and not in battle; and where it
    stands, read now, is within :data:`HOP_START_TOLERANCE` of *start* and at most
    :data:`HOP_MAX` from *end*, so neither a param nor a stale read can stretch a hop. It writes
    once, waits :data:`HOP_SETTLE`, and only then reads back. A hop the server undid fails, and is
    never written again. Every refusal is logged at WARNING with the zone, both points and the
    distance, naming the client by its number only.
    """
    from bartlebot.nodes.node_helpers import zone_matches  # noqa: PLC0415 - node_helpers imports this module

    start, end = point(start), point(end)
    label = f"P{number}"

    def refuse(why: str) -> bool:
        machine.log(f"{label}: no hop from {_at(start)} to {_at(end)} in {zone!r}: {why}", logging.WARNING)
        return False

    try:
        async with machine.arbiter.acquire(client, Owner.MOVEMENT, wait=False, detail="hopping"):
            if await client.is_loading():
                return refuse("it is loading")
            here_zone = await client.zone_name()
            if not here_zone or not zone_matches(here_zone, zone):
                return refuse(f"it is in {here_zone!r}")
            if await client.in_battle():
                return refuse("it is in a fight")
            here = point(await client.body.position())
            if (far := _dist(here, end)) > HOP_MAX:
                return refuse(f"it stands at {_at(here)}, {far:.0f} from the end, past HOP_MAX ({HOP_MAX:.0f})")
            if (off := _dist(here, start)) > HOP_START_TOLERANCE:
                return refuse(f"it stands at {_at(here)}, {off:.0f} from the start, past {HOP_START_TOLERANCE:.0f}")
            with run_trace.span("hop", client=client, zone=zone, distance=round(far)):
                try:
                    await client.teleport(XYZ(*end))
                except Exception as e:  # noqa: BLE001 - a failed write fails the hop, never retried
                    return refuse(f"the write failed: {e}")
                await asyncio.sleep(HOP_SETTLE)
                after = point(await client.body.position())
            if _dist(after, end) > HOP_LANDED or _dist(after, here) < _dist(after, end):
                machine.log(
                    f"FAILURE: {label}'s hop to {_at(end)} in {zone!r} was undone: it stands at {_at(after)}, "
                    f"{_dist(after, end):.0f} from the end",
                    logging.WARNING,
                )
                return False
    except Busy as busy:
        return refuse(f"its movement is held ({busy})")
    except LeaseLost as lost:
        # Taken after the checks, by a fight or a stop: the hop may have been written, so it fails.
        machine.log(f"FAILURE: {label}'s hop to {_at(end)} in {zone!r} was interrupted: {lost}", logging.WARNING)
        return False
    machine.log(f"{label}: hopped {far:.0f} to {_at(end)}", logging.DEBUG)
    return True


def hop_span(state) -> float | None:
    """How far apart a Teleport to XYZ's start and end lie, or None while either is unset."""
    params = getattr(state, "params", None) or {}
    try:
        a, b = point(_xyz(params.get("start_xyz"))), point(_xyz(params.get("end_xyz")))
    except (TypeError, KeyError, ValueError):
        return None
    return _dist(a, b)


def _xyz(value):
    if isinstance(value, dict):
        return (value["x"], value["y"], value["z"])
    return value


#: The node a hop belongs to.
TELEPORT_TO_XYZ = "teleport_to_xyz"


def span_error(state) -> str | None:
    """The validation error for a Teleport to XYZ whose start and end lie farther apart than
    :data:`HOP_SPAN`, or None. The editor marks the node with it, and Run refuses the bot."""
    if getattr(state, "node_type", None) != TELEPORT_TO_XYZ:
        return None
    span = hop_span(state)
    if span is None or span <= HOP_SPAN:
        return None
    return (
        f"Start XYZ and End XYZ are {span:.0f} apart, past the {HOP_SPAN:.0f} a hop may span. "
        "Walk closer to the gate first."
    )


def span_errors(states) -> list[tuple[str, str]]:
    """``(node label, error)`` for every Teleport to XYZ in *states* that spans too far."""
    return [(s.label or s.id, e) for s in states if (e := span_error(s)) is not None]


@dataclass
class Walked:
    """One client's walk: core's result, whether the goal lay in one of the zone's exits, and
    whether it was a walk into a fight."""

    result: WalkResult
    into_exit: bool = False
    fight: bool = False

    @property
    def ok(self) -> bool:
        """Whether the walk did what was asked. A walk into an exit has done its part once it
        stands there, since the server moves the wizard a moment after, or once it has moved.
        A walk into a fight has done it once the fight has started."""
        if self.result.arrived:
            return True
        if self.fight and self.result.end is WalkEnd.FIGHT:
            return True
        return self.into_exit and self.result.end is WalkEnd.ZONE_CHANGED

    def describe(self) -> str:
        if self.result.start is None:
            return f"not walked: {self.result.detail}"
        return self.result.describe()


def _not_walked(end: WalkEnd, zone: str | None, start, goal, detail: str) -> WalkResult:
    return WalkResult(end, zone, start, tuple(goal), position=start, detail=detail)


def point(goal) -> tuple[float, float, float]:
    """*goal* as core's point: from wizwalker's XYZ, or any three numbers."""
    if hasattr(goal, "x"):
        return (float(goal.x), float(goal.y), float(goal.z))
    return (float(goal[0]), float(goal[1]), float(goal[2]))


def _in_front(result: WalkResult) -> Point | None:
    """Where an interaction walks instead, when its goal lies on a closed gate's own floor: the
    last open floor before that gate, on the way to it (the gate's ``Crossing.before``)."""
    why = result.no_route
    if result.end is not WalkEnd.NO_ROUTE or why is None or why.gate is None or why.crossing is None:
        return None
    if _flat(why.crossing.after, result.goal) > ARRIVED_WITHIN:
        return None  # the gate stands between, not under the goal: in front of it is not there
    before = why.crossing.before
    return (float(before[0]), float(before[1]), float(before[2]))


class _TimedRead(ClientRead):
    """The client's entity list, as the walker reads it, timing each read: what watching mobs
    and reading gates costs a walk, noted on its span (Stage 5 of #322)."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self.mob_reads = 0
        self.mob_seconds = 0.0
        self.mob_longest = 0.0
        self.list_seconds = 0.0

    async def refresh(self):
        began = time.perf_counter()
        try:
            return await super().refresh()
        finally:
            self.list_seconds += time.perf_counter() - began

    async def mobs(self):
        began = time.perf_counter()
        try:
            return await super().mobs()
        finally:
            took = time.perf_counter() - began
            self.mob_reads += 1
            self.mob_seconds += took
            self.mob_longest = max(self.mob_longest, took)


async def _walk_one(
    machine,
    client,
    number: int,
    goal,
    avoid: bool,
    approach: bool = False,
    fight: bool = False,
    spare: Sequence[int] = (),
    call: _Call | None = None,
    reach: float = ARRIVE_GOAL,
) -> Walked:
    """One client's walk. With *call*, the :func:`walk_clients` call ports are on for, a walk not
    into a fight or an exit, in a zone that has not refused a port this run, first asks whether
    a port would be quicker (:func:`_port_or_walk`), and the call is told how the walk ended.

    *goal* is a point, or a :data:`GoalRead` for one that moves (a wisp), which core's walker
    reads every tick: it is read once here, for the zone's exits and the port question, and a
    goal gone already is arrived with nothing walked. *reach* is how near the goal a walk has
    arrived (core's ``RouteWalker.walk``)."""
    result: WalkResult | None = None
    try:
        result, walked = await _walk_or_port(machine, client, number, goal, avoid, approach, fight, spare, call, reach)
        return walked
    finally:
        if call is not None:
            call.ended(client, result is not None and result.arrived)


async def _walk_or_port(  # noqa: PLR0913 - _walk_one's arguments, passed on
    machine,
    client,
    number: int,
    goal,
    avoid: bool,
    approach: bool,
    fight: bool,
    spare,
    call: _Call | None,
    reach: float = ARRIVE_GOAL,
) -> tuple[WalkResult, Walked]:
    moving: GoalRead | None = goal if callable(goal) else None
    if moving is not None:
        now = await moving()
        if now is None:
            pos = await client.body.position()
            here = (pos.x, pos.y, pos.z)
            result = WalkResult(WalkEnd.ARRIVED, None, here, here, position=here, detail="its goal was gone")
            return result, Walked(result)
        goal = now
    goal = point(goal)
    walk_to = moving or goal  # what the walker goes to: a moving goal read again every tick
    if await client.is_loading():
        # On its way somewhere already: a walk would start from where it was (#70).
        result = _not_walked(WalkEnd.ZONE_CHANGED, None, None, goal, "it was loading")
        return result, Walked(result)
    zone = await client.zone_name()
    if not zone or not has_map(zone):
        pos = await client.body.position()
        start = (pos.x, pos.y, pos.z)
        result = _not_walked(WalkEnd.NO_ROUTE, zone, start, goal, "the zone has no walking map")
        return result, Walked(result)
    planner = await load_planner(zone)
    into_exit = bool(planner.exits_at(*goal))
    read = _TimedRead(client)
    asks = call is not None and not fight and not into_exit and zone not in _no_port_zones(machine)
    # Its first tick is the first moment a walk is known to be on a short plan: the walker ticks
    # only once its plan is made and found no longer than PORT_LONGEST, never while planning.
    ticked = (lambda _tick: call.short(client)) if asks else None
    walker = RouteWalker(client, planner, avoid=avoid, watch_mobs=avoid, read=read, label=f"P{number}", on_tick=ticked)
    ported = False
    with run_trace.span("walk", client=client, zone=zone, goal=[round(v) for v in goal], fight=fight):
        try:
            async with machine.arbiter.acquire(client, Owner.MOVEMENT, detail="walking") as lease:
                if asks:
                    result = await walker.walk(lease, walk_to, longest=PORT_LONGEST, reach=reach)
                    if result.end is WalkEnd.TOO_LONG:
                        call.long(client)
                        ported = await _port_or_walk(machine, call, client, number, zone, planner, goal, avoid, result)
                        # From where it landed, or on the plan kept from where it stands: the same
                        # walker, with no longest, so the walk after a port never ports again.
                        result = await walker.walk(lease, walk_to, reach=reach)
                else:
                    result = await walker.walk(lease, walk_to, fight=fight, spare=spare, reach=reach)
                if approach and (front := _in_front(result)) is not None:
                    # The point is inside the object the client lists, so it is closed: stand
                    # at its edge instead, where its prompt shows (Stage 4 of #322).
                    machine.log(
                        f"P{number}: the point is inside {result.no_route.gate}; walking to its edge",
                        logging.DEBUG,
                    )
                    result = await walker.walk(lease, front)
        except LeaseLost as lost:
            # Another owner took the client (a stop, or a higher priority): the walk is over.
            pos = await client.body.position()
            here = (pos.x, pos.y, pos.z)
            result = _not_walked(WalkEnd.BLOCKED, zone, here, goal, f"its movement was taken: {lost}")
        run_trace.note(
            end=result.end.value,
            distance=round(sum(_flat(a, b) for a, b in zip(result.trail, result.trail[1:]))),
            seconds=round(result.seconds, 2),
            stalls=result.recoveries,
            detours=result.detours,
            replans=result.replans,
            reaims=result.reaims,
            mob_reads=read.mob_reads,
            mob_read_s=round(read.mob_seconds, 3),
            mob_read_max=round(read.mob_longest, 3),
            list_read_s=round(read.list_seconds, 3),
            ported=ported,
        )
    return result, Walked(result, into_exit, fight)


class _Call:
    """One :func:`walk_clients` call that ports are on for: the run's port map, the call's movers,
    and how each one's walk stands, so a long walker can port to a short one of the same call
    once it has arrived.

    A mover's first walk is planning, then either long (:meth:`long`) or short, walking a plan
    no longer than :data:`PORT_LONGEST` (:meth:`short`), until it ends. Every change bumps
    :attr:`generation`, which a waiter reads *before* it looks at how the movers stand, so a
    change made while it looked is never slept through (:meth:`changed`)."""

    def __init__(self, port_map, clients: Sequence, numbers: Sequence[int]) -> None:
        self.port_map = port_map
        self.clients = list(clients)
        self.numbers = {id(c): n for c, n in zip(clients, numbers)}
        self.generation = 0
        self._first = {id(c) for c in clients}  # on its first walk: planning it, or walking a short plan
        self._short: dict[int, float] = {}  # walking a short first plan, since this monotonic time
        self._arrived: set[int] = set()
        self._changed = asyncio.Event()

    def has(self, client) -> bool:
        return id(client) in self.numbers

    def planning(self, client) -> bool:
        """Whether *client*, a mover of this call, is still planning its first walk: whether it
        is short or long is not known yet, so it is no target."""
        return id(client) in self._first and id(client) not in self._short

    def short_since(self, client) -> float | None:
        """When *client* began walking its short first plan, or None unless it is walking one."""
        return self._short.get(id(client))

    def arrived(self, client) -> bool:
        return id(client) in self._arrived

    def short(self, client) -> None:
        """*client*'s first walk has begun on a plan no longer than :data:`PORT_LONGEST`: called
        on each of its walker's ticks, it marks only the first, and only on that first walk."""
        if id(client) in self._first and id(client) not in self._short:
            self._short[id(client)] = time.monotonic()
            self._pulse()

    def long(self, client) -> None:
        """*client*'s first plan was too long: it is no target until it arrives."""
        self._first.discard(id(client))
        self._short.pop(id(client), None)
        self._pulse()

    def ended(self, client, arrived: bool) -> None:
        self._first.discard(id(client))
        self._short.pop(id(client), None)
        if arrived:
            self._arrived.add(id(client))
        self._pulse()

    def _pulse(self) -> None:
        self.generation += 1
        self._changed.set()
        self._changed = asyncio.Event()

    async def changed(self, since: int, timeout: float) -> None:
        """Wait until a mover's walk changes how it stands, or *timeout* passes. *since* is the
        :attr:`generation` read before the caller looked: a change made after that returns at
        once, even one made before this call (found live, 2026-11-27: a waiter that missed its
        peer's change sat out its whole deadline, and then walked the whole way)."""
        if self.generation != since:
            return
        event = self._changed
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), max(timeout, 0.0))


@dataclass
class _Target:
    """One of ours a mover could port to, and the trip that makes: :data:`PORT_COST`, the walk on
    from where it stands, and for a mover of the call still walking, the wait for it to arrive."""

    client: object
    trip: float
    waits: bool = False


def _no_port_zones(machine) -> set[str]:
    zones = getattr(machine, "no_port_zones", None)
    return zones if isinstance(zones, set) else set()


def _reaches(port_map, mover, target) -> bool:
    """Whether *mover* has a port route to *target*: the friends list or the group, or Teleport to
    Leader where the group record has *target* leading *mover*'s group. So a leader is never sent
    by Teleport to Leader, its leader being itself. ``port_waves`` proves the record again."""
    if port_map.routes(mover, target):
        return True
    record = group_manager.group_record(target)
    return (
        record is not None
        and record.leader is target
        and any(m is mover for m in record.members)
        and bool(port_map.leader_candidates(target, [mover]))
    )


async def _free_in(machine, client, zone: str) -> bool:
    """Whether *client* is in *zone*, not loading or fighting, and holds no movement lease."""
    try:
        if machine.arbiter.holder(client) is not None or await client.is_loading():
            return False
        return await client.zone_name() == zone and not await client.in_battle()
    except Exception:  # noqa: BLE001 - one that cannot be read now is no target
        logger.debug("a port target could not be read", exc_info=True)
        return False


def _on_my_realm(machine, mover, target) -> bool:
    """Whether a port from *mover* to *target* keeps to Player Realms: always while it is
    Together, and while it is Separate only once the two have been seen sharing a zone, realm
    and instance, with neither loading since (``machine.are_together``). A port onto a player
    not known to share the mover's realm could carry it onto another's, which Separate never
    does, as straggler recovery's ``_recovery_batches`` keeps to as well."""
    if not player_realms.machine_is_separate(machine):
        return True
    together = getattr(machine, "are_together", None)
    return together is not None and bool(together([mover, target]))


def _candidates(machine, call: _Call, mover) -> list:
    """The players of ours *mover* may port to at all: the call's movers and the rest of the run's
    players, each with a route from *mover* and on its realm as Player Realms has it."""
    party = [*call.clients, *(c for c in getattr(machine, "clients", None) or () if not call.has(c))]
    return [
        c for c in party if c is not mover and _reaches(call.port_map, mover, c) and _on_my_realm(machine, mover, c)
    ]


async def _port_targets(  # noqa: PLR0913 - the walk's state, passed whole
    machine, call: _Call, mover, zone: str, planner, goal: Point, avoid: bool, planned: float, speed: float
) -> list[_Target]:
    """The players of ours *mover* could port to now, each with its trip.

    A player outside the call, or a mover of the call that has arrived, counts where it stands:
    in the zone, not fighting, holding no movement lease, and with a route from *mover*. Its walk
    on to *goal* is a ``seconds_to`` search from it, at *speed*, the mover's own, unless its
    straight line there already misses *planned* by :data:`PORT_MARGIN`, which is never searched.
    A mover of the call walking a short first plan is walking to *goal* too, so its trip is the
    wait for it, at most what is left of :data:`PORT_LONGEST` since it began walking, and the walk
    on from up to :data:`ARRIVED_WITHIN` short of the goal, where its walk stops. A mover still
    planning is no target: its plan may be as long as the mover's own.
    """
    candidates = _candidates(machine, call, mover)
    now = time.monotonic()

    async def one(target) -> _Target | None:
        since = call.short_since(target)
        if since is not None:
            # It stops up to ARRIVED_WITHIN short of the goal, so that walk on counts too: without
            # it, one still walking once PORT_LONGEST had passed undercut one already arrived.
            wait = max(0.0, PORT_LONGEST - (now - since))
            return _Target(target, wait + PORT_COST + ARRIVED_WITHIN / speed, waits=True)
        if call.has(target) and not call.arrived(target):
            return None  # planning, long, porting or failed: no anchor yet
        if not await _free_in(machine, target, zone):
            return None
        at = point(await target.body.position())
        if PORT_COST + _flat(at, goal) / speed + PORT_MARGIN > planned:
            return None
        (walk_on,) = await plan_in_thread(planner.seconds_to, at, [goal], avoid, speed)
        return None if walk_on is None else _Target(target, PORT_COST + walk_on)

    found = await asyncio.gather(*(one(c) for c in candidates))
    return [t for t in found if t is not None]


def _quickest(targets: Sequence[_Target], planned: float) -> _Target | None:
    """The target with the quickest trip, if it beats walking *planned* seconds by :data:`PORT_MARGIN`."""
    best = min(targets, key=lambda t: t.trip, default=None)
    return best if best is not None and best.trip + PORT_MARGIN <= planned else None


async def _port_or_walk(  # noqa: PLR0913 - the walk's state, passed whole
    machine, call: _Call, mover, number: int, zone: str, planner, goal: Point, avoid: bool, first: WalkResult
) -> bool:
    """Port *mover*, whose *first* walk ended :attr:`WalkEnd.TOO_LONG`, to the one of ours whose
    trip beats walking by :data:`PORT_MARGIN`, if one does; whether it ported.

    A mover of the call walking a short first plan is waited for to arrive, and one still
    planning is waited for to say whether it is short. Every target is costed again whenever a
    mover's walk changes, so the first to arrive is the one ported to. All the waiting together
    lasts at most :func:`_wait_bound`: past it, only a target standing ready counts, and with
    none the mover walks. A refusal marks the zone for the rest of the run
    (``machine.no_port_zones``). A port that lands nowhere walks too."""
    planned, speed = first.planned, first.speed or WIZARD_SPEED
    deadline: float | None = None
    told: set[int] = set()
    while True:
        seen = call.generation  # before looking, so a change made while it looks is not slept through
        targets = await _port_targets(machine, call, mover, zone, planner, goal, avoid, planned, speed)
        over = deadline is not None and time.monotonic() >= deadline
        if over:
            targets = [t for t in targets if not t.waits]
        best = _quickest(targets, planned)
        if best is not None and not best.waits:
            return await _port_to(
                machine, call, mover, number, best, _number_of(machine, call, best.client), zone, planned
            )
        planning = [] if over else [c for c in _candidates(machine, call, mover) if call.planning(c)]
        if best is None and not planning:
            machine.log(f"P{number}: no port beats its {planned:.1f} s walk; walking", logging.DEBUG)
            return False
        if deadline is None:
            deadline = time.monotonic() + _wait_bound(planned)
        for client in [best.client] if best is not None else planning:
            if id(client) not in told:
                told.add(id(client))
                what = "arrive" if best is not None else "plan its walk"
                to = _number_of(machine, call, client)
                machine.log(f"P{number}: waiting for P{to} to {what}, to port to it", logging.DEBUG)
        await call.changed(seen, deadline - time.monotonic())


def _wait_bound(planned: float) -> float:
    """The longest a mover planned at *planned* seconds waits on others before it walks.

    At most :data:`PORT_WAIT_LONGEST`: a mover is waited for only while it plans or walks a plan
    no longer than :data:`PORT_LONGEST`, so one that has not arrived by then has been held up on
    its way, and walking is the better bet. A wait that comes to nothing then costs at most those
    7 s, never most of a long walk, as waiting until a port no longer saved :data:`PORT_MARGIN`
    once did: 50 s before a 55 s walk (2026-11-27). Shorter still for a walk planned under 14 s: a
    wait past *planned* less :data:`PORT_COST` and :data:`PORT_MARGIN` leaves no port worth taking."""
    return min(PORT_WAIT_LONGEST, planned - PORT_MARGIN - PORT_COST)


async def _port_to(machine, call: _Call, mover, number: int, best: _Target, to: int, zone: str, planned: float) -> bool:
    """Port *mover* to *best*; whether it landed. A refusal marks *zone*, logged once a run."""
    machine.log(f"P{number}: porting to P{to}, about {best.trip:.1f} s against a {planned:.1f} s walk")
    with run_trace.span("port", client=mover, zone=zone):
        batches = await port(machine, mover, best.client, call.port_map)
        landed = any(any(c is mover for c in b.landed) for b in batches)
        refused = any(b.refused for b in batches)
        run_trace.note(
            route=next((b.route for b in batches if b.landed), batches[-1].route if batches else None),
            seconds=round(sum(b.seconds for b in batches), 2),
            landed=landed,
            refused=refused,
        )
    if landed:
        return True
    if refused:
        zones = getattr(machine, "no_port_zones", None)
        if isinstance(zones, set) and zone not in zones:
            zones.add(zone)
            machine.log(f"P{number}: {zone!r} refuses ports; walking there for the rest of the run")
        return False
    machine.log(f"P{number}: the port to P{to} went nowhere; walking")
    return False


def _number_of(machine, call: _Call, client) -> int:
    if call.has(client):
        return call.numbers[id(client)]
    return player_numbers(machine, [client])[0]


async def port(machine, mover, target, port_map) -> list:
    """Port *mover* to *target*, one of ours, through ``port_map.port_waves``, with *mover* the
    one waiting and *target* the one arrived, so landing is checked by zone and by sighting as
    every port's is; the batches it sent (``port_map.Batch``). What the suite replaces to port
    simulated clients."""
    from bartlebot.clients import port_map as ports  # noqa: PLC0415 - port_map imports node_helpers, which imports this

    party = list(getattr(machine, "clients", None) or ())
    named = party if all(any(c is k for k in party) for c in (mover, target)) else [mover, target]
    batches: list = []
    await ports.port_waves(machine, named, [mover], [target], port_map, batches=batches)
    return batches


#: One client's walk: what :func:`walk_clients` calls, and what the suite's walking double
#: replaces (``tests/conftest.py``, ``walk_double``).
walk_one = _walk_one


def _flat(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def player_numbers(machine, clients: Sequence) -> list[int]:
    """Each of *clients*' player number, 1-based, for the log: its place among the run's
    players (``machine.clients``), so a node run on some of them, as a trip's leader alone,
    names each as the rest of the log does. When the machine lists no players, or not all of
    these, each is numbered by its place in *clients*."""
    party = list(getattr(machine, "clients", None) or ())
    found = [next((i + 1 for i, c in enumerate(party) if c is client), None) for client in clients]
    return list(range(1, len(clients) + 1)) if None in found else found


async def walk_clients(
    machine,
    clients: Sequence,
    goal,
    *,
    numbers: Sequence[int] | None = None,
    approach: bool = False,
    fight: bool = False,
    spare: Sequence[int] = (),
    wait_for_preempt: bool = True,
    reach: float = ARRIVE_GOAL,
) -> list[Walked]:
    """Walk every one of *clients* to *goal* at once, each from where it stands.

    *goal* is a point, or a :data:`GoalRead` for a goal that moves, such as a wisp, which each
    walk reads every tick and ends within *reach* of, or once it reads None (core's
    ``RouteWalker.walk``). The port question reads it once.

    Returns a :class:`Walked` per client, in order. A failed walk is logged under the node
    with where it ended (the zone, both points, and the closed gate in the way when one is
    why). Nothing here teleports.

    A walk that ends because a fight started is not the node's failure: ambient combat
    preempts the node, fights it out and runs the node again. So this waits, up to
    :data:`FIGHT_PREEMPT_WAIT`, for that cancel to arrive, and only then returns.

    *approach* is for a walk to an object's interact point: one that sits inside the object
    itself, which the client lists and so the walk closes, is walked to the object's edge
    instead (:func:`_in_front`), where its prompt shows.

    *fight* makes it a walk into a fight at *goal* (core's ``RouteWalker.walk(fight=True)``): the
    mobs there, and those in *spare* by global id, are walked up to, not around, and the fight
    starting is what it is for, so it ends the walk as done and nothing waits for a preempt.
    Every other mob is avoided as on any walk.

    A walk that ends in a fight while ambient combat is already running a session (a wisp or
    the move after a fight, walked by the session itself) waits for nothing: nothing will
    preempt it.

    *numbers* labels the clients in the log, 1-based; by default each is its player number
    (:func:`player_numbers`).

    *wait_for_preempt* False is for a walk outside a run (the authoring tools' go-to), where
    nothing would preempt it: a fight met is logged, and nothing is waited for.

    With a port map on the machine (``machine.port_map``, which only a run has), a walk planned
    longer than :data:`PORT_LONGEST` may be a port instead: to the player of ours, outside the
    call or a mover of it that has arrived, whose :data:`PORT_COST` plus walk on beats walking by
    :data:`PORT_MARGIN`. The mover ports through ``port_map``, under its movement lease, and walks
    the rest. With Player Realms Separate, only a player seen on the mover's own realm is a
    target. A walk into a fight or an exit always walks, and so does every walk in a zone that
    has refused a port this run (``machine.no_port_zones``).
    """
    labels = list(numbers) if numbers is not None else player_numbers(machine, clients)
    avoid = bool(machine.get_option(AVOID_MOBS, AVOID_MOBS_OPTION.default))
    port_map = getattr(machine, "port_map", None)
    # Passed only when ports are on, so a walk double without it walks as before.
    extra = {"call": _Call(port_map, clients, labels)} if port_map is not None else {}
    if reach != ARRIVE_GOAL:
        extra["reach"] = reach  # likewise, so a double that knows no reach walks as before
    walked = list(
        await asyncio.gather(
            *[walk_one(machine, c, n, goal, avoid, approach, fight, spare, **extra) for c, n in zip(clients, labels)]
        )
    )
    for number, w in zip(labels, walked):
        if not w.ok and w.result.end is not WalkEnd.FIGHT:
            machine.log(f"P{number}: {w.describe()}", logging.WARNING)
    unplanned = [n for n, w in zip(labels, walked) if w.result.end is WalkEnd.FIGHT and not w.fight]
    if unplanned and not wait_for_preempt:
        machine.log(f"{_players(unplanned)}: a fight started on the walk", logging.WARNING)
    elif unplanned and _in_ambient_session(machine):
        machine.log(f"{_players(unplanned)}: a fight started on the walk, during ambient combat", logging.WARNING)
    elif unplanned:
        machine.log(
            f"{_players(unplanned)}: a fight started on the walk; waiting for ambient combat to take over",
            logging.INFO,
        )
        await asyncio.sleep(FIGHT_PREEMPT_WAIT)
        machine.log(
            f"FAILURE: a fight started on the walk of {_players(unplanned)}, and nothing took it over "
            f"in {FIGHT_PREEMPT_WAIT:.0f}s",
            logging.WARNING,
        )
    return walked


def _in_ambient_session(machine) -> bool:
    """Whether ambient combat is running a session, so a fight met now has no preempt to wait for."""
    return getattr(machine, "is_ambient_combat_active", False) is True


async def passes_clear_of_patrols(machine, client, goal) -> bool:
    """Whether a walk from where *client* stands to *goal*, planned as the walk would be (Avoid
    Mobs), keeps outside the trigger radius of every patrol a mob is walking now: what Collect
    Wisps' Only Safe asks of a wisp before walking to it. A walk that cannot be planned here (no
    map, no route) counts as clear: the walk itself meets that, and says where.

    The map's patrols come from the zone's spawn files, which list a mob whether or not it is
    alive, so only the patrols :func:`live_patrols` finds a mob on count. Without that, a boss
    room's wisps were all refused after the fight: the party stood in the dead boss's radius, so
    every route started inside it (Captain Nemo's Ruined Hermitage, 2026-11-27)."""
    zone = await client.zone_name()
    if not zone or not has_map(zone):
        return True
    mobs = [at for _key, _name, at in await ClientRead(client).mobs()]
    if not mobs:
        return True
    planner = await load_planner(zone)
    live = live_patrols(planner.patrols, mobs)
    if not live:
        return True
    pos = await client.body.position()
    avoid = bool(machine.get_option(AVOID_MOBS, AVOID_MOBS_OPTION.default))
    route = await plan_in_thread(planner.route, (pos.x, pos.y, pos.z), point(goal), avoid)
    if route is None:
        return True
    cells = np.asarray([planner.grid.world(*node)[:2] for node in route.nodes], dtype=float)
    x, y = cells[:, 0], cells[:, 1]
    return all(float((distance_to_segments(x, y, segments(p)) - p.proximity).min()) >= 0 for p in live)


async def seconds_to(machine, clients: Sequence, goals: Sequence) -> list[list[float | None]] | None:
    """Seconds each of *clients* would take to walk from where it stands to each of *goals*, at
    its own speed, along the route its walk would plan there (Avoid Mobs as the walk has it), or
    None for a goal it has no route to: one list per client, a value per goal.

    None instead when the zone has no walking map, or the first client is loading: the caller
    then measures by straight line, as everything did before routes were costed. Every client is
    taken to be in the first one's zone, as a node's are. Each client is one search out from
    where it stands that settles every goal at once (core's ``ZonePlanner.seconds_to``), all on
    one planner and side by side in threads, so ten wisps cost what one does."""
    lead = clients[0]
    if await lead.is_loading():
        return None
    zone = await lead.zone_name()
    if not zone or not has_map(zone):
        return None
    planner = await load_planner(zone)
    avoid = bool(machine.get_option(AVOID_MOBS, AVOID_MOBS_OPTION.default))
    aims = [point(g) for g in goals]

    async def one(client) -> list[float | None]:
        pos = await client.body.position()
        speed = await own_speed(client)
        return await plan_in_thread(planner.seconds_to, (pos.x, pos.y, pos.z), aims, avoid, speed)

    return list(await asyncio.gather(*[one(c) for c in clients]))


#: How near its path a mob must stand for its patrol to count as walked now. A mob stands on its
#: path (core's ``FIGHT_REACH`` rests on the same), so this only absorbs a read taken mid-step.
LIVE_PATROL_REACH = 150.0


def live_patrols(patrols: Sequence[Patrol], mobs: Sequence[Point]) -> list[Patrol]:
    """The *patrols* with one of *mobs*, the positions of the mobs loaded now, within
    :data:`LIVE_PATROL_REACH` of their path. A patrol whose mob was beaten, or is farther than a
    client loads mobs (some 3,800 units), has none: it is no danger to a walk starting now."""
    if not mobs:
        return []
    at = np.asarray([m[:2] for m in mobs], dtype=float)
    return [
        p for p in patrols if float(distance_to_segments(at[:, 0], at[:, 1], segments(p)).min()) <= LIVE_PATROL_REACH
    ]


async def warm_lookups(client) -> None:
    """Make a client's first fight check and key-binding read now, when it hooks, not on its
    first walk. The first ``in_battle`` scans for the duel manager (about 0.5 s) and the first
    key held reads the bindings; each is kept for the client's life. Core's walker makes both
    while its first route is planned, which hides them only behind a plan longer than they
    are. A failure is left to the walk's own call to meet."""
    try:
        await client.in_battle()
    except Exception:  # noqa: BLE001 - the walk's own fight check meets it
        logger.debug("the first fight check failed", exc_info=True)
    try:
        await game_keys.key_map(client)
    except Exception:  # noqa: BLE001 - the first key held meets it
        logger.debug("the first read of the key bindings failed", exc_info=True)


async def walk_all(
    machine, clients: Sequence, goal, *, numbers: Sequence[int] | None = None, approach: bool = False
) -> bool:
    """:func:`walk_clients`, and whether every walk did what was asked."""
    return all(w.ok for w in await walk_clients(machine, clients, goal, numbers=numbers, approach=approach))


def _players(numbers: Sequence[int]) -> str:
    return ", ".join(f"P{n}" for n in numbers)


__all__ = [
    "HOP_LANDED",
    "HOP_MAX",
    "HOP_SETTLE",
    "HOP_SPAN",
    "HOP_START_TOLERANCE",
    "PORT_COST",
    "PORT_LONGEST",
    "PORT_MARGIN",
    "PORT_WAIT_LONGEST",
    "TELEPORT_TO_XYZ",
    "WALKING_PACE",
    "hop",
    "hop_span",
    "span_error",
    "span_errors",
    "ARRIVED_WITHIN",
    "AVOID_MOBS",
    "AVOID_MOBS_OPTION",
    "FIGHT_PREEMPT_WAIT",
    "LIVE_PATROL_REACH",
    "WALKS_WRITE_SPEED",
    "Walked",
    "live_patrols",
    "own_speed",
    "passes_clear_of_patrols",
    "port",
    "seconds_to",
    "walk_all",
    "walk_clients",
    "warm_lookups",
]
