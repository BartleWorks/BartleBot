"""The zone navigation map, and the cheapest route across it.

``bartlebot/data/zone_nav.json`` declares each world's zones and the links between them.
This module validates it, generates the links it stores compactly (list networks, the hub
button), and plans routes costed in estimated seconds. It does not move a client: executing
a step is the navigation node's job, through the zone-entry node each kind names.

docs/ZONE_NAVIGATION.md has the reasoning -- why teleporters are plain object links, why
lists are networks, and why a route is costed in time rather than hops.
"""

from __future__ import annotations

import heapq
import itertools
import json
import logging
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from bartlebot.nodes.timing import OBJECT_USE_DELAY
from bartlebot.restricted_zones import check_zone

logger = logging.getLogger(__name__)

ZONE_NAV_PATH = Path(__file__).resolve().parent / "data" / "zone_nav.json"

WALK = "walk"
"""Touch a transition volume: enter_zone."""
OBJECT = "object"
"""Interact with a teleporter or door: enter_zone_via_object."""
RIDE = "ride"
"""Interact, land in a cutscene zone, press X to skip it."""
LIST = "list"
"""Pick the destination from a zone-list object: enter_zone_via_list. Generated, never stored."""
HUB = "hub"
"""Click the compass hub button: teleport_to_world_hub. Implied for every zone, never stored."""

# ── Cost estimates ─────────────────────────────────────────────────────────────
# Seconds, so that a route is the one a user would call fastest. These are estimates to
# tune, not timeouts -- nothing waits on them, which is why they are not in nodes/timing.py.
LOAD_SECONDS = 10.0
COST_SECONDS: dict[str, float] = {
    WALK: LOAD_SECONDS,
    OBJECT: LOAD_SECONDS + 2.0,  # the interact prompt
    LIST: LOAD_SECONDS + 5.0,  # opening the list and paging to the entry
    RIDE: 2 * LOAD_SECONDS + 3.0,  # into the cutscene zone, then out of it
    HUB: LOAD_SECONDS + 1.0,  # a hair over a walk, so a walk wins an exact tie
}
HUB_BUTTON_COOLDOWN = 30.0
"""What a second hub-button use in one route waits, since the first just started the cooldown."""

# Required xyz fields per stored kind, then the optional ones.
_REQUIRED_XYZ: dict[str, tuple[str, ...]] = {
    WALK: ("xyz",),
    OBJECT: ("interact_xyz", "reset_xyz"),
    RIDE: ("interact_xyz", "reset_xyz"),
}
_OPTIONAL_XYZ: dict[str, tuple[str, ...]] = {WALK: ("reset_xyz",)}


@dataclass(frozen=True)
class Step:
    """One zone change: a stored link, a generated list link, or the hub button.

    ``params`` holds what the kind's zone-entry node needs beyond the two zones -- the xyz
    fields, ``sequential``, ``ride_zone``. ``arrives_in`` is ``to_zone`` plus any zone the
    quest line swaps in for it; arriving in any of them counts.
    """

    kind: str
    from_zone: str
    to_zone: str
    params: dict = field(default_factory=dict, compare=False)
    also_arrives_in: tuple[str, ...] = ()
    verified: bool = True
    source: str = ""
    note: str = ""

    @property
    def arrives_in(self) -> tuple[str, ...]:
        return (self.to_zone, *self.also_arrives_in)


@dataclass
class World:
    key: str
    hub: str
    door: str | None = None
    entered_from: str | None = None
    zones: dict[str, dict] = field(default_factory=dict)

    def destinations(self) -> list[str]:
        """Zones a user may pick as a destination: every declared zone but the connectors."""
        return [zone for zone, attrs in self.zones.items() if attrs.get("destination", True)]

    def label(self, zone: str) -> str | None:
        """The name to show for `zone` when the game's own is ambiguous, e.g. "Zanadu Sewers"."""
        return self.zones.get(zone, {}).get("label")

    def arrival(self, zone: str, came_from: str) -> dict | None:
        """Where a player lands in `zone` coming from `came_from`, if recorded."""
        return self.zones.get(zone, {}).get("arrivals", {}).get(came_from)


