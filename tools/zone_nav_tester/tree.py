"""A world's zones as a tree rooted at its hub, and how much of it is mapped.

Pure: the game's zone graph comes in as a ``neighbors`` function, so tests can hand in a
small one and the tester hands in ``zonemap.nav``.

WHY THE TREE FOLLOWS THE GAME'S GRAPH
    A zone sits under the zone you reach it from, so a gray node is always next to the zone to
    record it from. The map's own links go in too, since a teleporter or a list can join zones
    the graph does not. Many main zones are only reachable through an interior -- a dojo, an
    airship -- so the search walks through undeclared zones, at a cost that keeps it on streets
    wherever a street route exists, and hangs a zone under the last declared zone on its way.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from bartlebot import zone_nav

VERIFIED, TENTATIVE, NO_DATA = "verified", "tentative", "none"

_UNDECLARED_COST = 1000
"""Stepping through an undeclared zone costs this many street hops, as in main_zones.py."""


@dataclass
class ZoneTree:
    root: str
    children: dict[str, list[str]] = field(default_factory=dict)
    parent: dict[str, str] = field(default_factory=dict)
    unattached: list[str] = field(default_factory=list)
    """Declared zones the search never reached: no game link and no map link leads to them."""

    def path_to(self, zone: str) -> list[str]:
        """Declared zones from the root down to `zone`, both included; [] if not in the tree."""
        if zone != self.root and zone not in self.parent:
            return []
        path = [zone]
        while path[-1] != self.root:
            path.append(self.parent[path[-1]])
        return path[::-1]


def build(nav: zone_nav.NavMap, world_key: str, neighbors: Callable[[str], set[str]]) -> ZoneTree:
    world = nav.worlds[world_key]
    declared = set(world.zones)
    prefix = world_key + "/"

    def edges(zone: str) -> set[str]:
        linked = {s.to_zone for s in nav.links if s.from_zone == zone}
        return {n for n in neighbors(zone) | linked if n in declared or n.startswith(prefix)}

    tree = ZoneTree(world.hub)
    tiebreak = itertools.count()
    best = {world.hub: 0}
    last_declared = {world.hub: world.hub}
    heap = [(0, next(tiebreak), world.hub)]
    while heap:
        cost, _, zone = heapq.heappop(heap)
        if cost > best[zone]:
            continue
        for nxt in sorted(edges(zone)):
            total = cost + (1 if nxt in declared else _UNDECLARED_COST)
            if total < best.get(nxt, float("inf")):
                best[nxt] = total
                last_declared[nxt] = nxt if nxt in declared else last_declared[zone]
                if nxt in declared and nxt != world.hub:
                    tree.parent[nxt] = last_declared[zone]
                heapq.heappush(heap, (total, next(tiebreak), nxt))

    for zone in world.zones:
        if zone == world.hub:
            continue
        if zone in tree.parent:
            tree.children.setdefault(tree.parent[zone], []).append(zone)
        else:
            tree.unattached.append(zone)
    return tree


def statuses(nav: zone_nav.NavMap, world_key: str, avoid: Collection[str] = ()) -> dict[str, str]:
    """Each declared zone's color: routable on verified links, only on tentative ones, or not at all.

    Measured from the hub of the world whose door leads here, so a side world's zones are only
    green once the way in from its parent world is mapped too. `avoid` leaves kinds of link
    out, as the planner does -- avoiding teleporters and lists shows what is mapped on foot.
    """
    start = nav.worlds[nav.entry_world(world_key)].hub
    verified = zone_nav.reachable(nav, start, avoid=avoid)
    tentative = zone_nav.reachable(nav, start, include_unverified=True, avoid=avoid)
    return {
        zone: VERIFIED if zone in verified else TENTATIVE if zone in tentative else NO_DATA
        for zone in nav.worlds[world_key].zones
    }


def link_status(nav: zone_nav.NavMap, src: str, dst: str, avoid: Collection[str] = ()) -> str:
    """The best stored or generated link from `src` to `dst`: verified, tentative, or none."""
    found = [s for s in nav.links if s.from_zone == src and s.to_zone == dst and s.kind not in avoid]
    if any(s.verified for s in found):
        return VERIFIED
    return TENTATIVE if found else NO_DATA


def hops_to(
    nav: zone_nav.NavMap, tree: ZoneTree, target: str, avoid: Collection[str] = ()
) -> list[tuple[str, str, str]]:
    """(from, to, link status) for each hop down the tree to `target` -- what to record to get there."""
    path = tree.path_to(target)
    return [(a, b, link_status(nav, a, b, avoid)) for a, b in itertools.pairwise(path)]
