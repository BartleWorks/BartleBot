"""Score Deimos' hand-built traversal data against the game's own zone graph and object positions.

Checks, for one world:
    zoneMap.txt    every parent;child link exists in zonemap.nav
    gates_list.txt every crossing exists in zonemap.nav, and how far its recorded
                   coordinate is from the nearest tagged object in the source zone
                   (a gate the game places is usually within a few hundred units;
                   Deimos recorded where a player stood, not the gate itself)
    ZONEDOOR       matches the world's spiral-door zone in WorldHubZones.xml

USAGE
    uv run python tools/zone_graph/compare_traversal_data.py
    uv run python tools/zone_graph/compare_traversal_data.py --world Krokotopia
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import zonemap_nav
from wad_common import GAME_DATA, TRAVERSAL_DATA, Deserializer, read_traversal_section, root_wad, text
from zone_data import objects, zone_record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--world", default="WizardCity", help="the WORLD - <name> block to check")
    ap.add_argument("--game-data", type=Path, default=GAME_DATA)
    ap.add_argument("--traversal-data", type=Path, default=TRAVERSAL_DATA)
    args = ap.parse_args()

    graph = zonemap_nav.load(args.game_data)
    deserialize = Deserializer()

    zone_map = read_traversal_section("zoneMap.txt", args.world, args.traversal_data)
    doors = [row[1] for row in zone_map if row[0] == "ZONEDOOR"]
    links = [row for row in zone_map if row[0] != "ZONEDOOR"]
    absent = [(a, b) for a, b in links if not graph.linked(a, b)]
    print(f"== zoneMap.txt: {len(links)} links, {len(absent)} not in zonemap.nav")
    for a, b in absent:
        print(f"   absent: {a} -> {b}")

    hubs = deserialize(bytes(root_wad(args.game_data)["WorldHubZones.xml"]))
    door_zone = next(
        (text(h["m_universeTPZone"]) for h in hubs["m_hubZoneMapping"] if text(h["m_world"]) == args.world), None
    )
    print(f"\n== ZONEDOOR: Deimos {doors}, WorldHubZones.xml {door_zone!r}")

    gates = read_traversal_section("gates_list.txt", args.world, args.traversal_data)
    absent = [g for g in gates if not graph.linked(g[4], g[5])]
    print(f"\n== gates_list.txt: {len(gates)} crossings, {len(absent)} not in zonemap.nav")
    for g in absent:
        print(f"   absent: {g[4]} -> {g[5]}  at ({g[1]}, {g[2]}, {g[3]})")

    print("\n   recorded coordinate vs nearest tagged object in the source zone:")
    cache: dict[str, list] = {}
    for kind, x, y, z, src, dst in (g[:6] for g in gates):
        if src not in cache:
            try:
                cache[src] = objects(zone_record(src, deserialize, args.game_data))
            except (FileNotFoundError, KeyError) as exc:
                cache[src] = []
                print(f"   (no object data for {src}: {exc})")
        if not cache[src]:
            continue
        here = (float(x), float(y), float(z))
        tag, pos = min(cache[src], key=lambda o: math.dist(o[1], here))
        short = lambda zone: zone.rsplit("/", 1)[-1]  # noqa: E731
        print(f"   {short(src):>26} -> {short(dst):<28} {kind:<18} d={math.dist(pos, here):8.1f}  {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
