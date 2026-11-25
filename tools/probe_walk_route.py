"""Diagnostic: can a zone be walked from its own game files, around its mobs? (movement anti-cheat)

The server now resets a wizard to the last position it acknowledged, so a teleport, a write
of the position, is undone. Moving means walking, and walking a whole game by hand-recorded
waypoints does not scale. This probe asks whether a route can be planned from what every
zone's wad already ships, with no client running and nothing moved in game:

  - collision.bcd   the walkable mesh, and the box/cylinder/sphere solids that wall it off
  - spawnData.xml   every spawner, its template, and the path it walks (m_pathID)
  - pathData.xml    each path's node ids, in order
  - pathNodeData.bin  each node's position
  - gamedata.bin    the named arrival points, used as route ends

A mob is a spawned template with DuelistBehavior, whose m_npcProximity is the trigger radius
the server uses (200 for Unicorn Way's ghosts; tools/probe_aggro_radius.py measured 270-390
from the wizard's side, lag and body radii included). Its patrol is its path's nodes in order.
The danger cost grows toward every patrol line, so the planner prefers the zone's edges
without anyone marking them.

Each route is planned twice on the same grid: shortest, and with the danger cost. Both are
drawn on one PNG under logs/walk_route/, with +y up.

OUTCOME (Unicorn Way, 2026-10-27): yes. The zone parses and plans in about 5s, all one level.
Ten mob spawners patrol (ghosts, dark sprites, skeletons). probe_aggro_radius's run01, which
was recorded here, puts 561 live mob samples a median 23 units from these patrol lines (p90
85, max 370), and 47 of them on the last-to-first segment, so patrols loop and the wad's paths
are where mobs walk. With the danger cost, every route keeps at least 250 units outside every
trigger radius by following the street edges, for 4-20% more walking:

    Commons gate -> Hedge Maze   ~75s shortest (some 16,000 units near a patrol)  ~80s avoiding (none)
    Commons gate -> Tower 2      ~51s                (~5,000)                      ~55s          (none)
    Commons gate -> T1           ~34s                (1,335)                       ~36s          (none)

(the avoiding times are the straightened routes, after the cylinder fix below.) The routes
were checked in game, and probe_walk_input.py walked the T1 one end to end by keys alone.

Straightening (smooth) cuts a route's 100-260 grid turns to 25-42 without coming any nearer a
patrol. A cylinder stores (length, radius); Deimos reads them the other way round, which put
every tree a wizard walks into nowhere on the grid. Open: the cost is static, ignoring where
on its loop each mob is now; other probe_aggro_radius CSVs are from other zones, so pass
run01 alone.

Floors stack (Housing_FarmHouse/Interior has three, at z 2300, 3100 and 3900), so the grid
keeps every floor in a cell and a step goes to the neighbor's floor at about the same height;
that is what joins storeys by their stairs and by nothing else. The map draws one panel per
storey. probe_walk_input.py walked the planned route down two flights to the basement.

The planner is core's since 2026-11-24 (bartleby.runtime.movement: collision, zone_grid,
patrols, zone_links, route; core's docs/WALKING.md), and so is the reader that turns a zone's
wad into its ZoneFiles (core's tools/zone_files.py, from the checkout beside this one). This
probe plans from the wad with them and draws the result; a run plans from core's shipped
maps, which tools/build_zone_maps.py there checks against the wad (--compare). Core added the zone's exits: a route keeps out
of every volume that teleports and is not one of the zone's links, unless it starts or ends in
one. --no-exits plans as this probe did before, to compare a route with one planned then.

HOW TO RUN
    uv run python tools/probe_walk_route.py
    uv run python tools/probe_walk_route.py --from "Hub Street1 Entrance" --to "Hedgemaze Exit" --to "-10515,2162"
    uv run python tools/probe_walk_route.py --observed tools/probe_aggro_radius.20260927-run01.local.csv

With core taken by path, editable, add --no-sync to uv run, or uv swaps it for the pin.

--observed overlays mob positions logged by tools/probe_aggro_radius.py and reports how far
they strayed from the patrol lines, which is the check that the lines are where mobs walk.
Those files hold no wizard names or ids; this probe reads only their mob rows.
"""

