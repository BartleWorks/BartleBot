"""Diagnostic: where does a walk the game allows leave the planner's floor? (#322)

The planner's grid (core's movement/zone_grid.py) splits some zones into pieces no walk joins,
where a wizard walks across in the game. This finds where, and why. It records a walk you make
by hand, then lays the track over the grid that zone's planner builds: which piece each stretch
was on, and, wherever the wizard stood on a spot the grid refuses, what refuses it:

  no floor   no floor triangle at the wizard's height there (a box top, or a mesh not marked
             walkable, is not floor to the grid)
  wall       floor walled by a solid's footprint; the solids that wall it are named
  edge       floor kept clear of a wall or a drop, CLEARANCE wide
  step       passable, but the step from the last spot rises or drops more than a walk may

A jump of more than JUMP units between two samples is a link the wizard took (a stone, a
teleport volume) and is shown as one, not as a step.

It writes nothing to the game: it only reads the zone and position. You walk; it watches.

HOW TO RUN
    1. Close BartleBot (two programs hooking one client clash). Log a wizard in, standing where
       the walk starts.
    2. uv run --no-sync python tools/probe_walk_track.py
    3. Walk the way you know works. Ctrl+C stops it and writes the CSV.
    4. Lay it over the grid (no game needed; seconds to build the zone):

         uv run --no-sync python tools/probe_walk_track.py --analyze tools/probe_walk_track.<time>.local.csv

Each run writes tools/probe_walk_track.<time>.local.csv (gitignored); the analysis writes
logs/walk_route/track/<zone>.png beside a printed account.

Wizard names and ids are never read or printed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import io
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

SAMPLE_INTERVAL = 0.2
#: Moving further than this between two samples is a link taken, not a walk.
JUMP = 300.0
#: A floor within this of the wizard's height is the one it stands on.
FLOOR_REACH = 60.0
CSV_FIELDS = ["t", "zone", "x", "y", "z"]
OUT = Path(__file__).resolve().parent.parent / "logs" / "walk_route" / "track"


# --- recording ---------------------------------------------------------------------------------


async def record(client, writer) -> int:
    started = time.monotonic()
    count = 0
    while True:
        if await client.is_loading():
            await asyncio.sleep(SAMPLE_INTERVAL)
            continue
        zone = await client.zone_name()
        pos = await client.body.position()
        t = time.monotonic() - started
        writer.writerow([f"{t:.2f}", zone, f"{pos.x:.1f}", f"{pos.y:.1f}", f"{pos.z:.1f}"])
        count += 1
        print(f"\r{zone}  ({pos.x:8.0f}, {pos.y:8.0f}, {pos.z:6.0f})  {count} samples", end="", flush=True)
        await asyncio.sleep(SAMPLE_INTERVAL)


async def run_record(args: argparse.Namespace) -> int:
    from wizwalker.extensions.wizsprinter import WizSprinter  # noqa: PLC0415

    csv_path = Path(__file__).with_name(f"probe_walk_track.{time.strftime('%Y%m%d-%H%M%S')}.local.csv")
    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= args.client <= len(clients):
        print(f"No client {args.client}; have {len(clients)}.")
        return 2
    client = clients[args.client - 1]
    try:
        await client.activate_hooks()
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)
            try:
                await record(client, writer)
            except asyncio.CancelledError:
                print("\n\nStopped by Ctrl+C.")
    finally:
        for c in clients:
            with contextlib.suppress(Exception):
                await c.close()
    print(f"Track written to {csv_path}")
    print(f"Analyze it: uv run --no-sync python tools/probe_walk_track.py --analyze {csv_path}")
    return 0


# --- analysis ----------------------------------------------------------------------------------


def read_track(path: Path) -> list[tuple[float, str, tuple[float, float, float]]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [(float(r["t"]), r["zone"], (float(r["x"]), float(r["y"]), float(r["z"]))) for r in csv.DictReader(f)]


def classify(grid, walls_ok, passable, label, at) -> tuple[str, int | None, tuple[int, int, int] | None]:
    """(kind, piece, node) for the wizard standing at *at*: kind is "piece", "no floor", "wall"
    or "edge"; the node is the floor it stands on, when there is one."""
    j, i = grid.index(at[0], at[1])
    if not (0 <= j < grid.ny and 0 <= i < grid.nx):
        return "no floor", None, None
    heights = grid.levels[:, j, i]
    with np.errstate(invalid="ignore"):
        gaps = np.abs(heights - at[2])
    if np.all(np.isnan(gaps)) or np.nanmin(gaps) > FLOOR_REACH:
        return "no floor", None, None
    layer = int(np.nanargmin(gaps))
    node = (layer, j, i)
    if not walls_ok[node]:
        return "wall", None, node
    if not passable[node]:
        return "edge", None, node
    return "piece", int(label[node]), node


def walling_solids(solids, grid, node) -> list[str]:
    """The solids whose footprints wall *node*, as the grid's add_walls decides it."""
    from bartleby.runtime.movement.collision import (  # noqa: PLC0415
        Z_BAND_DOWN,
        Z_BAND_UP,
        Z_BOX_TOL,
        Z_EMBED_TOL,
        blocking_footprints,
    )

    layer, j, i = node
    x, y, z = grid.world(layer, j, i)
    out = []
    for s in solids:
        for f in blocking_footprints([s]):
            if f.hull is None:
                inside = math.hypot(x - f.center[0], y - f.center[1]) <= f.radius
            else:
                h = f.hull
                inside = all(
                    (h[(k + 1) % len(h)][0] - h[k][0]) * (y - h[k][1])
                    - (h[(k + 1) % len(h)][1] - h[k][1]) * (x - h[k][0])
                    >= 0
                    for k in range(len(h))
                )
            embedded = f.zlo <= z and (f.zhi - z) >= (Z_BOX_TOL if f.firm else Z_EMBED_TOL)
            overhead = z < f.zlo and (f.zlo - z) <= Z_BAND_UP
            if inside and z <= f.zhi + Z_BAND_DOWN and (embedded or overhead):
                kind = {0: "box", 2: "sphere", 3: "cylinder"}.get(s.proxy, f"proxy {s.proxy}")
                where = tuple(round(c) for c in s.location)
                out.append(f"{kind} at {where}, z {f.zlo:.0f}..{f.zhi:.0f}, params {tuple(round(v) for v in s.params)}")
    return out


