"""Propose each world's main zones: the streets and hubs you walk between, not the rooms off them.

A PROPOSAL, NOT A LIST
    Connection counts alone do not find main zones. ``zonemap.nav`` records every zone
    change the game allows, so the spiral door links to 200+ zones, the Commons links to
    monthly-event portals, and every Treasure Tower floor has three links. Peeling off
    leaf zones fails too: a dungeon with two entrances forms a loop and survives.

    The folder layout is the reliable signal. Side rooms, boss rooms, gauntlets, towers
    and events live under folders like ``Interiors/`` and ``Gauntlets/``; streets do not.
    So a candidate is a zone of the world that

        - has no segment matching MINOR (Interiors, Gauntlets, TreasureTower, ...)
        - is not restricted (bartlebot/game/restricted_zones.py)
        - links to at least one other zone of the same world

    and the columns exist to make hand review fast:

        hops    links from the world's hub ('-' = unreachable)
        links   links to other zones of the same world
        rooms   of those, how many are side rooms (MINOR, or linked to nothing else)
        deimos  '*' if wizsprinter's hand-built zoneMap.txt lists the zone
        name    English display name
        via     non-candidate zones the route from the hub must pass through (an interior is
                used only when no route through streets exists); these bridges (a dojo, a
                building, an airship flight) are the transitions to check by hand

    After the table, zones Deimos lists that are not candidates are printed with the
    reason, which is the quickest check on what the filter wrongly dropped.

USAGE
    uv run python tools/zone_graph/main_zones.py --world WizardCity --world Krokotopia
    uv run python tools/zone_graph/main_zones.py --out main_zones.local.json
"""

from __future__ import annotations

import argparse
import heapq
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import zonemap_nav
from wad_common import (
    GAME_DATA,
    TOOLS_DIR,
    TRAVERSAL_DATA,
    Deserializer,
    display_name,
    read_traversal_section,
    root_wad,
    text,
    zone_display_names,
)
from zone_data import zone_record

sys.path.insert(0, str(TOOLS_DIR.parent))
from bartlebot.game.restricted_zones import check_zone  # noqa: E402

# A path segment matching this marks a side room, instance or non-questing zone.
MINOR = re.compile(
    r"^(Interiors|.*Gauntlet.*|TreasureTower|Tutorial.*|QA_.*|.*Event.*|Housing.*|.*Cinematic.*|.*_Lite"
    # copies of a zone for a mode, an instance or a replay, and the airship flights between streets
    r"|.*_(Challenge|Hard|Repeat|STRD)|.*_Instance\d*|.*_XTRA.*|.*Flight(To|From).*)$",
    re.IGNORECASE,
)
# A zone with more same-world links than this teleports anywhere (spiral door, Wizard City's
# Library) and is not walked through.
FANOUT = 60
# Cost of walking through a non-candidate zone, against 1 for a candidate.
INTERIOR_COST = 1000
# WorldHubZones.xml also maps housing, pet derby and event "worlds"; they are not questing worlds.
NOT_A_WORLD = re.compile(r"^(Housing.*|PetDerby|DD_.*|Heroic_.*|Raids|MonthlyEvents)$")


def is_minor(zone: str) -> bool:
    return any(MINOR.match(segment) for segment in zone.split("/")[1:])


def world_hubs(game_data: Path) -> dict[str, dict[str, set[str]]]:
    """folder -> {"hubs": {...}, "doors": {...}}, grouped by the hub's own top-level folder.

    Grouping by folder rather than the entry's world name folds ``KT_Selenopolis`` into
    Krokotopia and the two Grizzleheim entries together.
    """
    record = Deserializer()(bytes(root_wad(game_data)["WorldHubZones.xml"]))
    worlds: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"hubs": set(), "doors": set()})
    for entry in record["m_hubZoneMapping"]:
        hub, door = text(entry["m_hubZone"]), text(entry["m_universeTPZone"])
        folder = hub.split("/")[0]
        if NOT_A_WORLD.match(text(entry["m_world"])) or NOT_A_WORLD.match(folder) or check_zone(hub):
            continue
        worlds[folder]["hubs"].add(hub)
        if door.startswith(f"{folder}/"):
            worlds[folder]["doors"].add(door)
    return dict(worlds)


def deimos_zones(world: str, root: Path) -> set[str]:
    try:
        rows = read_traversal_section("zoneMap.txt", world, root)
    except FileNotFoundError:
        return set()
    return {zone for row in rows if row[0] != "ZONEDOOR" for zone in row[:2]}


