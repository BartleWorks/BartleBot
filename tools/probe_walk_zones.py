"""Diagnostic: can every zone's arrival points reach each other on the walk planner's grid? (movement anti-cheat)

probe_walk_route.py plans a route in one zone. This asks the question for every zone a bot
might walk: those on the zone map (bartlebot/game/data/zone_nav.json) and those a shipped bot
names. For each it builds the grid, splits the passable floor into connected pieces by the
planner's own step rule, and puts each named arrival point (gamedata.bin's m_locationList)
on its piece. Pieces are then joined by the zone's in-zone links (probe_walk_route.Link: a
teleport volume and the arrival point it lands on), as the planner joins them. A zone whose
arrival points all share one piece, or whose pieces the links join both ways, can be walked
between any two of them; one where they do not either has areas joined by something not yet
read (a ladder, a ride, a quest), or a grid too strict for the real floor. Each such zone gets
a map with every piece in its own color and its links drawn, so which it is can be judged by eye.

No client is needed and nothing in the game is touched. A full run takes some minutes.

OUTCOME (2026-10-27): 455 zones in 288s, none failing to load. Of the 387 with two or more
arrival points, 295 have them all on one piece, 65 split them across pieces and 38 have one
off the grid. The names say most splits are real: ladders, "Tele Up", "Floor_02_to_01", Big
Ben's levels, gauntlet tents; and most off-grid points are cinematic or marker spots. Triton
Avenue was checked closely: its five pieces stay apart with no walls, floor up to 78 degrees
steep, floors merged and every mesh counted as floor, and its triggers.xml names the teleport
volumes that join them (Tele Up, Tele Down, To TempestNexus, High_Ledge, From Kraken Isle), each
landing on the arrival point of the same name; some wait on a quest. So a zone is walked
between its pieces by those links, which the planner does not have yet.

With links (2026-10-27): Triton Avenue's six pair as checked by hand, and four of them were
walked live, each landing within 5 units of the arrival point its name paired it with. Across
every zone, though, volume links join only 2 of the 65 split zones: 295 all on one piece, 2
joined by links, 63 still apart, 38 with a point off the grid. Most splits are joined by
things pressed rather than stepped into (fast-travel stones, ziplines, ladders), whose
destinations the client's files do not hold.

With every link source (2026-10-27; probe_walk_route.zone_links, docs/WALKING.md): volumes
paired by name or by zone.nav, rides' templates, stones' partners, X/X_B pairs, and zone.nav
edges from travel objects. 295 links in 56 zones (189 volumes, 101 objects pressed, 5 rides):
295 all on one piece, 21 joined by links, 44 still apart (135 points, from 299), 38 with a
point off the grid.

HOW TO RUN
    uv run --with numpy python tools/probe_walk_zones.py                 # every zone
    uv run --with numpy python tools/probe_walk_zones.py --zone WizardCity/WC_Streets/WC_Unicorn

Writes logs/walk_route/zones/summary.json (one row per zone) and, for each zone whose
arrival points the walk cannot join, logs/walk_route/zones/<zone>.png (a full run clears the
old maps first).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_walk_route import (  # noqa: E402
    REPO,
    Grid,
    blocking_footprints,
    nearest_node,
    parse_collision,
    pieces,
    walkable_triangles,
    zone_links,
)

sys.path.insert(0, str(Path(__file__).resolve().parent / "zone_graph"))

from wad_common import GAME_DATA, Deserializer, zone_wad  # noqa: E402
from zone_data import locations, zone_record  # noqa: E402

OUT = REPO / "logs" / "walk_route" / "zones"
CELL = 40.0
RADIUS = 50.0
MAX_RISE = 1.0
# An arrival point further than this from any passable floor is off the grid.
OFF_GRID = 200.0
# Snapping an arrival point, a unit of height costs this many of distance (probe_walk_route.Z_RANK).
Z_RANK = 2.0
_ZONE_STRING = re.compile(r'"([A-Z][A-Za-z0-9_]*(?:/[A-Za-z0-9_\-]+)+)"')


def population() -> dict[str, list[str]]:
    """Every zone on the zone map or named by a shipped bot that has a wad, and why it is here."""
    zones: dict[str, set[str]] = {}
    nav = json.loads((REPO / "bartlebot/game/data/zone_nav.json").read_text(encoding="utf-8"))
    for world in nav["worlds"].values():
        for zone in world.get("zones", {}):
            zones.setdefault(zone, set()).add("map")
    for bot in (REPO / "bots").rglob("*.json"):
        for zone in _ZONE_STRING.findall(bot.read_text(encoding="utf-8")):
            zones.setdefault(zone, set()).add("bot")
    return {
        zone: sorted(why)
        for zone, why in sorted(zones.items())
        if (GAME_DATA / (zone.replace("/", "-") + ".wad")).exists()
    }


def place(grid: Grid, passable: np.ndarray, label: np.ndarray, x: float, y: float, z: float) -> int | None:
    """The piece an arrival point stands on: the nearest passable spot, keeping to its storey."""
    ls, js, is_ = np.nonzero(passable)
    if not len(js):
        return None
    j, i = grid.index(x, y)
    d2 = (js - j) ** 2 + (is_ - i) ** 2 + ((grid.levels[ls, js, is_] - z) / grid.cell * Z_RANK) ** 2
    k = int(np.argmin(d2))
    if math.sqrt(d2[k]) * grid.cell > OFF_GRID:
        return None
    return int(label[ls[k], js[k], is_[k]])


_PALETTE = [
    (66, 135, 245),
    (245, 130, 48),
    (60, 180, 75),
    (230, 25, 75),
    (145, 30, 180),
    (70, 240, 240),
    (240, 50, 230),
    (210, 245, 60),
    (0, 128, 128),
    (170, 110, 40),
    (128, 0, 0),
    (0, 0, 128),
]


def draw(path: Path, zone: str, grid: Grid, passable: np.ndarray, label: np.ndarray, points: list, links: list) -> None:
    """Every piece in its own color (the arrival points' pieces first in the palette), grey for
    pieces no arrival point stands on, the arrival points labeled with their piece, and each
    link a yellow line from its volume to where it lands."""
    ranked = []
    for *_, piece in points:
        if piece is not None and piece not in ranked:
            ranked.append(piece)
    color_of = {piece: _PALETTE[k % len(_PALETTE)] for k, piece in enumerate(ranked)}
    top = np.full((grid.ny, grid.nx), -1, dtype=np.int32)
    for layer in range(len(grid.levels)):
        top = np.where(passable[layer], label[layer], top)
    floor = grid.floors >= 1
    img = np.zeros((grid.ny, grid.nx, 3), dtype=np.uint8)
    img[:] = (40, 40, 46)
    img[floor] = (120, 120, 115)
    img[(top >= 0)] = (185, 185, 180)
    for piece, color in color_of.items():
        img[top == piece] = color
    scale = max(1, min(6, 1400 // max(grid.nx, grid.ny)))
    image = Image.fromarray(img[::-1]).resize((grid.nx * scale, grid.ny * scale), Image.NEAREST)
    pen = ImageDraw.Draw(image)

    def px(x: float, y: float) -> tuple[float, float]:
        return (x - grid.x0) / grid.cell * scale, (grid.ny - (y - grid.y0) / grid.cell) * scale

    for link in links:
        (ax, ay), (bx, by) = px(*link.source[:2]), px(*link.target[:2])
        pen.line((ax, ay, bx, by), fill=(255, 220, 0), width=2)
        pen.rectangle((ax - 4, ay - 4, ax + 4, ay + 4), outline=(255, 220, 0), width=2)
        pen.text((ax + 7, ay + 4), link.name.removeprefix("TeleportVol_"), fill=(255, 220, 0))
    for name, (x, y, _), piece in points:
        cx = (x - grid.x0) / grid.cell * scale
        cy = (grid.ny - (y - grid.y0) / grid.cell) * scale
        if piece is None:
            pen.line((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 0, 0), width=2)
            pen.line((cx - 5, cy + 5, cx + 5, cy - 5), fill=(255, 0, 0), width=2)
            text = f"{name} (off the grid)"
        else:
            pen.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 255, 255), outline=(0, 0, 0))
            text = f"{name} [{ranked.index(piece) + 1}]"
        pen.text((cx + 7, cy - 6), text, fill=(255, 255, 255))
    legend = (
        f"{zone}: each colored area is one connected piece, [n] the piece an arrival point is on; "
        "light grey: a piece with no arrival point; dark grey: floor too near a wall or a drop; "
        "yellow: a link, volume (square) to landing; +y is up"
    )
    pen.rectangle((0, 0, 8 * len(legend) // 1.6, 16), fill=(255, 255, 255))
    pen.text((4, 2), legend, fill=(0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def reachable(piece: int, joins: dict[int, set[int]]) -> set[int]:
    """Every piece *piece* reaches by walking and links, itself included."""
    seen, todo = {piece}, [piece]
    while todo:
        for other in joins.get(todo.pop(), ()):
            if other not in seen:
                seen.add(other)
                todo.append(other)
    return seen


def survey(zone: str, why: list[str], deserialize: Deserializer) -> dict:
    row: dict = {"zone": zone, "why": why}
    started = time.perf_counter()
    solids = parse_collision(bytes(zone_wad(zone)["collision.bcd"]))
    tris = walkable_triangles(solids)
    if not len(tris):
        row["problem"] = "no walkable floor"
        return row
    grid = Grid(tris, CELL)
    grid.add_walls(blocking_footprints(solids))
    passable = grid.passable(RADIUS, MAX_RISE)
    label = pieces(grid, passable, MAX_RISE)
    sizes = np.bincount(label[label >= 0])
    try:
        record = zone_record(zone, deserialize)
        named = dict(locations(record))
    except Exception:
        record, named = None, {}
    points = [(name, xyz, place(grid, passable, label, *xyz)) for name, xyz in named.items()]
    on = [p for *_, p in points if p is not None]
    links = zone_links(zone, grid, passable, label, record, named) if record is not None else []
    joins: dict[int, set[int]] = {}
    for link in links:
        here, there = nearest_node(grid, passable, *link.source), nearest_node(grid, passable, *link.target)
        joins.setdefault(int(label[here]), set()).add(int(label[there]))
    reach = {piece: reachable(piece, joins) for piece in set(on)}
    # The piece most arrival points stand on; the rest count as joined when links go there and back.
    main_piece = max(set(on), key=on.count) if on else None
    apart = sorted(
        name
        for name, _, piece in points
        if piece is not None
        and "cinematic" not in name.lower()  # a camera's mark, not somewhere a walk goes
        and not (main_piece in reach[piece] and piece in reach[main_piece])
    )
    row.update(
        layers=len(grid.levels),
        floor_cells=int((grid.floors >= 1).sum()),
        stacked_cells=int((grid.floors >= 2).sum()),
        pieces=int(len(sizes)),
        largest_share=round(float(sizes.max() / sizes.sum()), 3) if len(sizes) else 0.0,
        arrivals=len(points),
        off_grid=sorted(name for name, _, p in points if p is None),
        arrival_pieces=len(set(on)),
        links=[f"{link.kind} {link.name} -> {link.landing}" for link in links],
        apart=apart,
        seconds=round(time.perf_counter() - started, 2),
    )
    if len(points) >= 2 and (apart or row["off_grid"]):
        groups: dict[int, list[str]] = {}
        for name, _, piece in points:
            if piece is not None:
                groups.setdefault(piece, []).append(name)
        row["groups"] = sorted(groups.values(), key=len, reverse=True)
        png = OUT / (zone.replace("/", "-") + ".png")
        draw(png, zone, grid, passable, label, points, links)
        row["map"] = str(png.relative_to(REPO))
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone", action="append", help="survey only this zone; repeatable")
    args = ap.parse_args()

    zones = {z: ["asked"] for z in args.zone} if args.zone else population()
    if not args.zone:
        for old in OUT.glob("*.png"):
            old.unlink()
    deserialize = Deserializer()
    rows = []
    started = time.perf_counter()
    for n, (zone, why) in enumerate(zones.items(), 1):
        try:
            row = survey(zone, why, deserialize)
        except Exception as e:
            row = {"zone": zone, "why": why, "problem": f"{type(e).__name__}: {e}"[:200]}
        rows.append(row)
        flag = (
            row.get("problem")
            or ("SPLIT" if row.get("apart") else "")
            or (f"joined by {len(row['links'])} links" if row.get("arrival_pieces", 1) > 1 else "")
            or ("OFF" if row.get("off_grid") else "")
        )
        print(f"{n:4}/{len(zones)} {zone:70} {row.get('seconds', 0):6.1f}s {flag}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    multi = [r for r in rows if r.get("arrivals", 0) >= 2]
    whole = [r for r in multi if r["arrival_pieces"] == 1 and not r["off_grid"]]
    linked = [r for r in multi if r["arrival_pieces"] > 1 and not r["apart"]]
    split = [r for r in multi if r["apart"]]
    off = [r for r in multi if r["off_grid"]]
    print(
        f"\n{len(rows)} zones in {time.perf_counter() - started:.0f}s; {len(multi)} with two or more arrival points: "
        f"{len(whole)} all on one piece, {len(linked)} joined by links, {len(split)} still apart, "
        f"{len(off)} with a point off the grid; "
        f"{sum('problem' in r for r in rows)} could not be surveyed"
    )
    print(f"wrote {OUT / 'summary.json'} and a map per zone still apart or with a point off the grid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
