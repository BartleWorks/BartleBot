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
from dataclasses import dataclass, field, replace
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
"""Board a boat, airship or dragon, then press X to skip the ride: enter_zone_via_ride."""
LIST = "list"
"""Pick the destination from a zone-list object: enter_zone_via_list. Generated, never stored."""
HUB = "hub"
"""Click the compass hub button: teleport_to_world_hub. Implied for every zone, never stored."""
GATE = "gate"
"""Pick another world at a World Gate: enter_zone_via_list. Generated from each world's gate."""
HOME = "home"
"""Press HOME, landing in the dorm. Implied for every zone but the dorm, never stored."""

# ── Cost ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, order=True)
class Cost:
    """What a route costs, compared field by field: fewest transitions wins, the rest only break ties.

    Transitions rather than estimated seconds, because a loading screen is the one cost that is
    real on every machine, and it scales with the group where the game makes it: a teleporter
    that takes players one at a time is a loading screen per player.
    """

    transitions: int = 0
    waiting: float = 0.0
    """Seconds with no loading screen to show for them: a hub button cooling down, a gate opening."""
    effort: int = 0
    """How much can go wrong on the way; the kinds are ranked in EFFORT."""

    def __add__(self, other: Cost) -> Cost:
        return Cost(self.transitions + other.transitions, self.waiting + other.waiting, self.effort + other.effort)


EFFORT: dict[str, int] = {WALK: 0, HUB: 1, OBJECT: 2, LIST: 3, RIDE: 3, GATE: 3, HOME: 4}
"""A walk has the fewest ways to fail. HOME the most: castles to unequip, and a dungeon's exit warning."""
TELEPORT_COOLDOWN = 30.0
"""The cooldown the hub and HOME buttons share: what a second use of either in one route waits."""

_STORED_KINDS = (WALK, OBJECT, RIDE)
# Required xyz fields per way of starting a link, then the optional ones. A ride starts the way
# its `board` says: by interacting, like an object, or through a gate, like a walk.
_REQUIRED_XYZ: dict[str, tuple[str, ...]] = {
    WALK: ("xyz",),
    OBJECT: ("interact_xyz", "reset_xyz"),
}
_OPTIONAL_XYZ: dict[str, tuple[str, ...]] = {WALK: ("reset_xyz",)}