def propose(world: str, hubs: set[str], doors: set[str], graph, names, deserialize, game_data, traversal_data):
    zones = {n for n in graph.names if n.startswith(f"{world}/")}
    # WorldHubZones.xml names some hubs zonemap.nav does not have (Grizzleheim/GH_HFjord).
    hubs, doors = hubs & zones, doors & zones
    same_world = {z: graph.neighbors(z) & zones for z in zones}
    candidates = {z for z in zones if not is_minor(z) and not check_zone(z) and same_world[z]} | hubs

    # Walk from the hubs through any zone of the world, because some areas are only
    # reached through an interior (MooShu's Plague and Death areas, Scotland Yard,
    # Karamelle past Nibbleheim). Restricted zones are never walked, and neither are
    # teleport-anywhere zones: a route through the spiral door is not a walk.
    # An interior is only a last resort: entering one costs far more than a street, so a
    # route through streets always wins when one exists. A plain shortest walk sent The
    # Dreaming through Dreamspeak when it is reached from Eucalyptus Forest.
    walkable = {z for z in zones if not check_zone(z) and len(same_world[z]) <= FANOUT}
    cost = {hub: 0 for hub in hubs}
    hops = {hub: 0 for hub in hubs}
    parent: dict[str, str] = {}
    heap = [(0, hub) for hub in sorted(hubs)]
    while heap:
        spent, zone = heapq.heappop(heap)
        if spent > cost[zone]:
            continue
        for n in sorted(same_world[zone] & walkable):
            step = spent + (1 if n in candidates else INTERIOR_COST)
            if step < cost.get(n, step + 1):
                cost[n], hops[n], parent[n] = step, hops[zone] + 1, zone
                heapq.heappush(heap, (step, n))

    def via(zone: str) -> list[str]:
        """The non-candidate zones on the shortest walk from the hub to *zone*."""
        path = []
        while zone in parent:
            zone = parent[zone]
            if zone not in candidates:
                path.append(zone)
        return path[::-1]

    def english(zone: str) -> str:
        try:
            return display_name(zone_record(zone, deserialize, game_data), names) or "?"
        except (FileNotFoundError, KeyError):
            return "?"

    deimos = deimos_zones(world, traversal_data)
    rows = []
    for zone in candidates:
        rooms = sum(1 for n in same_world[zone] if is_minor(n) or len(same_world[n]) <= 1)
        rows.append(
            {
                "zone": zone,
                "name": english(zone),
                "hops": hops.get(zone),
                "links": len(same_world[zone]),
                "rooms": rooms,
                "hub": zone in hubs,
                "deimos": zone in deimos,
                "via": via(zone),
            }
        )
    rows.sort(key=lambda r: (r["hops"] is None, r["hops"] or 0, r["zone"]))

    missed = []
    for zone in sorted(deimos - candidates):
        if zone not in zones:
            reason = "not in zonemap.nav" if zone not in graph.names else "different world"
        elif check_zone(zone):
            reason = "restricted"
        elif is_minor(zone):
            reason = "minor folder"
        else:
            reason = "no same-world links"
        missed.append({"zone": zone, "name": english(zone) if zone in zones else "?", "reason": reason})
    return rows, missed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-data", type=Path, default=GAME_DATA)
    ap.add_argument("--traversal-data", type=Path, default=TRAVERSAL_DATA)
    ap.add_argument("--world", action="append", help="a world folder, e.g. WizardCity; repeatable (default: all)")
    ap.add_argument("--out", type=Path, help="also write the proposal as JSON")
    args = ap.parse_args()

    graph = zonemap_nav.load(args.game_data)
    names = zone_display_names(root_wad(args.game_data))
    deserialize = Deserializer()
    worlds = world_hubs(args.game_data)
    selected = args.world or sorted(worlds)
    unknown = [w for w in selected if w not in worlds]
    if unknown:
        print(f"no hub in WorldHubZones.xml for {unknown}; worlds are: {', '.join(sorted(worlds))}")
        return 1

    payload = {}
    for world in selected:
        hubs, doors = worlds[world]["hubs"], worlds[world]["doors"]
        rows, missed = propose(world, hubs, doors, graph, names, deserialize, args.game_data, args.traversal_data)
        payload[world] = {
            "hubs": sorted(hubs),
            "doors": sorted(doors),
            "candidates": rows,
            "deimos_not_candidates": missed,
        }

        print(f"== {world}: {len(rows)} candidates  hubs={sorted(hubs)}  doors={sorted(doors)}")
        print(f"   {'hops':>4} {'links':>5} {'rooms':>5}  d  {'zone':62} name")
        for r in rows:
            hops = "-" if r["hops"] is None else r["hops"]
            mark = "*" if r["deimos"] else " "
            via = f"  via {' > '.join(z.removeprefix(f'{world}/') for z in r['via'])}" if r["via"] else ""
            print(f"   {hops:>4} {r['links']:>5} {r['rooms']:>5}  {mark}  {r['zone']:62} {r['name']}{via}")
        for m in missed:
            print(f"   deimos lists, not a candidate ({m['reason']}): {m['zone']}  {m['name']}")
        print()

    if args.out:
        args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
