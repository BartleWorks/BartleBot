"""Take clients to any zone on the navigation map, one planned step at a time.

The Go To Zone node is a thin wrapper around ``navigate()``, and ``tools/zone_nav_tester`` runs it
directly, so the tester exercises the code the node does.

Getting to another world is part of the plan, not a separate trip first: a World Gate is a step
like any other, and so is pressing HOME, so a route through the gate of the world the clients
are already in is weighed against going home to the World Tree, and the one with fewer
transitions wins.

Each step runs through the zone-entry node its kind names, so everything those nodes already
handle -- prompts, sequential teleporters, the dungeon exit warning -- comes along unchanged.
Their teleport-to-player recovery does not: a port partway through a route puts a straggler in a
zone the others are about to leave, so it is turned off here and stragglers are dealt with once,
at the end.

The route is planned again after every step rather than run whole: a step can land somewhere
unplanned, and the hub button's cooldown depends on what has happened since. A plan costs
nothing next to a loading screen.

A group does not always travel together. A port is one loading screen for any number of
clients, while a group making a transition is still loaded in one player at a time, so beyond
the shortest routes -- or wherever a teleporter would have to be taken in turns -- one client
travels and the rest port to it on arrival. That also keeps every object to one user. See
docs/ZONE_NAVIGATION.md, "Running a route with several clients".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Protocol

from bartlebot import port_map, worlds, zone_lists, zone_nav
from bartlebot.nodes import node_helpers, wizard_city
from bartlebot.nodes.interaction import interact
from bartlebot.nodes.teleportation import teleport_to_world_hub
from bartlebot.nodes.timing import LOADING_SCREEN_TIMEOUT, POLL_INTERVAL
from bartlebot.nodes.utility import sleep
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list, enter_zone_via_object, enter_zone_via_ride
from bartlebot.restricted_zones import check_zone

MAX_STEPS = 30
"""No route needs this many. Reaching it means steps keep landing somewhere the plan did not expect."""

TRAVEL_TOGETHER_MAX_TRANSITIONS = 2
"""The longest route a group that starts together travels together, when it has no object step.

Anything longer, or with a teleporter in it, goes quicker with one client travelling and the rest
porting to it once: the game loads a group in one player at a time even on a shared transition.
"""


@dataclass
class Outcome:
    success: bool
    reason: str = ""
    taken: list[zone_nav.Step] = field(default_factory=list)

    @property
    def unverified(self) -> list[zone_nav.Step]:
        """Tentative links this run used -- each one worked, if the run succeeded."""
        return [step for step in self.taken if not step.verified]


class Runs(Protocol):
    """A node module, or anything else with a node's run()."""

    async def run(self, machine, clients: list, params: dict) -> str: ...


class GoHome:
    """HOME as a step, with a node's run(). wizard_city's press-and-verify unequips castles first, so HOME reaches the dorm."""

    @staticmethod
    async def run(machine, clients: list, params: dict) -> str:
        return "SUCCESS" if await wizard_city.teleport_home_and_verify_dorm(machine, clients) else "FAILURE"


def step_call(step: zone_nav.Step) -> list[tuple[Runs, dict]] | str:
    """The nodes and params that run `step`, in order, or why it cannot run.

    Every step is one zone-entry node, except a walk through a gate that has to be opened first.
    """
    p = step.params
    zones = {"from_zone": step.from_zone, "to_zone": step.to_zone}
    if step.kind == zone_nav.WALK:
        walk = (enter_zone, {**zones, "xyz": p["xyz"], **({"reset_xyz": p["reset_xyz"]} if "reset_xyz" in p else {})})
        if not (unlock := p.get("unlock")):
            return [walk]
        # The gate is shared, so one player opens it for the group; then it takes a while to appear.
        opening = [
            (
                interact,
                {
                    "zone": step.from_zone,
                    "interact_xyz": spot["interact_xyz"],
                    "reset_xyz": spot["reset_xyz"],
                    "interact_clients": [1],
                    "has_dialogue": False,
                },
            )
            for spot in unlock["objects"]
        ]
        return [*opening, (sleep, {"duration": unlock["opens_after"]}), walk]
    call = _zone_entry_call(step, zones)
    return call if isinstance(call, str) else [call]


