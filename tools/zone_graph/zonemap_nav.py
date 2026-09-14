"""Decode Root.wad's ``zonemap.nav``, the game's graph of which zones connect to which.

LAYOUT (reverse-engineered; see CLAUDE.md in this folder for what is still unknown)
    u16  node_count
    u16  node_count again
    u16  unknown (0)
    node_count x 14-byte node records: 12 bytes (zero in every record seen), then u16 node index (== position)
    u32  edge_count
    edge_count x (u16 a, u16 b) zone-index pairs
    u32  name_count (== node_count)
    name_count x (u32 length, bytes) zone paths, e.g. "WizardCity/WC_Hub"; index = position

USAGE
    uv run python tools/zone_graph/zonemap_nav.py stats
    uv run python tools/zone_graph/zonemap_nav.py find "WizardCity/WC_Streets"
    uv run python tools/zone_graph/zonemap_nav.py neighbours WizardCity/WC_Hub
    uv run python tools/zone_graph/zonemap_nav.py export --out zonemap.local.json
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from wad_common import GAME_DATA, root_wad

NODE_RECORD_SIZE = 14


@dataclass
class ZoneGraph:
    names: list[str]
    # Raw (a, b) pairs as stored. Which end is the source has not been established;
    # every link checked so far appears in both orders, so the tools treat it as undirected.
    pairs: list[tuple[int, int]]
    unknown_node_bytes: int = 0  # node records whose 12 leading bytes are not all zero
    trailer: int = 0  # the u32 between the edge list and the name table
    _adjacency: dict[str, set[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        adj: dict[str, set[str]] = defaultdict(set)
        for a, b in self.pairs:
            adj[self.names[a]].add(self.names[b])
            adj[self.names[b]].add(self.names[a])
        self._adjacency = adj

    def neighbours(self, zone: str) -> set[str]:
        return set(self._adjacency.get(zone, ()))

    def linked(self, a: str, b: str) -> bool:
        return b in self._adjacency.get(a, ())


def parse(data: bytes) -> ZoneGraph:
    node_count, node_count_2, _ = struct.unpack_from("<HHH", data, 0)
    if node_count != node_count_2:
        raise ValueError(f"header node counts disagree: {node_count} vs {node_count_2}")

    off = 6
    unknown_node_bytes = 0
    for i in range(node_count):
        record = data[off : off + NODE_RECORD_SIZE]
        (index,) = struct.unpack_from("<H", record, 12)
        if index != i:
            raise ValueError(f"node record {i} carries index {index}")
        if any(record[:12]):
            unknown_node_bytes += 1
        off += NODE_RECORD_SIZE

    (edge_count,) = struct.unpack_from("<I", data, off)
    off += 4
    pairs = [struct.unpack_from("<HH", data, off + 4 * i) for i in range(edge_count)]
    off += 4 * edge_count
    (trailer,) = struct.unpack_from("<I", data, off)
    off += 4

    names: list[str] = []
    while off < len(data):
        (length,) = struct.unpack_from("<I", data, off)
        names.append(data[off + 4 : off + 4 + length].decode("utf-8"))
        off += 4 + length
    if len(names) != node_count:
        raise ValueError(f"name table has {len(names)} entries, header says {node_count}")
    bad = [p for p in pairs if max(p) >= node_count]
    if bad:
        raise ValueError(f"{len(bad)} edges reference a zone index out of range, e.g. {bad[0]}")

    return ZoneGraph(names, pairs, unknown_node_bytes, trailer)


def load(game_data: Path = GAME_DATA) -> ZoneGraph:
    return parse(bytes(root_wad(game_data)["zonemap.nav"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-data", type=Path, default=GAME_DATA, help="the Wizard101 Data/GameData directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", help="counts, and how much of the layout is still unexplained")
    p_find = sub.add_parser("find", help="zone paths matching a regex")
    p_find.add_argument("pattern")
    p_nb = sub.add_parser("neighbours", help="zones linked to one zone")
    p_nb.add_argument("zone")
    p_ex = sub.add_parser("export", help="write names and edges as JSON")
    p_ex.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    graph = load(args.game_data)

    if args.cmd == "stats":
        worlds = defaultdict(int)
        for name in graph.names:
            worlds[name.split("/")[0]] += 1
        linked = sum(1 for n in graph.names if graph.neighbours(n))
        print(f"zones: {len(graph.names)} ({linked} with at least one link)")
        print(f"edge pairs: {len(graph.pairs)}")
        print(f"node records with non-zero unknown bytes: {graph.unknown_node_bytes}")
        print(f"name-table count: {graph.trailer} (matches node count: {graph.trailer == len(graph.names)})")
        print("zones per top-level folder:")
        for world, count in sorted(worlds.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5}  {world}")
    elif args.cmd == "find":
        rx = re.compile(args.pattern, re.IGNORECASE)
        for name in graph.names:
            if rx.search(name):
                print(name)
    elif args.cmd == "neighbours":
        if args.zone not in graph.names:
            print(f"{args.zone!r} is not in zonemap.nav; try `find`")
            return 1
        for name in sorted(graph.neighbours(args.zone)):
            print(name)
    elif args.cmd == "export":
        payload = {"names": graph.names, "pairs": graph.pairs}
        args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
