"""Walking every client of a run to a point, on core's route walker.

Every move a node makes inside a zone goes through :func:`walk_clients`: each client walks
from where it stands, all at once, each under a movement lease on the run's one arbiter
(``machine.arbiter``), on a planner of its own from the zone maps core ships. A walk never
teleports, and a walk that fails says where (``WalkResult.describe``).

Core's ``docs/WALKING.md`` is how a route is planned and walked; its *A bot run* section is
what this module adds on top of it, and ``bartlebot/nodes/CLAUDE.md``, *Moving a client*, the
rule a node keeps.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from bartleby.runtime import run_trace
from bartleby.runtime.arbiter import LeaseLost, Owner
from bartleby.runtime.clients import game_keys
from bartleby.runtime.movement import zone_maps
from bartleby.runtime.movement.walk import ARRIVE_GOAL, Point, RouteWalker, WalkEnd, WalkResult

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

#: Planners come from here, so a test can hand the walk a zone of its own.
load_planner = zone_maps.load
has_map = zone_maps.has


@dataclass
class Walked:
    """One client's walk: core's result, and whether the goal lay in one of the zone's exits."""

    result: WalkResult
    into_exit: bool = False

    @property
    def ok(self) -> bool:
        """Whether the walk did what was asked. A walk into an exit has done its part once it
        stands there, since the server moves the wizard a moment after, or once it has moved."""
        if self.result.arrived:
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


async def _walk_one(machine, client, number: int, goal, avoid: bool, approach: bool = False) -> Walked:
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
    walker = RouteWalker(client, planner, avoid=avoid, watch_mobs=avoid, label=f"P{number}")
    with run_trace.span("walk", client=client, zone=zone, goal=[round(v) for v in goal]):
        try:
            async with machine.arbiter.acquire(client, Owner.MOVEMENT, detail="walking") as lease:
                result = await walker.walk(lease, goal)
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
        )
    return Walked(result, into_exit)


#: One client's walk: what :func:`walk_clients` calls, and what the suite's walking double
#: replaces (``tests/conftest.py``, ``walk_double``).
walk_one = _walk_one


def _flat(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


async def walk_clients(
    machine, clients: Sequence, goal, *, numbers: Sequence[int] | None = None, approach: bool = False
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

    *numbers* labels the clients in the log, 1-based, for a caller walking a subset.
    """
    labels = list(numbers) if numbers is not None else list(range(1, len(clients) + 1))
    avoid = bool(machine.get_option(AVOID_MOBS, AVOID_MOBS_OPTION.default))
    walked = list(
        await asyncio.gather(*[walk_one(machine, c, n, goal, avoid, approach) for c, n in zip(clients, labels)])
    )
    for number, w in zip(labels, walked):
        if not w.ok and w.result.end is not WalkEnd.FIGHT:
            machine.log(f"P{number}: {w.describe()}", logging.WARNING)
    if any(w.result.end is WalkEnd.FIGHT for w in walked):
        fought = [n for n, w in zip(labels, walked) if w.result.end is WalkEnd.FIGHT]
        machine.log(f"P{fought}: a fight started on the walk; waiting for ambient combat to take over", logging.INFO)
        await asyncio.sleep(FIGHT_PREEMPT_WAIT)
        machine.log(
            f"FAILURE: a fight started on the walk of {_players(fought)}, and nothing took it over "
            f"in {FIGHT_PREEMPT_WAIT:.0f}s",
            logging.WARNING,
        )
    return walked


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
    "ARRIVED_WITHIN",
    "AVOID_MOBS",
    "AVOID_MOBS_OPTION",
    "FIGHT_PREEMPT_WAIT",
    "WALKS_WRITE_SPEED",
    "Walked",
    "walk_all",
    "walk_clients",
    "warm_lookups",
]