def analyze_zone(zone: str, samples: list[tuple[float, tuple[float, float, float]]]) -> None:
    from bartleby.runtime.movement.zone_grid import MAX_RISE, STEP_TOL, pieces  # noqa: PLC0415
    from PIL import Image, ImageDraw  # noqa: PLC0415
    from probe_walk_route import build_planner, zone_files  # noqa: PLC0415

    print(f"\n== {zone}: {len(samples)} samples ==")
    with contextlib.redirect_stdout(io.StringIO()):
        planner = build_planner(zone)
    files = zone_files(zone)
    grid, passable = planner.grid, planner.passable
    walls_ok = ~np.isnan(grid.levels) & ~grid.walls
    label = pieces(grid, passable, planner.max_rise)

    rows = []  # (t, at, kind, piece, node, note)
    last_node = None
    last_at = None
    for t, at in samples:
        kind, piece, node = classify(grid, walls_ok, passable, label, at)
        note = ""
        if last_at is not None and math.dist(at[:2], last_at[:2]) > JUMP:
            note = f"jump of {math.dist(at, last_at):.0f}: a link"
        elif kind == "piece" and last_node is not None and node != last_node:
            dist = math.hypot((node[1] - last_node[1]) * grid.cell, (node[2] - last_node[2]) * grid.cell)
            rise = abs(grid.levels[node] - grid.levels[last_node])
            if rise > MAX_RISE * dist + STEP_TOL:
                kind, note = "step", f"rises {rise:.0f} over {dist:.0f}"
        rows.append((t, at, kind, piece, node, note))
        last_at = at
        if node is not None:
            last_node = node

    # One line per stretch of the same kind and piece.
    stretches = []
    for row in rows:
        key = (row[2], row[3])
        if stretches and stretches[-1][0] == key and not row[5]:
            stretches[-1][2] = row
        else:
            stretches.append([key, row, row])
    named_walls: set[str] = set()
    for (kind, piece), first, last in stretches:
        span = f"{first[0]:7.1f}-{last[0]:7.1f}s"
        a, b = first[1], last[1]
        where = f"({a[0]:.0f}, {a[1]:.0f}, {a[2]:.0f}) to ({b[0]:.0f}, {b[1]:.0f}, {b[2]:.0f})"
        what = f"piece {piece}" if kind == "piece" else kind.upper()
        print(f"  {span}  {what:<10} {where}  {first[5]}")
        if kind == "wall" and first[4] is not None:
            for solid in walling_solids(files.solids, grid, first[4]):
                if solid not in named_walls:
                    named_walls.add(solid)
                    print(f"                       walled by {solid}")
        if kind == "no floor":
            j, i = grid.index(a[0], a[1])
            if 0 <= j < grid.ny and 0 <= i < grid.nx:
                floors = [round(float(v)) for v in grid.levels[:, j, i] if not np.isnan(v)]
                print(f"                       the grid's floors there: {floors or 'none'}")

    # The picture: the track's surroundings, pieces in color, the track over them.
    xs = [at[0] for _, at in samples]
    ys = [at[1] for _, at in samples]
    margin = 1200
    i0, i1 = (max(0, int((v - grid.x0) / grid.cell)) for v in (min(xs) - margin, max(xs) + margin))
    j0, j1 = (max(0, int((v - grid.y0) / grid.cell)) for v in (min(ys) - margin, max(ys) + margin))
    i1, j1 = min(i1, grid.nx - 1), min(j1, grid.ny - 1)
    top = np.full((grid.ny, grid.nx), -1, dtype=np.int64)
    for layer in range(len(grid.levels)):
        top = np.where(passable[layer], label[layer], top)
    palette = [(66, 135, 245), (245, 130, 48), (60, 180, 75), (145, 30, 180), (70, 240, 240), (240, 50, 230)]
    on_track = [p for *_, p, _, _ in rows if p is not None]
    ranked = list(dict.fromkeys(on_track))
    img = np.zeros((grid.ny, grid.nx, 3), dtype=np.uint8)
    img[:] = (40, 40, 46)
    img[grid.floors >= 1] = (110, 110, 105)
    img[grid.walls.any(axis=0)] = (225, 225, 225)
    img[top >= 0] = (170, 170, 165)
    for k, piece in enumerate(ranked):
        img[top == piece] = palette[k % len(palette)]
    crop = img[j0 : j1 + 1, i0 : i1 + 1][::-1]
    scale = max(1, min(6, 1400 // max(crop.shape[:2])))
    image = Image.fromarray(crop).resize((crop.shape[1] * scale, crop.shape[0] * scale), Image.NEAREST)
    pen = ImageDraw.Draw(image)

    def px(x: float, y: float) -> tuple[float, float]:
        return ((x - grid.x0) / grid.cell - i0) * scale, (j1 + 1 - (y - grid.y0) / grid.cell) * scale

    colors = {
        "piece": (0, 0, 0),
        "no floor": (255, 0, 0),
        "wall": (255, 0, 255),
        "edge": (255, 200, 0),
        "step": (0, 255, 255),
    }
    for prev, row in zip(rows, rows[1:]):
        (ax, ay), (bx, by) = px(*prev[1][:2]), px(*row[1][:2])
        if row[5].startswith("jump"):
            pen.line((ax, ay, bx, by), fill=(255, 255, 0), width=1)
        else:
            pen.line((ax, ay, bx, by), fill=colors[row[2]], width=3)
    legend = [
        f"{zone}: colored = the track's pieces, light gray = other pieces, white = wall,",
        "mid gray = floor too near a wall or drop. Track: black on a piece, red no floor,",
        "magenta wall, orange edge, cyan step, yellow a link",
    ]
    pen.rectangle((0, 0, image.width, 14 * len(legend) + 4), fill=(255, 255, 255))
    for k, line in enumerate(legend):
        pen.text((4, 2 + 14 * k), line, fill=(0, 0, 0))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / (zone.replace("/", "-") + ".png")
    image.save(path)
    print(f"  map: {path}")


def run_analyze(args: argparse.Namespace) -> int:
    track = read_track(Path(args.analyze))
    by_zone: dict[str, list] = {}
    for t, zone, at in track:
        by_zone.setdefault(zone, []).append((t, at))
    for zone, samples in by_zone.items():
        analyze_zone(zone, samples)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=int, default=1, help="Which client to read, 1-based (default 1).")
    parser.add_argument("--analyze", metavar="CSV", help="Lay a recorded track over its zone's grid.")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    sys.exit(run_analyze(arguments) if arguments.analyze else asyncio.run(run_record(arguments)))
