"""Print a zone wad's ``zone.nav``: the graph the quest arrow routes a wizard through one zone.

The parser is core's (``bartleby.runtime.movement.zone_links.parse_zone_nav``), whose comment
has the layout; this is its command line, and ``load``, which reads it out of a zone's wad.
Most nodes sit on a 480-unit grid over the walkable floor; the rest of the edges are the zone's
links as the arrow sees them, each a long edge from near where the link is taken to near where
it lands.

USAGE
    uv run python tools/zone_graph/zone_nav.py Azteca/AZ_Z10_FloatingMountains
    uv run python tools/zone_graph/zone_nav.py WizardCity/WC_Streets/WC_Triton --longer-than 3000
"""

from __future__ import annotations

import argparse
import math
import sys

from bartleby.runtime.movement.zone_links import LONG_EDGE, ZoneNav, parse_zone_nav
from wad_common import zone_wad

__all__ = ["LONG_EDGE", "ZoneNav", "load", "parse"]
parse = parse_zone_nav


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