@dataclass(frozen=True)
class Step:
    """One zone change: a stored link, a generated list or gate link, the hub button, or HOME.

    ``params`` holds what the kind's zone-entry node needs beyond the two zones -- the xyz
    fields, ``sequential``, a ride's ``board`` and ``ride_zone``, ``board_zone`` on the step
    that finishes a ride from inside its cutscene zone, and a gate's destination ``world``. ``arrives_in`` is ``to_zone`` plus any
    zone the quest line swaps in for it; arriving in any of them counts.
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
    gate: dict | None = None
    """The World Gate standing in `door`: interact_xyz, reset_xyz, verified, source, note. None until recorded."""

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
    home: str | None = None
    """The zone HOME lands in: the dorm. Without one, no route presses HOME."""

    def __post_init__(self) -> None:
        self._zone_world = {zone: world.key for world in self.worlds.values() for zone in world.zones}
        self._out: dict[str, list[Step]] = defaultdict(list)
        for step in self.links:
            self._out[step.from_zone].append(step)
            if step.kind == RIDE and "ride_zone" in step.params:
                # A client left on the ride -- a run stopped on the dragon -- finishes it, rather than
                # pressing the hub button from a zone it can do nothing else in.
                ride_zone = step.params["ride_zone"]
                finish = replace(step, from_zone=ride_zone, params={**step.params, "board_zone": step.from_zone})
                self._out[ride_zone].append(finish)

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

    def steps_from(
        self,
        zone: str,
        *,
        include_unverified: bool = False,
        avoid: Collection[str] = (),
        avoid_links: Collection[Step] = (),
    ) -> list[Step]:
        """Every single step out of `zone`, leaving out unverified links, the kinds in `avoid`,
        and the particular steps in `avoid_links`.

        HOME works from anywhere, including a zone no world declares -- a castle, a zone off the
        map entirely -- so it is how a client with no other step still has a route.

        The hub button is left out at home: from the dorm it goes to the hub of the world last
        visited, not Wizard City's, so the dorm walks out through Ravenwood instead.
        """
        steps = [s for s in self._out.get(zone, ()) if (s.verified or include_unverified) and s.kind not in avoid]
        world = self.world_of(zone)
        if world is not None and zone not in (world.hub, self.home) and HUB not in avoid:
            steps.append(Step(HUB, zone, world.hub, source="implied"))
        if self.home is not None and zone != self.home and HOME not in avoid:
            steps.append(Step(HOME, zone, self.home, source="implied"))
        if avoid_links:
            avoided = {link_key(s) for s in avoid_links}
            steps = [s for s in steps if link_key(s) not in avoided]
        return steps


def link_key(step: Step) -> tuple[str, str, str]:
    """What identifies one zone change for `avoid_links`: its kind and its two zones.

    Not the Step itself, whose equality also compares provenance, so a step recorded from a
    run still matches the one the map generates.
    """
    return step.kind, step.from_zone, step.to_zone


def step_cost(step: Step, *, clients: int = 1, teleport_wait: float = 0.0) -> Cost:
    """What `step` costs for a group of `clients`. `teleport_wait` is the cooldown left, for a hub or home step."""
    transitions, waiting, effort = 1, 0.0, EFFORT[step.kind]
    if step.kind == RIDE and "ride_zone" in step.params:
        transitions += 1  # the cutscene zone is a loading screen of its own
    if step.kind == OBJECT and step.params.get("sequential"):
        # One player at a time: each one's loading screen is its own transition.
        transitions = max(clients, 1)
        waiting += step.params.get("object_use_delay", OBJECT_USE_DELAY) * max(clients - 1, 0)
    if unlock := step.params.get("unlock"):
        waiting += unlock["opens_after"]
        effort += len(unlock["objects"])
    if step.kind in (HUB, HOME):
        waiting += teleport_wait
    return Cost(transitions, waiting, effort)


def plan(
    nav: NavMap,
    start: str,
    target: str,
    *,
    teleport_cooldown: float = 0.0,
    clients: int = 1,
    include_unverified: bool = False,
    avoid: Collection[str] = (),
    avoid_links: Collection[Step] = (),
) -> list[Step] | None:
    """The cheapest steps from `start` to `target`, [] if already there, None if unreachable.

    `avoid_links` leaves out particular steps rather than whole kinds: a teleporter that just
    failed for every client, most likely one this account has not unlocked.

    `teleport_cooldown` is what is left right now of the cooldown the hub and HOME buttons
    share. The search state carries whether either has been used yet, because a second use of
    either waits the full cooldown the first one started.

    `avoid` leaves whole kinds out -- ``{OBJECT, LIST}`` for a route on foot, which is how a
    street route gets tested when a teleporter would always win, and how a user skips
    teleporters slow enough to lose to walking.
    """
    if start == target:
        return []

    tiebreak = itertools.count()
    best: dict[tuple[str, bool], Cost] = {(start, False): Cost()}
    prev: dict[tuple[str, bool], tuple[tuple[str, bool], Step]] = {}
    heap = [(Cost(), next(tiebreak), start, False)]
    while heap:
        cost, _, zone, used_teleport = heapq.heappop(heap)
        state = (zone, used_teleport)
        if cost > best[state]:
            continue
        if zone == target:
            route = []
            while state in prev:
                state, step = prev[state]
                route.append(step)
            return route[::-1]
        for step in nav.steps_from(zone, include_unverified=include_unverified, avoid=avoid, avoid_links=avoid_links):
            teleports = step.kind in (HUB, HOME)
            wait = (TELEPORT_COOLDOWN if used_teleport else teleport_cooldown) if teleports else 0.0
            nxt = (step.to_zone, used_teleport or teleports)
            total = cost + step_cost(step, clients=clients, teleport_wait=wait)
            if nxt not in best or total < best[nxt]:
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
            gate=_parse_gate(spec, key, problems),
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

    home = raw.get("home")
    if home is not None and home not in zone_world:
        problems.append(f"home {home!r} is not a declared zone")

    links: list[Step] = _gate_links(worlds)
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
        # A ride boarded through a gate is started the same way, so it gets the same default.
        on_foot = step.kind == WALK or step.params.get("board") == WALK
        if on_foot and "reset_xyz" not in step.params and step.from_zone in by_zone:
            if (landing := by_zone[step.from_zone].arrival(step.from_zone, step.to_zone)) is not None:
                step.params["reset_xyz"] = landing

    counts = defaultdict(int)
    for step in links:
        counts[(step.kind, step.from_zone, step.to_zone)] += 1
    problems.extend(
        f"{kind} link {src!r} -> {dst!r} appears more than once" for (kind, src, dst), n in counts.items() if n > 1
    )
    # A client found in a cutscene zone finishes that zone's ride, so the zone must name one ride.
    rides_through = defaultdict(int)
    for step in links:
        if "ride_zone" in step.params:
            rides_through[step.params["ride_zone"]] += 1
    problems.extend(f"ride_zone {zone!r} is used by more than one ride" for zone, n in rides_through.items() if n > 1)

    if problems:
        raise ValueError("zone_nav.json is invalid:\n  " + "\n  ".join(problems))
    return NavMap(worlds, links, home)


def _gate_links(worlds: dict[str, World]) -> list[Step]:
    """A gate link from each world's recorded World Gate to every other world with a door.

    Every gate lists every world the character has unlocked, so the destination needs a door
    -- where its gate drops arriving players -- but no gate recorded of its own. A side world
    has no door and is not on the list; its way in is an ordinary link from its parent.
    """
    return [
        Step(
            GATE,
            world.door,
            other.door,
            {"interact_xyz": world.gate["interact_xyz"], "reset_xyz": world.gate["reset_xyz"], "world": other.key},
            verified=world.gate["verified"],
            source=world.gate["source"],
            note=world.gate["note"],
        )
        for world in worlds.values()
        if world.gate is not None and world.door is not None
        for other in worlds.values()
        if other is not world and other.door is not None
    ]


def _parse_gate(spec: dict, where: str, problems: list[str]) -> dict | None:
    """A world's World Gate. It stands where go_to_world lands, so only a world with a door has one."""
    if "gate" not in spec:
        return None
    gate = spec["gate"]
    before = len(problems)
    if spec.get("door") is None:
        problems.append(f"{where}: a gate needs the world's door, the zone it stands in")
    if not isinstance(gate, dict):
        problems.append(f"{where}: gate must be an object")
        return None
    for name in ("interact_xyz", "reset_xyz"):
        if _bad_xyz(gate.get(name)):
            problems.append(f"{where}: gate needs {name} as an {{x, y, z}} object")
    if not isinstance(gate.get("verified", False), bool):
        problems.append(f"{where}: gate verified must be true or false")
    if len(problems) > before:
        return None
    return {
        "interact_xyz": gate["interact_xyz"],
        "reset_xyz": gate["reset_xyz"],
        "verified": gate.get("verified", False),
        "source": str(gate.get("source", "")),
        "note": str(gate.get("note", "")),
    }


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
    if kind not in _STORED_KINDS:
        problems.append(f"{where}: unknown kind {kind!r} (stored links are {', '.join(_STORED_KINDS)})")
        return None
    src, dst = link.get("from"), link.get("to")
    for label, zone in (("from", src), ("to", dst)):
        if zone not in zone_world:
            problems.append(f"{where}: {label} zone {zone!r} is not declared")

    board = link.get("board", OBJECT) if kind == RIDE else kind
    if board not in _REQUIRED_XYZ:
        problems.append(f"{where}: board must be {WALK!r} or {OBJECT!r}, not {board!r}")
        return None
    params: dict = {"board": board} if kind == RIDE else {}
    for name in _REQUIRED_XYZ[board]:
        if _bad_xyz(link.get(name)):
            problems.append(f"{where}: {name} must be an {{x, y, z}} object")
        else:
            params[name] = link[name]
    for name in _OPTIONAL_XYZ.get(board, ()):
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
    if "unlock" in link:
        # A gate that objects open, and that appears only a while later: Tomb of Storms.
        unlock = link["unlock"]
        objects = unlock.get("objects") if isinstance(unlock, dict) else None
        opens_after = unlock.get("opens_after") if isinstance(unlock, dict) else None
        if kind != WALK:
            problems.append(f"{where}: only a walk can have an unlock")
        elif (
            not isinstance(objects, list)
            or not objects
            or any(
                not isinstance(o, dict) or _bad_xyz(o.get("interact_xyz")) or _bad_xyz(o.get("reset_xyz"))
                for o in objects
            )
        ):
            problems.append(f"{where}: unlock needs objects, each with interact_xyz and reset_xyz")
        elif not isinstance(opens_after, int | float) or isinstance(opens_after, bool) or opens_after < 0:
            problems.append(f"{where}: unlock needs opens_after, the seconds the gate takes to appear")
        else:
            params["unlock"] = {
                "objects": [{"interact_xyz": o["interact_xyz"], "reset_xyz": o["reset_xyz"]} for o in objects],
                "opens_after": float(opens_after),
            }
    if kind == RIDE:
        # ride_zone is optional: the Krokotopia boats ride inside the zone they start in.
        ride_zone = link.get("ride_zone")
        if ride_zone is None:
            if board == WALK:
                problems.append(f"{where}: a ride boarded through a gate needs ride_zone, since the gate loads one")
        elif not isinstance(ride_zone, str) or not ride_zone:
            problems.append(f"{where}: ride_zone must be a zone name")
        elif hit := check_zone(ride_zone):
            problems.append(f"{where}: ride_zone {ride_zone!r} is restricted: {hit.describe()}")
        elif ride_zone in zone_world:
            problems.append(f"{where}: ride_zone {ride_zone!r} is a declared zone, and a route cannot stop on a ride")
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
    # A member with no object recorded yet (spot == {}) is a destination only; list_aliases
    # is about how the entry is shown, so it applies either way and does not by itself count
    # as "the object is recorded".
    has_object: dict[str, bool] = {}
    for zone, spot in zones.items():
        if zone not in zone_world:
            problems.append(f"{where}: zone {zone!r} is not declared")
        aliases = spot.get("list_aliases", []) if isinstance(spot, dict) else []
        if not isinstance(aliases, list) or any(not isinstance(a, str) or not a for a in aliases):
            problems.append(f"{where}: {zone!r} list_aliases must be a list of non-empty strings")
        aliases_only = spot.get("list_aliases_only", False) if isinstance(spot, dict) else False
        if not isinstance(aliases_only, bool):
            problems.append(f"{where}: {zone!r} list_aliases_only must be true or false")
        elif aliases_only and not aliases:
            problems.append(f"{where}: {zone!r} sets list_aliases_only but has no list_aliases to use")
        has_object[zone] = isinstance(spot, dict) and ("interact_xyz" in spot or "reset_xyz" in spot)
        if not has_object[zone]:
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
    aliases_by_zone = {zone: (spot.get("list_aliases") or []) for zone, spot in zones.items() if isinstance(spot, dict)}
    aliases_only_by_zone = {
        zone: bool(spot.get("list_aliases_only", False)) for zone, spot in zones.items() if isinstance(spot, dict)
    }
    return [
        Step(
            LIST,
            src,
            dst,
            {
                "interact_xyz": zones[src]["interact_xyz"],
                "reset_xyz": zones[src]["reset_xyz"],
                **({"list_aliases": aliases_by_zone[dst]} if aliases_by_zone.get(dst) else {}),
                **({"list_aliases_only": True} if aliases_only_by_zone.get(dst) else {}),
            },
            verified=verified,
            source=source,
            note=note,
        )
        for src, dst in itertools.permutations(zones, 2)
        if has_object[src]
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