@dataclass
class NavMap:
    worlds: dict[str, World] = field(default_factory=dict)
    links: list[Step] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._zone_world = {zone: world.key for world in self.worlds.values() for zone in world.zones}
        self._out: dict[str, list[Step]] = defaultdict(list)
        for step in self.links:
            self._out[step.from_zone].append(step)

    def world_of(self, zone: str) -> World | None:
        """The world a zone belongs to: the one declaring it, else the one its path starts with.

        The fallback is what lets a client in an undeclared zone -- a boss room -- still use
        the hub button.
        """
        key = self._zone_world.get(zone) or zone.split("/", 1)[0]
        return self.worlds.get(key)

    def entry_world(self, world: str) -> str:
        """The world go_to_world should be asked for to reach `world`: follows entered_from."""
        current = self.worlds[world]
        while current.entered_from:
            current = self.worlds[current.entered_from]
        return current.key

    def steps_from(self, zone: str, *, include_unverified: bool = False, avoid: Collection[str] = ()) -> list[Step]:
        """Every single step out of `zone`, leaving out unverified links and the kinds in `avoid`."""
        steps = [s for s in self._out.get(zone, ()) if (s.verified or include_unverified) and s.kind not in avoid]
        world = self.world_of(zone)
        if world is not None and zone != world.hub and HUB not in avoid:
            steps.append(Step(HUB, zone, world.hub, source="implied"))
        return steps


def step_cost(step: Step, *, clients: int = 1, hub_wait: float = 0.0) -> float:
    """Estimated seconds for `step`. `hub_wait` is the cooldown left if it is a hub step."""
    cost = COST_SECONDS[step.kind]
    if step.kind == OBJECT and step.params.get("sequential"):
        cost += step.params.get("object_use_delay", OBJECT_USE_DELAY) * max(clients - 1, 0)
    if step.kind == HUB:
        cost += hub_wait
    return cost


def plan(
    nav: NavMap,
    start: str,
    target: str,
    *,
    hub_cooldown: float = 0.0,
    clients: int = 1,
    include_unverified: bool = False,
    avoid: Collection[str] = (),
) -> list[Step] | None:
    """The cheapest steps from `start` to `target`, [] if already there, None if unreachable.

    `hub_cooldown` is what is left of the hub button's cooldown right now. The search state
    carries whether the button has been used yet, because a second use waits the full
    cooldown the first one started.

    `avoid` leaves whole kinds out -- ``{OBJECT, LIST}`` for a route on foot, which is how a
    street route gets tested when a teleporter would always win, and how a user skips
    teleporters slow enough to lose to walking.
    """
    if start == target:
        return []

    tiebreak = itertools.count()
    best: dict[tuple[str, bool], float] = {(start, False): 0.0}
    prev: dict[tuple[str, bool], tuple[tuple[str, bool], Step]] = {}
    heap = [(0.0, next(tiebreak), start, False)]
    while heap:
        cost, _, zone, used_hub = heapq.heappop(heap)
        state = (zone, used_hub)
        if cost > best[state]:
            continue
        if zone == target:
            route = []
            while state in prev:
                state, step = prev[state]
                route.append(step)
            return route[::-1]
        for step in nav.steps_from(zone, include_unverified=include_unverified, avoid=avoid):
            wait = (HUB_BUTTON_COOLDOWN if used_hub else hub_cooldown) if step.kind == HUB else 0.0
            nxt = (step.to_zone, used_hub or step.kind == HUB)
            total = cost + step_cost(step, clients=clients, hub_wait=wait)
            if total < best.get(nxt, float("inf")):
                best[nxt] = total
                prev[nxt] = (state, step)
                heapq.heappush(heap, (total, next(tiebreak), step.to_zone, nxt[1]))
    return None


def reachable(nav: NavMap, start: str, *, include_unverified: bool = False, avoid: Collection[str] = ()) -> set[str]:
    """Every zone some route from `start` reaches, `start` included."""
    seen = {start}
    stack = [start]
    while stack:
        for step in nav.steps_from(stack.pop(), include_unverified=include_unverified, avoid=avoid):
            if step.to_zone not in seen:
                seen.add(step.to_zone)
                stack.append(step.to_zone)
    return seen


# ── Loading ────────────────────────────────────────────────────────────────────