from __future__ import annotations

import argparse
import csv
import functools
import glob
import sys
import time
from pathlib import Path

import numpy as np
from bartleby.runtime.movement.patrols import (
    HARD,
    HARD_WEIGHT,
    SOFT,
    WEIGHT,
    Patrol,
    distance_to_segments,
    segments,
)
from bartleby.runtime.movement.route import WIZARD_SPEED, Route, ZoneFiles, ZonePlanner
from bartleby.runtime.movement.route import build_planner as plan_zone
from bartleby.runtime.movement.zone_grid import CELL, CLEARANCE, LEVEL_GAP, MAX_RISE, Grid, Node
from bartleby.runtime.movement.zone_links import PlacedObject
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent / "zone_graph"))

from wad_common import Deserializer, reader, wad_entry  # noqa: E402, F401  (wad_entry for probe_walk_zones)
from zone_files import ZoneReader  # noqa: E402

__all__ = [
    "REPO",
    "WIZARD_SPEED",
    "Deserializer",
    "Patrol",
    "Planner",
    "build_planner",
    "distance_to_segments",
    "mob_patrols_of",
    "render",
    "resolve_point",
]

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ZONE = "WizardCity/WC_Streets/WC_Unicorn"
DEFAULT_FROM = "Hub Street1 Entrance"
DEFAULT_TO = ("Street1 Hedgemaze Exit", "Street1 Tower2 Exit", "WC-UW T1 Exit")

#: The probes' name for core's planner.
Planner = ZonePlanner
# Kept for probe_mob_paths.py, which draws patrol lines.
_segments = segments


# --- reading a zone's wad into core's ZoneFiles ----------------------------------------------
#
# The reader is core's (its tools/zone_files.py, for the zone maps tools/build_zone_maps.py
# builds there); these are the probes' names for it, on this install and the newest dump.


@functools.cache
def _reader() -> ZoneReader:
    return reader()


def mob_patrols_of(
    zone: str, deserialize: Deserializer | None = None, starts: tuple[int, ...] = (0, 1)
) -> list[Patrol]:
    """The zone's patrols, read from its spawn files and the game's templates by core's reader."""
    return _reader().mob_patrols(zone, starts)


def placed_objects(record) -> list[PlacedObject]:
    """The zone's placed objects whose templates can be interacted with."""
    return _reader().placed_objects(record)


def zone_files(zone: str, deserialize: Deserializer | None = None, *, ignore_cylinders: bool = False) -> ZoneFiles:
    """Everything core's planner is built from, read out of the zone's wad. *ignore_cylinders*
    leaves every cylinder off the grid, as reading them wrong once did, so a route runs through
    the trees: a way to make a walk stall, for testing its recovery."""
    return _reader().zone_files(zone, ignore_cylinders=ignore_cylinders)


def build_planner(
    zone: str,
    *,
    cell: float = CELL,
    radius: float = CLEARANCE,
    hard: float = HARD,
    soft: float = SOFT,
    weight: float = WEIGHT,
    hard_weight: float = HARD_WEIGHT,
    max_rise: float = MAX_RISE,
    ignore_cylinders: bool = False,
    exits: bool = True,
) -> Planner:
    """Core's planner for *zone*, from its wad. Without *exits*, routes ignore the zone's exits,
    as the probe planned before core had them: for comparing a route with one planned then."""
    started = time.perf_counter()
    files = zone_files(zone, ignore_cylinders=ignore_cylinders)
    planner = plan_zone(
        files,
        cell=cell,
        radius=radius,
        hard=hard,
        soft=soft,
        weight=weight,
        hard_weight=hard_weight,
        max_rise=max_rise,
    )
    if not exits:
        planner.exits = []
    print(summary(planner, files, time.perf_counter() - started))
    return planner