def _zone_entry_call(step: zone_nav.Step, zones: dict) -> tuple[Runs, dict] | str:
    p = step.params
    if step.kind == zone_nav.OBJECT:
        params = {**zones, "interact_xyz": p["interact_xyz"], "reset_xyz": p["reset_xyz"]}
        params["sequential"] = p.get("sequential", False)
        if "object_use_delay" in p:
            params["object_use_delay"] = p["object_use_delay"]
        return enter_zone_via_object, params
    if step.kind == zone_nav.LIST:
        # Resolved here rather than left to the node: the node resolves the destination its
        # author picked, and a planned step has no author. Both go through zone_lists.
        resolved = zone_lists.resolve(step)
        if isinstance(resolved, str):
            return resolved
        return enter_zone_via_list, {
            "zone": step.from_zone,
            "to_zone": step.to_zone,
            "interact_xyz": resolved.interact_xyz,
            "reset_xyz": resolved.reset_xyz,
            "zone_list_entry": resolved.entry,
            "zone_list_aliases": resolved.aliases,
        }
    if step.kind == zone_nav.RIDE:
        # A ride finished from inside its cutscene zone still names the zone it is boarded in.
        params = {"from_zone": p.get("board_zone", step.from_zone), "to_zone": step.to_zone, "board": p["board"]}
        params["board_xyz"] = p["interact_xyz"] if p["board"] == zone_nav.OBJECT else p["xyz"]
        params.update({name: p[name] for name in ("reset_xyz", "ride_zone") if name in p})
        return enter_zone_via_ride, params
    if step.kind == zone_nav.HUB:
        return teleport_to_world_hub, {"zone": step.to_zone}
    if step.kind == zone_nav.GATE:
        # The gate lists worlds, in the client's language, as go_to_world searches it.
        names = worlds.display_names(p["world"])
        if not names:
            return (
                f"no display name is known for world {p['world']!r}, so its World Gate row cannot be found; "
                f"regenerate bartlebot/data/world_names.json with tools/dump_world_names.py"
            )
        primary, *aliases = names
        return enter_zone_via_list, {
            "zone": step.from_zone,
            # Anywhere in the world counts as arriving, as for go_to_world; the next plan starts from there.
            "to_zone": worlds.zone_pattern(p["world"]),
            "interact_xyz": p["interact_xyz"],
            "reset_xyz": p["reset_xyz"],
            "zone_list_entry": primary,
            "zone_list_aliases": aliases,
        }
    if step.kind == zone_nav.HOME:
        return GoHome, {}
    return f"{step.kind} steps cannot run: no zone-entry node runs them"


def lands(step: zone_nav.Step, zone: str) -> bool:
    """Whether a client now in `zone` made `step`. A gate step arrives anywhere in its world."""
    if step.kind == zone_nav.GATE:
        return node_helpers.zone_matches(zone, worlds.zone_pattern(step.params["world"]))
    return zone in step.arrives_in


def route_cost(route: list[zone_nav.Step], clients: int = 1) -> zone_nav.Cost:
    """What a planned route costs a group of `clients`, the hub button's cooldown aside."""
    return sum((zone_nav.step_cost(step, clients=clients) for step in route), zone_nav.Cost())


def travels_together(route: list[zone_nav.Step], clients: int) -> bool:
    """Whether a group that starts together should take `route` together rather than follow one client."""
    return route_cost(route, clients).transitions <= TRAVEL_TOGETHER_MAX_TRANSITIONS and not any(
        step.kind == zone_nav.OBJECT for step in route
    )


async def _teleport_cooldown(clients: list) -> float:
    """The longest wait left on any client's hub/HOME cooldown. A timer that cannot be read counts as a full one."""
    left = await asyncio.gather(*[node_helpers.teleport_cooldown(c) for c in clients])
    return max((zone_nav.TELEPORT_COOLDOWN if seconds is None else seconds for seconds in left), default=0.0)


