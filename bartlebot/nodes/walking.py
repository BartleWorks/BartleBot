"""Walking every client of a run to a point, on core's route walker.

Every move a node makes inside a zone goes through :func:`walk_clients`: each client walks
from where it stands, all at once, each under a movement lease on the run's one arbiter
(``machine.arbiter``), on a planner of its own from the zone maps core ships. A walk never
teleports, and a walk that fails says where (``WalkResult.describe``).

:func:`hop` is the one position write in BartleBot: Teleport to XYZ's few steps past a closed
gate, never farther than :data:`HOP_MAX` from where the wizard stands
(``test_nothing_writes_a_position_but_the_hop``).

Core's ``docs/WALKING.md`` is how a route is planned and walked; its *A bot run* section is
what this module adds on top of it, and ``bartlebot/nodes/CLAUDE.md``, *Moving a client*, the
rule a node keeps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from bartleby.runtime import run_trace
from bartleby.runtime.arbiter import Busy, LeaseLost, Owner
from bartleby.runtime.clients import game_keys
from bartleby.runtime.movement import zone_maps
from bartleby.runtime.movement.patrols import Patrol, distance_to_segments, segments
from bartleby.runtime.movement.walk import (
    ARRIVE_GOAL,
    ClientRead,
    Point,
    RouteWalker,
    WalkEnd,
    WalkResult,
    plan_in_thread,
)
from wizwalker import XYZ

from bartlebot.nodes.param_schema import BotOptionSpec
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
) -> Walked:
    goal = point(goal)
    if await client.is_loading():
        # On its way somewhere already: a walk would start from where it was (#70).
        return Walked(_not_walked(WalkEnd.ZONE_CHANGED, None, None, goal, "it was loading"))
    zone = await client.zone_name()
    if not zone or not has_map(zone):
        pos = await client.body.position()
        start = (pos.x, pos.y, pos.z)
        return Walked(_not_walked(WalkEnd.NO_ROUTE, zone, start, goal, "the zone has no walking map"))
    planner = await load_planner(zone)
    into_exit = bool(planner.exits_at(*goal))
    read = _TimedRead(client)
    walker = RouteWalker(client, planner, avoid=avoid, watch_mobs=avoid, read=read, label=f"P{number}")
    with run_trace.span("walk", client=client, zone=zone, goal=[round(v) for v in goal], fight=fight):
        try:
            async with machine.arbiter.acquire(client, Owner.MOVEMENT, detail="walking") as lease:
                result = await walker.walk(lease, goal, fight=fight, spare=spare)
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
            mob_reads=read.mob_reads,
            mob_read_s=round(read.mob_seconds, 3),
            mob_read_max=round(read.mob_longest, 3),
            list_read_s=round(read.list_seconds, 3),
        )
    return Walked(result, into_exit, fight)


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
) -> list[Walked]:
    """Walk every one of *clients* to *goal* at once, each from where it stands.

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
    """
    labels = list(numbers) if numbers is not None else player_numbers(machine, clients)
    avoid = bool(machine.get_option(AVOID_MOBS, AVOID_MOBS_OPTION.default))
    walked = list(
        await asyncio.gather(
            *[walk_one(machine, c, n, goal, avoid, approach, fight, spare) for c, n in zip(clients, labels)]
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
    "passes_clear_of_patrols",
    "walk_all",
    "walk_clients",
    "warm_lookups",
]