def summary(planner: Planner, files: ZoneFiles, seconds: float) -> str:
    grid, floors = planner.grid, planner.grid.floors
    lines = [
        f"{planner.zone}: {len(files.solids)} collision shapes, {int((floors >= 1).sum())} floor cells, "
        f"{int((floors >= 2).sum())} of them with more than one floor (up to {len(grid.levels)}), "
        f"grid {grid.nx}x{grid.ny} at {grid.cell:.0f}, {int(planner.passable.sum())} passable floor spots  [{seconds:.1f}s]"
    ]
    lines += [
        f"  patrol {p.spawner!r}: {p.template} x{p.max_spawns}, {len(p.points)} nodes, trigger radius {p.proximity:.0f}"
        for p in planner.patrols
    ]
    lines += [f"  {link.kind} link {link.name!r} -> {link.landing!r}" for link in planner.links]
    lines += [f"  exit {e.name!r}: {e.shape} reaching {e.reach:.0f}" for e in planner.exits]
    return "\n".join(lines)


# --- reporting -----------------------------------------------------------------------------


def resolve_point(spec: str, named: dict[str, tuple[float, float, float]]) -> tuple[str, tuple[float, ...]]:
    """An arrival point by name (any unique substring), or x,y or x,y,z."""
    try:
        values = tuple(float(v) for v in spec.split(","))
    except ValueError:
        values = ()
    if len(values) in (2, 3):
        return spec, values
    matches = [n for n in named if spec.lower() in n.lower()]
    if len(matches) != 1:
        raise SystemExit(f"{spec!r} matches {matches or 'no arrival point'}; arrival points: {sorted(named)}")
    return matches[0], named[matches[0]]


def route_stats(
    grid: Grid, path: list[Node], danger_distance: np.ndarray, hard: float, jumps: set[int] | None = None
) -> dict:
    """*jumps* are the steps that are links, not walked; by default, a cell-by-cell path's
    steps between cells that are not neighbors."""
    pts = np.array([grid.world(*n) for n in path])
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    if jumps is None:
        jumps = {k for k, (a, b) in enumerate(zip(path, path[1:])) if abs(b[1] - a[1]) > 1 or abs(b[2] - a[2]) > 1}
    steps[sorted(jumps)] = 0.0
    near = np.array([danger_distance[j, i] for _, j, i in path])
    inside = near[1:] < hard
    turns = 0
    for k in range(1, len(path) - 1):
        a = (path[k][1] - path[k - 1][1], path[k][2] - path[k - 1][2])
        b = (path[k + 1][1] - path[k][1], path[k + 1][2] - path[k][2])
        turns += a != b
    return {
        "length": float(steps.sum()),
        "seconds": float(steps.sum() / WIZARD_SPEED),
        "closest": float(near.min()),
        "inside_hard": float(steps[inside].sum()),
        "turns": turns,
        "climb": float(pts[:, 2].max() - pts[:, 2].min()),
    }