class _ZoneUnreadable(Exception):
    """A client's zone stayed unreadable past LOADING_SCREEN_TIMEOUT."""


async def _zones(clients: list) -> list[str]:
    """Every client's zone, once every one of them can be read.

    A client between zones has none -- zone_name() is None until the next one is set up -- and
    a route planned from None means nothing, so that is waited out rather than planned from.
    """
    zones: list[str] = []

    async def readable() -> bool:
        zones[:] = await asyncio.gather(*[c.zone_name() for c in clients])
        return all(zones)

    if not await node_helpers.poll_until(readable, LOADING_SCREEN_TIMEOUT, POLL_INTERVAL):
        raise _ZoneUnreadable(f"a client's zone could not be read for {LOADING_SCREEN_TIMEOUT:.0f}s: {zones}")
    return zones


async def navigate(
    machine,
    clients: list,
    target: str,
    *,
    include_unverified: bool = False,
    avoid: Collection[str] = (),
    nav: zone_nav.NavMap | None = None,
) -> Outcome:
    """Move every client to `target`. See the module docstring for the approach.

    `avoid` is passed to the planner: the kinds of link the route must not use. It does not
    cover regrouping, which has no alternative to the hub button and HOME.
    """
    nav = zone_nav.load() if nav is None else nav
    world = nav.world_of(target)
    if world is None or target not in world.zones:
        machine.log(f"FAILURE: {target!r} is not on the navigation map", logging.WARNING)
        return Outcome(False, f"{target!r} is not on the navigation map")
    trip = _Trip(machine, clients, target, nav, include_unverified, avoid)
    try:
        return await trip.run()
    except _ZoneUnreadable as error:
        return trip.failed(str(error))