@cache
def load() -> NavMap:
    """The shipped map. An unreadable file logs and returns an empty map.

    Nothing routes on an empty map, so a node fails with "no route" rather than the app
    refusing to start. tests/test_zone_nav.py parses the shipped file strictly, which keeps
    a broken one from reaching a user in the first place.
    """
    try:
        return parse(json.loads(ZONE_NAV_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        logger.exception("Could not load the zone navigation map from %s", ZONE_NAV_PATH)
        return NavMap()


def parse(raw: dict) -> NavMap:
    """Validate a zone_nav.json document. Raises ValueError listing every problem found."""
    raw_worlds = raw.get("worlds") if isinstance(raw, dict) else None
    if not isinstance(raw_worlds, dict) or not raw_worlds:
        raise ValueError("zone_nav.json declares no worlds")

    problems: list[str] = []
    zone_world: dict[str, str] = {}
    worlds: dict[str, World] = {}
    for key, spec in raw_worlds.items():
        zones = spec.get("zones") or {}
        for zone, attrs in zones.items():
            if zone in zone_world:
                problems.append(f"{zone!r} is declared by both {zone_world[zone]} and {key}")
            zone_world[zone] = key
            if hit := check_zone(zone):
                problems.append(f"{zone!r} is restricted: {hit.describe()}")
            if not isinstance(attrs, dict):
                problems.append(f"{key}: {zone!r} must map to an object")
            else:
                _check_zone_attrs(attrs, f"{key}: {zone!r}", problems)
        worlds[key] = World(
            key=key,
            hub=spec.get("hub", ""),
            door=spec.get("door"),
            entered_from=spec.get("entered_from"),
            zones={zone: dict(attrs) if isinstance(attrs, dict) else {} for zone, attrs in zones.items()},
        )

    for world in worlds.values():
        if world.hub not in zone_world:
            problems.append(f"{world.key}: hub {world.hub!r} is not a declared zone")
        if world.door is not None and zone_world.get(world.door) != world.key:
            problems.append(f"{world.key}: door {world.door!r} is not one of its own zones")
        if world.entered_from is not None and world.entered_from not in worlds:
            problems.append(f"{world.key}: entered_from {world.entered_from!r} is not a world")
        seen, current = {world.key}, world.entered_from
        while current in worlds:
            if current in seen:
                problems.append(f"{world.key}: entered_from loops back on itself")
                break
            seen.add(current)
            current = worlds[current].entered_from

    links: list[Step] = []
    for key, spec in raw_worlds.items():
        for i, link in enumerate(spec.get("links") or []):
            if step := _parse_link(link, f"{key} link {i}", zone_world, problems):
                links.append(step)
        for i, network in enumerate(spec.get("list_networks") or []):
            links.extend(_parse_network(network, f"{key} list network {i}", zone_world, problems))

    for world in worlds.values():
        for zone, attrs in world.zones.items():
            arrivals = attrs.get("arrivals")
            for came_from in arrivals if isinstance(arrivals, dict) else ():
                if came_from not in zone_world:
                    problems.append(f"{world.key}: {zone!r} has an arrival from undeclared zone {came_from!r}")

    by_zone = {zone: world for world in worlds.values() for zone in world.zones}
    for step in links:
        # A walk's reset point defaults to where a player lands coming back the other way:
        # the game puts arrivals clear of the gate, so the trigger cannot fire there.
        if step.kind == WALK and "reset_xyz" not in step.params and step.from_zone in by_zone:
            if (landing := by_zone[step.from_zone].arrival(step.from_zone, step.to_zone)) is not None:
                step.params["reset_xyz"] = landing

    counts = defaultdict(int)
    for step in links:
        counts[(step.kind, step.from_zone, step.to_zone)] += 1
    problems.extend(
        f"{kind} link {src!r} -> {dst!r} appears more than once" for (kind, src, dst), n in counts.items() if n > 1
    )

    if problems:
        raise ValueError("zone_nav.json is invalid:\n  " + "\n  ".join(problems))
    return NavMap(worlds, links)


def _check_zone_attrs(attrs: dict, where: str, problems: list[str]) -> None:
    if not isinstance(attrs.get("destination", True), bool):
        problems.append(f"{where}: destination must be true or false")
    if not isinstance(attrs.get("label", ""), str):
        problems.append(f"{where}: label must be a string")
    arrivals = attrs.get("arrivals", {})
    if not isinstance(arrivals, dict) or any(_bad_xyz(spot) for spot in arrivals.values()):
        problems.append(f"{where}: arrivals must map zones to {{x, y, z}} objects")


def _bad_xyz(value) -> bool:
    return not (
        isinstance(value, dict)
        and all(isinstance(value.get(axis), int | float) and not isinstance(value.get(axis), bool) for axis in "xyz")
    )


def _parse_link(link: dict, where: str, zone_world: dict[str, str], problems: list[str]) -> Step | None:
    before = len(problems)
    kind = link.get("kind")
    if kind not in _REQUIRED_XYZ:
        problems.append(f"{where}: unknown kind {kind!r} (stored links are {', '.join(_REQUIRED_XYZ)})")
        return None
    src, dst = link.get("from"), link.get("to")
    for label, zone in (("from", src), ("to", dst)):
        if zone not in zone_world:
            problems.append(f"{where}: {label} zone {zone!r} is not declared")

    params: dict = {}
    for name in _REQUIRED_XYZ[kind]:
        if _bad_xyz(link.get(name)):
            problems.append(f"{where}: {name} must be an {{x, y, z}} object")
        else:
            params[name] = link[name]
    for name in _OPTIONAL_XYZ.get(kind, ()):
        if name in link:
            if _bad_xyz(link[name]):
                problems.append(f"{where}: {name} must be an {{x, y, z}} object")
            else:
                params[name] = link[name]

    if kind == OBJECT:
        params["sequential"] = bool(link.get("sequential", False))
        if "object_use_delay" in link:
            delay = link["object_use_delay"]
            if not isinstance(delay, int | float) or isinstance(delay, bool) or delay < 0:
                problems.append(f"{where}: object_use_delay must be a number >= 0")
            else:
                params["object_use_delay"] = float(delay)
    if kind == RIDE:
        ride_zone = link.get("ride_zone")
        if not isinstance(ride_zone, str) or not ride_zone:
            problems.append(f"{where}: a ride needs ride_zone")
        elif hit := check_zone(ride_zone):
            problems.append(f"{where}: ride_zone {ride_zone!r} is restricted: {hit.describe()}")
        else:
            params["ride_zone"] = ride_zone

    also = link.get("also_arrives_in", [])
    if not isinstance(also, list) or any(zone not in zone_world for zone in also):
        problems.append(f"{where}: also_arrives_in must list declared zones")
    verified = link.get("verified", False)
    if not isinstance(verified, bool):
        problems.append(f"{where}: verified must be true or false")
    if not isinstance(link.get("volume", ""), str):
        problems.append(f"{where}: volume must be a string")

    if len(problems) > before:
        return None
    return Step(
        kind,
        src,
        dst,
        params,
        also_arrives_in=tuple(also),
        verified=verified,
        source=str(link.get("source", "")),
        note=str(link.get("note", "")),
    )


def _parse_network(network: dict, where: str, zone_world: dict[str, str], problems: list[str]) -> list[Step]:
    """A list link from each member with a recorded object to every other member.

    A member mapped to ``{}`` is on the list but its own object is not recorded yet, so the
    list routes into it and not out of it. The tester builds networks that way, one list use
    at a time.
    """
    before = len(problems)
    zones = network.get("zones") or {}
    if not zones:
        problems.append(f"{where}: a list network needs at least one zone")
    for zone, spot in zones.items():
        if zone not in zone_world:
            problems.append(f"{where}: zone {zone!r} is not declared")
        if spot == {}:
            continue
        for name in ("interact_xyz", "reset_xyz"):
            if not isinstance(spot, dict) or _bad_xyz(spot.get(name)):
                problems.append(f"{where}: {zone!r} needs {name} as an {{x, y, z}} object")
    verified = network.get("verified", False)
    if not isinstance(verified, bool):
        problems.append(f"{where}: verified must be true or false")
    if len(problems) > before:
        return []

    source, note = str(network.get("source", "")), str(network.get("note", ""))
    return [
        Step(
            LIST,
            src,
            dst,
            {"interact_xyz": zones[src]["interact_xyz"], "reset_xyz": zones[src]["reset_xyz"]},
            verified=verified,
            source=source,
            note=note,
        )
        for src, dst in itertools.permutations(zones, 2)
        if zones[src]
    ]


# ── Writing ────────────────────────────────────────────────────────────────────

_ONE_LINE_WIDTH = 100


def dumps(raw: dict) -> str:
    """zone_nav.json's canonical text: two-space indent, small flat objects on one line.

    The tester writes the file and people edit it by hand, so both go through one layout --
    otherwise every recording reformats the whole file and the diff hides what was recorded.
    tests/test_zone_nav.py checks the shipped file is already in this form.
    """
    return _encode(raw, 0) + "\n"


def _encode(value, depth: int) -> str:
    if not isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    items = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
    open_, close = ("{", "}") if isinstance(value, dict) else ("[", "]")
    if not items:
        return open_ + close

    def entry(key, item, inner: int) -> str:
        text = _encode(item, inner)
        return f"{json.dumps(key, ensure_ascii=False)}: {text}" if isinstance(value, dict) else text

    if all(not isinstance(item, dict | list) or not item for _, item in items):
        flat = open_ + ", ".join(entry(k, v, 0) for k, v in items) + close
        if len(flat) + 2 * depth <= _ONE_LINE_WIDTH:
            return flat
    pad = "  " * (depth + 1)
    body = ",\n".join(pad + entry(k, v, depth + 1) for k, v in items)
    return f"{open_}\n{body}\n{'  ' * depth}{close}"
