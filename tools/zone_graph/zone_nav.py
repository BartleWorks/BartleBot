"""Decode a zone wad's ``zone.nav``: the graph the quest arrow routes a wizard through one zone.

It is ``zonemap.nav``'s layout (zonemap_nav.py) without the name table, and with the 12 bytes
that are zero there holding each node's position:

LAYOUT (reverse-engineered)
    u16  node_count - 1 (or so: one less than the next field in every zone seen)
    u16  node_count
    u16  unknown (0)
    node_count x 14-byte node records: f32 x, y, z, then a u16 that is mostly the node's index
             (Triton Avenue repeats one, so nothing trusts it)
    u32  edge_count
    edge_count x (u16 a, u16 b) node-index pairs, nearly every one stored both ways

Most nodes sit on a 480-unit grid over the walkable floor, joined to their neighbors (480, or
679 diagonally). The rest of the edges are the zone's links as the arrow sees them: Triton
Avenue's teleport pads, Floating Mountains' ziplines, ropes, boats and mine carts elsewhere,
each a long edge from near where the link is taken to near where it lands. An edge does not
say what it is or which way it may be taken, and it can end a few hundred units from either.
A ladder's or a rope's edge is short across the floor but far too steep to walk.

USAGE
    uv run python tools/zone_graph/zone_nav.py Azteca/AZ_Z10_FloatingMountains
    uv run python tools/zone_graph/zone_nav.py WizardCity/WC_Streets/WC_Triton --longer-than 3000
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
from dataclasses import dataclass

from wad_common import zone_wad

NODE_RECORD_SIZE = 14
# Longer than two grid steps, diagonally: not a walk between neighbors.
LONG_EDGE = 1500.0
# Or steeper than any walk and higher than a stair: a ladder or a rope (Tyrian Gorge's rise
# 1,100-2,100 over 200-460 across; the grid's steepest edges rise about 300 over 679).
CLIMB = 500.0


@dataclass
class ZoneNav:
    nodes: list[tuple[float, float, float]]
    pairs: list[tuple[int, int]]

    def link_edges(self, longer_than: float = LONG_EDGE) -> list[tuple[int, int]]:
        """The edges no walk takes: spanning more than *longer_than* across the floor, or
        climbing more than CLIMB and more steeply than 1:1."""
        out = []
        for a, b in self.pairs:
            (ax, ay, az), (bx, by, bz) = self.nodes[a], self.nodes[b]
            across, rise = math.hypot(bx - ax, by - ay), abs(bz - az)
            if across > longer_than or rise > max(CLIMB, across):
                out.append((a, b))
        return out


def parse(data: bytes) -> ZoneNav:
    _, node_count, _ = struct.unpack_from("<HHH", data, 0)
    off = 6
    nodes = []
    for _ in range(node_count):
        nodes.append(struct.unpack_from("<fff", data, off))
        off += NODE_RECORD_SIZE
    (edge_count,) = struct.unpack_from("<I", data, off)
    off += 4
    if off + 4 * edge_count != len(data):
        raise ValueError(f"{len(data) - off - 4 * edge_count} bytes left over after {edge_count} edges")
    pairs = [struct.unpack_from("<HH", data, off + 4 * i) for i in range(edge_count)]
    bad = [p for p in pairs if max(p) >= node_count]
    if bad:
        raise ValueError(f"{len(bad)} edges reference a node out of range, e.g. {bad[0]}")
    return ZoneNav(nodes, pairs)


def load(wad) -> ZoneNav | None:
    """The zone's nav graph, or None when its wad has none."""
    try:
        return parse(bytes(wad["zone.nav"]))
    except KeyError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("zone")
    ap.add_argument("--longer-than", type=float, default=LONG_EDGE, help="count an edge longer than this as a link")
    args = ap.parse_args()
    nav = load(zone_wad(args.zone))
    if nav is None:
        print(f"{args.zone} has no zone.nav")
        return 1
    print(f"{len(nav.nodes)} nodes, {len(nav.pairs)} edges")
    for a, b in sorted(nav.link_edges(args.longer_than), key=lambda e: -math.dist(nav.nodes[e[0]], nav.nodes[e[1]])):
        pa, pb = nav.nodes[a], nav.nodes[b]
        print(
            f"  {math.dist(pa[:2], pb[:2]):7.0f}  {a:5} ({pa[0]:8.0f}, {pa[1]:8.0f}, {pa[2]:6.0f}) -> {b:5} ({pb[0]:8.0f}, {pb[1]:8.0f}, {pb[2]:6.0f})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