class _Trip:
    """One navigate() call: where it is going, and what it has learned on the way."""

    def __init__(self, machine, clients: list, target: str, nav: zone_nav.NavMap, include_unverified: bool, avoid):
        self.machine = machine
        self.clients = list(clients)
        self.target = target
        self.nav = nav
        self.include_unverified = include_unverified
        self.avoid = avoid
        # A link a quest line swaps the destination for (Samsara Village) arrives just as well.
        self.arrivals = {target, *(zone for s in nav.links if s.to_zone == target for zone in s.also_arrives_in)}
        self.taken: list[zone_nav.Step] = []
        self._ports: port_map.PortMap | None = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def arrived(self, zone: str) -> bool:
        return zone in self.arrivals

    def who(self, group: list) -> list[int]:
        """Player numbers, for the log. Never names: see "No real player data"."""
        return [next(i + 1 for i, c in enumerate(self.clients) if c is client) for client in group]

    def failed(self, reason: str) -> Outcome:
        self.machine.log(f"FAILURE: {reason}", logging.WARNING)
        return Outcome(False, reason, self.taken)

    def succeeded(self) -> Outcome:
        self.machine.log(f"SUCCESS: all clients in {self.target!r}")
        return Outcome(True, taken=self.taken)

    def plan(self, start: str, clients: int, cooldown: float = 0.0, avoid_links=()) -> list[zone_nav.Step] | None:
        return zone_nav.plan(
            self.nav,
            start,
            self.target,
            teleport_cooldown=cooldown,
            clients=clients,
            include_unverified=self.include_unverified,
            avoid=self.avoid,
            avoid_links=avoid_links,
        )

    async def ports(self) -> port_map.PortMap:
        """Who can port to whom: the pre-run survey's, or one taken now if the run had none."""
        if self._ports is None:
            known = getattr(self.machine, "port_map", None)
            if known is None:
                known = await port_map.survey(self.clients)
                if hasattr(self.machine, "port_map"):
                    self.machine.port_map = known
            self._ports = known
        return self._ports

    # ── choosing how to go ───────────────────────────────────────────────────

    async def run(self) -> Outcome:
        zones = await _zones(self.clients)
        if all(self.arrived(zone) for zone in zones):
            return self.succeeded()
        if hit := next((h for z in zones if (h := check_zone(z))), None):
            return self.failed(f"a client is in a restricted zone: {hit.describe()}")
        if len(self.clients) == 1:
            outcome, _ = await self.travel(self.clients, defer=False)
            return self.succeeded() if outcome.success else outcome

        split = len(set(zones)) > 1
        if not split:
            route = self.plan(zones[0], len(self.clients), await _teleport_cooldown(self.clients))
            if route is None:
                return self.failed(f"no route from {zones[0]!r} to {self.target!r}")
            if travels_together(route, len(self.clients)):
                return await self.together()

        ports = await self.ports()
        navigator = self.choose_navigator(zones, ports)
        if navigator is None:
            return self.failed(f"no route to {self.target!r} from where any client is: {sorted(set(zones))}")
        reach = ports.reach(navigator, self.clients)
        # Following one client only pays when the others can port to it. Together, nobody may be
        # left out; split, anyone left out still has to travel, but nobody reachable is the old
        # regroup-and-go.
        if not reach or (not split and len(reach) < len(self.clients) - 1):
            self.machine.log("The clients cannot all port to one another; they travel together")
            return await self.together()
        return await self.solo(navigator)

    def choose_navigator(self, zones: list[str], ports: port_map.PortMap):
        """The client the most others can port to, then the one with the cheapest route, then player order."""
        best = None
        for index, (client, zone) in enumerate(zip(self.clients, zones)):
            route = [] if self.arrived(zone) else self.plan(zone, 1)
            if route is None:
                continue
            key = (-len(ports.reach(client, self.clients)), route_cost(route), index)
            if best is None or key < best[0]:
                best = (key, client)
        return None if best is None else best[1]

    async def solo(self, navigator) -> Outcome:
        self.machine.log(f"Player {self.who([navigator])[0]} travels; the others port to it once it arrives")
        outcome, _ = await self.travel([navigator], defer=False)
        if not outcome.success:
            return outcome
        return await self.gather()

    async def together(self) -> Outcome:
        outcome, deferred = await self.travel(self.clients, defer=True)
        if not outcome.success:
            return outcome
        return await self.gather() if deferred else self.succeeded()

    async def gather(self) -> Outcome:
        """Port everyone not yet at the target to someone who is; whoever cannot, travels."""
        zones = await _zones(self.clients)
        arrived = [c for c, zone in zip(self.clients, zones) if self.arrived(zone)]
        waiting = [c for c, zone in zip(self.clients, zones) if not self.arrived(zone)]
        if waiting:
            left = await port_map.port_waves(self.machine, self.clients, waiting, arrived, await self.ports())
            if left:
                self.machine.log(f"Player(s) {self.who(left)} could not port; they travel there themselves")
                outcome, _ = await self.travel(left, defer=False)
                if not outcome.success:
                    return outcome
        return self.succeeded()

    # ── travelling ───────────────────────────────────────────────────────────

    async def travel(self, group: list, *, defer: bool) -> tuple[Outcome, list]:
        """Step `group` along the route until all of it is at the target.

        With `defer`, a client that misses a step twice stays exactly where it is -- nothing
        teleports it or runs a step on it -- and the rest carry on; those clients are returned
        for gather() to port. Without it, a split group regroups, as it would at the start.
        """
        group = list(group)
        deferred: list = []
        avoided: list[zone_nav.Step] = []
        for _ in range(MAX_STEPS):
            zones = await _zones(group)
            if all(self.arrived(zone) for zone in zones):
                return Outcome(True, taken=self.taken), deferred
            if hit := next((h for z in zones if (h := check_zone(z))), None):
                return self.failed(f"a client is in a restricted zone: {hit.describe()}"), deferred

            if len(set(zones)) > 1:
                if reason := await self.regroup(group, zones):
                    return self.failed(reason), deferred
                continue

            route = self.plan(zones[0], len(group), await _teleport_cooldown(group), avoided)
            if route is None:
                if avoided:
                    failed = avoided[-1]
                    return self.failed(
                        f"{failed.kind} step {failed.from_zone!r} -> {failed.to_zone!r} failed, "
                        f"and there is no other route to {self.target!r}"
                    ), deferred
                return self.failed(f"no route from {zones[0]!r} to {self.target!r}"), deferred

            step = route[0]
            calls = step_call(step)
            if isinstance(calls, str):
                return self.failed(calls), deferred
            self.machine.log(f"Step {len(self.taken) + 1}: {step.kind} {step.from_zone!r} -> {step.to_zone!r}")
            start = zones[0]
            await self.run_calls(calls, group)
            leaders, behind = await self.sort(step, group)

            if not leaders:
                if avoided:
                    return self.failed(f"{step.kind} step {step.from_zone!r} -> {step.to_zone!r} failed"), deferred
                self.machine.log(
                    f"Nobody made the {step.kind} step {step.from_zone!r} -> {step.to_zone!r}; planning around it",
                    logging.WARNING,
                )
                avoided.append(step)
                continue

            if behind and defer:
                if step.kind != zone_nav.OBJECT:
                    # The object node already gave its stragglers turns; that was their retry.
                    behind = await self.retry(step, calls, behind, start, leaders)
                if behind:
                    self.machine.log(
                        f"Player(s) {self.who(behind)} stay where they are, and port to the others at {self.target!r}"
                    )
                    deferred += behind
                    group = leaders
            self.taken.append(step)

        return self.failed(f"gave up after {MAX_STEPS} steps without reaching {self.target!r}"), deferred

    async def run_calls(self, calls: list[tuple[Runs, dict]], group: list) -> bool:
        """Run a step's nodes on `group`, stopping at the first that fails. Their stragglers are ours."""
        with node_helpers.without_straggler_recovery():
            for node, params in calls:
                if await node.run(self.machine, group, params) != "SUCCESS":
                    return False
        return True

    async def sort(self, step: zone_nav.Step, group: list) -> tuple[list, list]:
        """(made the step, did not), read once every loading screen in the group has cleared."""
        await node_helpers.poll_until(lambda: node_helpers.none_loading(group), None, POLL_INTERVAL)
        zones = await _zones(group)
        leaders = [c for c, zone in zip(group, zones) if lands(step, zone)]
        return leaders, [c for c in group if not any(c is leader for leader in leaders)]

    async def retry(self, step: zone_nav.Step, calls, behind: list, start: str, leaders: list) -> list:
        """Give the clients still where the step began one more go at it, alone. Returns who is still behind."""
        zones = await _zones(behind)
        again = [c for c, zone in zip(behind, zones) if zone == start]
        if not again:
            return behind
        self.machine.log(f"Player(s) {self.who(again)} missed the {step.kind} step; trying it once more")
        # A gate that has been opened stays open: only the walk through it is retried.
        await self.run_calls(calls[-1:] if step.params.get("unlock") else calls, again)
        made, _ = await self.sort(step, again)
        leaders += made
        return [c for c in behind if not any(c is m for m in made)]

    async def regroup(self, group: list, zones: list[str]) -> str | None:
        """Bring a split group back together: at the hub within one world, else at home. Returns why it could not."""
        here = self.nav.world_of(zones[0])
        if here is not None and all(self.nav.world_of(zone) is here for zone in zones):
            self.machine.log(f"Clients are split across {sorted(set(zones))}; regrouping at {here.hub!r}")
            if await teleport_to_world_hub.run(self.machine, group, {"zone": here.hub}) != "SUCCESS":
                return f"could not regroup at {here.hub!r}"
            return None
        if self.nav.home is None:
            return f"clients are split across worlds: {sorted(set(zones))}"
        self.machine.log(f"Clients are split across worlds {sorted(set(zones))}; regrouping at home")
        if await GoHome.run(self.machine, group, {}) != "SUCCESS":
            return "could not regroup at home"
        return None