def observed_fit(paths: list[str], patrols: list[Patrol], grid: Grid) -> tuple[np.ndarray, str]:
    rows = []
    for pattern in paths:
        for name in glob.glob(pattern):
            with open(name, newline="", encoding="utf-8") as f:
                rows += [(float(r["x"]), float(r["y"])) for r in csv.DictReader(f) if r["kind"] == "mob"]
    if not rows:
        return np.empty((0, 2)), "observed: no mob rows read"
    pts = np.array(rows)
    # The probe's CSVs do not record a zone, so a sample counts as this zone's only when it
    # stands on this zone's floor; a bounding box lets in other zones sharing the coordinates.
    j = ((pts[:, 1] - grid.y0) / grid.cell).astype(int)
    i = ((pts[:, 0] - grid.x0) / grid.cell).astype(int)
    in_box = (j >= 0) & (j < grid.ny) & (i >= 0) & (i < grid.nx)
    on_floor = np.zeros(len(pts), dtype=bool)
    on_floor[in_box] = ~np.isnan(grid.top[j[in_box], i[in_box]])
    pts = pts[on_floor]
    if not len(pts):
        return pts, "observed: none of the mob rows fall inside this zone"
    open_segs = np.concatenate([segments(p, closed=False) for p in patrols])
    closing = np.concatenate([segments(p, closed=True)[-1:] for p in patrols if len(p.points) > 2])
    d_open = distance_to_segments(pts[:, 0], pts[:, 1], open_segs)
    d_close = distance_to_segments(pts[:, 0], pts[:, 1], closing)
    q = np.percentile(d_open, [50, 90, 99, 100])
    lines = [
        f"observed: {len(pts)} mob samples in the zone, distance from the nearest patrol line "
        f"p50 {q[0]:.0f}  p90 {q[1]:.0f}  p99 {q[2]:.0f}  max {q[3]:.0f}",
        f"          {int(((d_close + 100) < d_open).sum())} samples sit on a last-to-first segment and off the "
        f"open line (evidence that patrols loop rather than turn back)",
    ]
    return pts, "\n".join(lines)


def render(
    out: Path,
    planner: Planner,
    hard: float,
    routes: list[tuple],
    observed: np.ndarray,
    observed_label: str = "observed mobs",
) -> None:
    """One panel per storey, side by side. A route is drawn bold on the storey it walks and
    faint on the others, so a stair shows as the route changing panels."""
    storeys = _storeys(planner.grid)
    panels = [_render_layer(planner, storeys, k, hard, routes, observed) for k in range(len(storeys))]
    legend = [
        "blue: shortest   green: avoiding patrols   black with yellow dots: avoiding, straightened to its turns",
        "dark red: patrol paths (thin = last node back to first)",
        f"red: within {hard:.0f} of a patrol's trigger radius   pink: within {planner.soft:.0f}   "
        f"purple: {observed_label}",
        "grey: floor too close to a wall or a drop   black: wall   +y is up   faint lines: the route on another storey",
        "orange rings: the zone's exits, which a route keeps out of unless it starts or ends in one",
    ]
    gap = 12
    sheet = Image.new(
        "RGB", (sum(p.width for p in panels) + gap * (len(panels) - 1), max(p.height for p in panels)), (255, 255, 255)
    )
    x = 0
    for panel in panels:
        sheet.paste(panel, (x, 0))
        x += panel.width + gap
    draw = ImageDraw.Draw(sheet)
    draw.rectangle((0, 0, 760, 16 * len(legend) + 8), fill=(255, 255, 255))
    for k, line in enumerate(legend):
        draw.text((6, 4 + 16 * k), line, fill=(0, 0, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def _storeys(grid: Grid) -> list[float]:
    """The heights most of the floor sits at, lowest first: a storey is a 50-unit height band
    holding at least 5% of all floor. A stair's cells fall between storeys and hold few."""
    z = grid.levels[~np.isnan(grid.levels)]
    if len(grid.levels) == 1:
        return [float(np.median(z))]  # nowhere has two floors: one storey, however uneven the ground
    bins = np.round(z / 50) * 50
    values, counts = np.unique(bins, return_counts=True)
    storeys: list[float] = []
    for value in values[counts >= 0.05 * len(z)]:
        if not storeys or value - storeys[-1] > LEVEL_GAP:
            storeys.append(float(value))
    return storeys or [float(np.median(z))]


def _storey_of(storeys: list[float], z: float) -> int:
    return min(range(len(storeys)), key=lambda k: abs(storeys[k] - z))


def _render_layer(
    planner: Planner, storeys: list[float], storey: int, hard: float, routes: list[tuple], observed: np.ndarray
) -> Image.Image:
    grid, danger_distance, soft = planner.grid, planner.clearance, planner.soft
    # Each cell's floor nearest this storey's height, if that is nearer this storey than any other.
    with np.errstate(invalid="ignore"):
        apart = np.where(np.isnan(grid.levels), np.inf, np.abs(grid.levels - storeys[storey]))
    pick = apart.argmin(axis=0)[None]
    z = np.take_along_axis(grid.levels, pick, axis=0)[0]
    floor = ~np.isnan(z)
    floor[floor] = [_storey_of(storeys, h) == storey for h in z[floor]]
    passable = np.take_along_axis(planner.passable, pick, axis=0)[0]
    walls = np.take_along_axis(grid.walls, pick, axis=0)[0] & floor
    # Small grids (a house) are drawn larger, so a panel is never a postage stamp.
    scale = max(2, min(8, 900 // max(grid.nx, grid.ny)))
    img = np.zeros((grid.ny, grid.nx, 3), dtype=np.uint8)
    img[:] = (40, 40, 46)
    img[floor] = (205, 205, 195)
    img[floor & ~passable] = (150, 150, 140)
    img[walls] = (25, 25, 25)
    near = floor & passable & (danger_distance < soft)
    t = np.clip((soft - danger_distance) / (soft - hard), 0, 1)
    tint = np.array([235, 120, 110])[None, :] * t[near][:, None] + img[near].astype(float) * (1 - t[near][:, None])
    img[near] = tint.astype(np.uint8)
    img[floor & passable & (danger_distance < hard)] = (205, 60, 55)

    image = Image.fromarray(img[::-1]).resize((grid.nx * scale, grid.ny * scale), Image.NEAREST)
    draw = ImageDraw.Draw(image)

    def px(x: float, y: float) -> tuple[float, float]:
        return (x - grid.x0) / grid.cell * scale, (grid.ny - (y - grid.y0) / grid.cell) * scale

    for x, y in observed:
        cx, cy = px(x, y)
        draw.ellipse((cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5), fill=(120, 40, 160))
    for p in planner.patrols:
        line = [px(x, y) for x, y, _ in p.points]
        if len(line) > 1:
            draw.line(line, fill=(120, 0, 0), width=2)
            draw.line([line[-1], line[0]], fill=(120, 0, 0), width=1)
        draw.text((line[0][0] + 4, line[0][1] - 12), f"{p.spawner} ({p.template})", fill=(90, 0, 0))

    def on_storey(n: Node) -> bool:
        return _storey_of(storeys, grid.levels[n]) == storey

    for exit in planner.exits:
        if _storey_of(storeys, exit.center[2]) == storey:
            cx, cy = px(*exit.center[:2])
            r = exit.reach / grid.cell * scale
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(230, 120, 0), width=2)

    def path_line(path: Route | list[Node], color: tuple[int, int, int], width: int) -> None:
        path = path.nodes if isinstance(path, Route) else path
        for a, b in zip(path, path[1:]):
            here = on_storey(a) or on_storey(b)
            draw.line(
                [px(*grid.world(*a)[:2]), px(*grid.world(*b)[:2])],
                fill=color if here else (170, 170, 170),
                width=width if here else 1,
            )

    colors = {"shortest": (40, 90, 220), "avoiding": (20, 150, 60)}
    for _goal, shortest, avoiding, straight in routes:
        for kind, path in (("shortest", shortest), ("avoiding", avoiding)):
            if path:
                path_line(path, colors[kind], 3)
        if straight:
            path_line(straight, (0, 0, 0), 1)
            for n in straight:
                if on_storey(n):
                    cx, cy = px(*grid.world(*n)[:2])
                    draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), outline=(0, 0, 0), fill=(255, 220, 0))
    for name, (x, y, pz) in planner.named.items():
        if _storey_of(storeys, pz) != storey:
            continue
        cx, cy = px(x, y)
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(30, 30, 160))
        draw.text((cx + 6, cy + 2), name, fill=(20, 20, 120))
    if floor.any():
        caption = f"storey {storey + 1} of {len(storeys)}: floor around z {storeys[storey]:.0f}"
        draw.rectangle((0, image.height - 18, 8 * len(caption) + 8, image.height), fill=(255, 255, 255))
        draw.text((6, image.height - 15), caption, fill=(0, 0, 0))
    return image


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone", default=DEFAULT_ZONE)
    ap.add_argument("--from", dest="start", default=DEFAULT_FROM, help="arrival point name (substring) or x,y")
    ap.add_argument("--to", action="append", help="arrival point name (substring) or x,y; repeatable")
    ap.add_argument("--cell", type=float, default=40.0, help="grid cell size, world units")
    ap.add_argument(
        "--radius", type=float, default=50.0, help="player clearance from walls (49 measured against a tree)"
    )
    ap.add_argument(
        "--hard",
        type=float,
        default=250.0,
        help="clearance beyond a patrol's trigger radius below which a step costs --hard-weight",
    )
    ap.add_argument(
        "--soft", type=float, default=1300.0, help="clearance beyond a patrol's trigger radius where the cost starts"
    )
    ap.add_argument("--weight", type=float, default=8.0, help="extra cost per unit at --hard, falling to 0 at --soft")
    ap.add_argument("--hard-weight", type=float, default=50.0, help="extra cost per unit inside --hard")
    ap.add_argument("--max-rise", type=float, default=1.0, help="height change allowed per unit walked")
    ap.add_argument("--observed", action="append", default=[], help="probe_aggro_radius CSV glob; repeatable")
    ap.add_argument("--out", type=Path, help="PNG path (default logs/walk_route/<zone>.png)")
    ap.add_argument(
        "--no-exits", action="store_true", help="let routes cross the zone's exits, as before core had them"
    )
    args = ap.parse_args()

    started = time.perf_counter()
    planner = build_planner(
        args.zone,
        cell=args.cell,
        radius=args.radius,
        hard=args.hard,
        soft=args.soft,
        weight=args.weight,
        hard_weight=args.hard_weight,
        max_rise=args.max_rise,
        exits=not args.no_exits,
    )
    grid, named = planner.grid, planner.named

    start_name, start_xy = resolve_point(args.start, named)
    routes = []
    for spec in args.to or DEFAULT_TO:
        goal_name, goal_xy = resolve_point(spec, named)
        t0 = time.perf_counter()
        shortest_route = planner.route(start_xy, goal_xy, avoid=False)
        avoiding_route = planner.route(start_xy, goal_xy)
        shortest = shortest_route.nodes if shortest_route else None
        avoiding = avoiding_route.nodes if avoiding_route else None
        straight, straight_jumps = planner.turns_and_jumps(avoiding_route) if avoiding_route else (None, set())
        print(f"\n{start_name} -> {goal_name}  [{time.perf_counter() - t0:.1f}s]")
        for kind, path in (("shortest", shortest), ("avoiding", avoiding), ("straight", straight)):
            if path is None:
                print(f"  {kind:9} unreachable")
                continue
            s = route_stats(grid, path, planner.clearance, args.hard, straight_jumps if kind == "straight" else None)
            print(
                f"  {kind:9} {s['length']:7.0f} units  {s['seconds']:5.1f}s on foot  "
                f"least clearance {s['closest']:5.0f}  under {args.hard:.0f} for {s['inside_hard']:5.0f} units  "
                f"{s['turns']} turns  {s['climb']:.0f} up or down"
            )
        routes.append((goal_name, shortest, avoiding, straight))

    observed, report = observed_fit(args.observed, planner.patrols, grid) if args.observed else (np.empty((0, 2)), "")
    if report:
        print("\n" + report)
    out = args.out or REPO / "logs" / "walk_route" / f"{args.zone.replace('/', '-')}.png"
    render(out, planner, args.hard, routes, observed)
    print(f"\nwrote {out}  [{time.perf_counter() - started:.1f}s total]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
