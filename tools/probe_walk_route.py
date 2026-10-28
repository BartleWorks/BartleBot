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

The collision parser follows Deimos' src/collision.py (PeechezNCreem, ISC), and the rule for
when a solid blocks a foot comes from Deimos' src/collision_math.py (GPL-3).

HOW TO RUN
    uv run --with numpy python tools/probe_walk_route.py
    uv run --with numpy python tools/probe_walk_route.py --from "Hub Street1 Entrance" --to "Hedgemaze Exit" --to "-10515,2162"
    uv run --with numpy python tools/probe_walk_route.py --observed tools/probe_aggro_radius.20260927-run01.local.csv

--observed overlays mob positions logged by tools/probe_aggro_radius.py and reports how far
they strayed from the patrol lines, which is the check that the lines are where mobs walk.
Those files hold no wizard names or ids; this probe reads only their mob rows.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import functools
import glob
import heapq
import math
import re
import struct
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent / "zone_graph"))

import zone_nav  # noqa: E402
from wad_common import Deserializer, parse_lang, root_wad, text, to_plain, volumes, xyz, zone_wad  # noqa: E402
from zone_data import locations, zone_record  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ZONE = "WizardCity/WC_Streets/WC_Unicorn"
DEFAULT_FROM = "Hub Street1 Entrance"
DEFAULT_TO = ("Street1 Hedgemaze Exit", "Street1 Tower2 Exit", "WC-UW T1 Exit")
# Units per second on foot, unmounted, counting only horizontal movement (a stair's height is
# free). Measured at 600 on flat ground in Unicorn Way and 626 in a house; wizwalker says 580.
# A mount multiplies it: most by 1.4, the fastest by 1.55.
WIZARD_SPEED = 600.0

# Collision flags (Deimos' CollisionFlag).
WALKABLE = 1 << 1
OBJECT = 1 << 0
NON_MOVEMENT = (1 << 7) | (1 << 8) | (1 << 3) | (1 << 9)  # client object, trigger, hitscan, fog
BOX, SPHERE, CYLINDER, MESH = 0, 2, 3, 6

# When a solid walls off a foot at ground height z (Deimos' _collider_blocks_foot).
Z_BAND_UP = 50.0
Z_BAND_DOWN = 8.0
Z_EMBED_TOL = 75.0
Z_BOX_TOL = 20.0

# A triangle steeper than this is a cliff face, not floor.
MIN_FLOOR_NORMAL_Z = 0.5
# Two surfaces this far apart at one cell are two floors (a house's storeys, a bridge over a street).
LEVEL_GAP = 150.0
# A step may rise or drop this much on top of --max-rise per unit walked: a stair tread, a kerb.
STEP_TOL = 20.0
# Snapping a point to the grid, a unit of height costs this many units of distance, so a
# point keeps to its own storey rather than the nearer floor above or below it.
Z_RANK = 2.0


# --- collision.bcd -------------------------------------------------------------------------


@dataclass
class Solid:
    proxy: int
    category: int
    rotation: tuple[float, ...]
    location: tuple[float, float, float]
    scale: float
    params: tuple[float, ...]
    vertices: list[tuple[float, float, float]] | None = None
    faces: list[tuple[int, int, int]] | None = None


class _Stream:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def unpack(self, fmt: str) -> tuple:
        size = struct.calcsize(fmt)
        out = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return out

    def string(self) -> str:
        (length,) = self.unpack("<i")
        self.pos += length
        return ""


_PARAM_FORMATS = {0: "<fff", 1: "<fff", 2: "<f", 3: "<ff", 4: "<ff", 5: "<ffff", 6: ""}


def parse_collision(data: bytes) -> list[Solid]:
    stream = _Stream(data)
    (count,) = stream.unpack("<i")
    solids = []
    for _ in range(count):
        proxy, category, _collide = stream.unpack("<iII")
        vertices = faces = None
        if proxy == MESH:
            vertex_count, face_count = stream.unpack("<ii")
            vertices = [stream.unpack("<fff") for _ in range(vertex_count)]
            faces = []
            for _ in range(face_count):
                faces.append(stream.unpack("<iii"))
                stream.unpack("<fff")  # the face normal; recomputed from the vertices
        stream.string()  # name
        rotation = stream.unpack("<9f")
        location = stream.unpack("<fff")
        (scale,) = stream.unpack("<f")
        stream.string()  # material
        (proxy_again,) = stream.unpack("<i")
        params = stream.unpack(_PARAM_FORMATS[proxy_again]) if _PARAM_FORMATS[proxy_again] else ()
        solids.append(Solid(proxy, category, rotation, location, scale, params, vertices, faces))
    return solids


def _to_world(points: np.ndarray, solid: Solid) -> np.ndarray:
    # Deimos' toMultidim + transformCube: the stored matrix is applied transposed.
    m = np.asarray(solid.rotation, dtype=float).reshape(3, 3).T
    return points @ m + np.asarray(solid.location, dtype=float)


def walkable_triangles(solids: list[Solid]) -> np.ndarray:
    chunks = []
    for s in solids:
        if s.proxy == MESH and s.category & WALKABLE and s.faces:
            world = _to_world(np.asarray(s.vertices, dtype=float), s)
            chunks.append(world[np.asarray(s.faces, dtype=np.intp)])
    tris = np.concatenate(chunks) if chunks else np.empty((0, 3, 3))
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    keep = lengths > 1e-6
    tris, normals, lengths = tris[keep], normals[keep], lengths[keep]
    return tris[np.abs(normals[:, 2]) / lengths >= MIN_FLOOR_NORMAL_Z]


@dataclass
class Footprint:
    hull: np.ndarray | None  # convex polygon, CCW, or None for a circle
    center: tuple[float, float]
    radius: float
    zlo: float
    zhi: float
    is_box: bool


def _convex_hull(points: np.ndarray) -> np.ndarray:
    pts = sorted(set(map(tuple, points.tolist())))
    if len(pts) <= 2:
        return np.asarray(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.asarray(lower[:-1] + upper[:-1])


def blocking_footprints(solids: list[Solid]) -> list[Footprint]:
    out = []
    for s in solids:
        if s.category & NON_MOVEMENT and not s.category & OBJECT:
            continue
        if s.proxy == BOX:
            length, width, depth = (v / 2 for v in s.params)
            corners = np.array([(x, y, z) for x in (-length, length) for y in (-width, width) for z in (-depth, depth)])
            world = _to_world(corners, s)
            hull = _convex_hull(world[:, :2])
            if len(hull) < 3:
                continue
            out.append(Footprint(hull, (0.0, 0.0), 0.0, world[:, 2].min(), world[:, 2].max(), True))
        elif s.proxy == SPHERE:
            r = half = s.params[0] * s.scale
        elif s.proxy == CYLINDER:
            # A cylinder stores (length, radius), not the (radius, length) Deimos reads. Walked
            # in Unicorn Way: a wizard slid along the tree at (-3606, 6581), stored (264.9, 85.7),
            # holding exactly 135 units from its center, which is radius 85.7 plus the wizard's
            # own 49. Read the other way it is a 265-wide disc the wizard stood inside of.
            half, r = s.params[0] / 2 * s.scale, s.params[1] * s.scale
        else:
            continue
        if s.proxy != BOX and r > 0:
            x, y, z = s.location
            out.append(Footprint(None, (x, y), r, z - half, z + half, False))
    return out


# --- spawns and patrols --------------------------------------------------------------------


@dataclass
class Patrol:
    spawner: str
    template: str
    proximity: float
    max_spawns: int
    points: np.ndarray  # (N, 3), in path order
    start_node_type: int = 0  # 0 starts at the path's first node; 1 picks one of its nodes


def _listed(deserialize: Deserializer, wad, name: str, key: str) -> list:
    """A list field of one of the zone's files, [] when the file, the field or its value is
    missing: about a third of the zones a bot names have spawn files with nothing in them."""
    try:
        record = to_plain(deserialize(bytes(wad[name])))
    except Exception:
        return []
    return [item for item in ((record or {}).get(key) or []) if item]


def mob_patrols(zone: str, deserialize: Deserializer, starts: tuple[int, ...] = (0,)) -> list[Patrol]:
    """Every mob spawner's path, for the spawners whose start node type is in *starts*. Type 0
    starts at the path's first node and walks it (Unicorn Way's ghosts, checked live); type 1
    picks one of its nodes, and whether it then walks the path is what probe_mob_paths.py asks."""
    wad = zone_wad(zone)
    spawns = _listed(deserialize, wad, "spawnData.xml", "m_spawners")
    paths = {p["m_id"]: p.get("m_nodeIDs") or [] for p in _listed(deserialize, wad, "pathData.xml", "m_pathList")}
    nodes = {n["m_id"]: n["m_location"] for n in _listed(deserialize, wad, "pathNodeData.bin", "m_nodeList")}
    if not spawns:
        return []

    root = root_wad()
    manifest = to_plain(deserialize(bytes(root["TemplateManifest.xml"])))["m_serializedTemplates"]
    filenames = {e["m_id"]: e["m_filename"] for e in manifest}
    duelists: dict[int, float | None] = {}

    def proximity(template_id: int) -> float | None:
        if template_id not in duelists:
            duelists[template_id] = None
            if template_id in filenames:
                template = to_plain(deserialize(bytes(root[filenames[template_id]])))
                for b in template.get("m_behaviors") or []:
                    if isinstance(b, dict) and b.get("m_behaviorName") == "DuelistBehavior":
                        duelists[template_id] = float(b.get("m_npcProximity") or 0.0)
        return duelists[template_id]

    out = []
    for spawner in spawns:
        for entry in spawner.get("m_spawnList") or []:
            info = (entry or {}).get("m_objectInfo")
            if not info:
                continue
            template_id = info["m_templateID.m_full"]
            radius = proximity(template_id)
            kind = info["m_kStartNodeType"]
            if radius is None or kind not in starts or info["m_pathID"] not in paths:
                continue
            points = np.asarray([nodes[i] for i in paths[info["m_pathID"]] if i in nodes], dtype=float)
            if len(points):
                name = Path(filenames[template_id]).stem
                out.append(Patrol(spawner["m_name"], name, radius, spawner["m_maxNumberOfSpawns"], points, kind))
                break  # one patrol per spawner, whatever it rolls
    return out


def _segments(patrol: Patrol, closed: bool) -> np.ndarray:
    pts = patrol.points[:, :2]
    if len(pts) == 1:
        return np.array([[pts[0], pts[0]]])
    segs = np.stack([pts[:-1], pts[1:]], axis=1)
    if closed and len(pts) > 2:
        segs = np.concatenate([segs, [[pts[-1], pts[0]]]])
    return segs


def distance_to_segments(px: np.ndarray, py: np.ndarray, segs: np.ndarray) -> np.ndarray:
    best = np.full(px.shape, np.inf)
    for (ax, ay), (bx, by) in segs:
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = np.zeros(px.shape) if length2 == 0 else np.clip(((px - ax) * dx + (py - ay) * dy) / length2, 0, 1)
        best = np.minimum(best, np.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


# --- the grid ------------------------------------------------------------------------------
#
# A cell can hold several floors (a house's two storeys, a bridge over a street), so the grid
# is layered: levels[l, j, i] is the l-th floor up at cell (j, i), or nan. A layer number only
# orders the floors within one cell and does not name a storey, since a stair cell has one
# floor where its neighbors have two. So walking is decided by height: a step goes to the
# neighbor's floor at about the same height, which is what lets a stair, and nothing else,
# join two storeys.

Node = tuple[int, int, int]  # (layer, j, i)


class Grid:
    def __init__(self, tris: np.ndarray, cell: float) -> None:
        self.cell = cell
        self.x0, self.y0 = tris[:, :, 0].min() - cell, tris[:, :, 1].min() - cell
        self.nx = int((tris[:, :, 0].max() - self.x0) / cell) + 2
        self.ny = int((tris[:, :, 1].max() - self.y0) / cell) + 2
        hits = [hit for tri in tris if (hit := self._raster(tri)) is not None]
        self.levels = self._stack(*(np.concatenate(parts) for parts in zip(*hits)))
        self.walls = np.zeros(self.levels.shape, dtype=bool)

    @property
    def top(self) -> np.ndarray:
        """The highest floor at each cell, or nan."""
        return np.fmax.reduce(self.levels, axis=0)

    @property
    def floors(self) -> np.ndarray:
        """How many floors each cell has."""
        return (~np.isnan(self.levels)).sum(axis=0)

    def centers(self, i0: int, i1: int, j0: int, j1: int) -> tuple[np.ndarray, np.ndarray]:
        xs = self.x0 + (np.arange(i0, i1 + 1) + 0.5) * self.cell
        ys = self.y0 + (np.arange(j0, j1 + 1) + 0.5) * self.cell
        return np.meshgrid(xs, ys)

    def _span(self, lo_x: float, hi_x: float, lo_y: float, hi_y: float) -> tuple[int, int, int, int]:
        i0 = max(0, int((lo_x - self.x0) / self.cell))
        i1 = min(self.nx - 1, int((hi_x - self.x0) / self.cell))
        j0 = max(0, int((lo_y - self.y0) / self.cell))
        j1 = min(self.ny - 1, int((hi_y - self.y0) / self.cell))
        return i0, i1, j0, j1

    def _raster(self, tri: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(j, i, z) of every cell center the triangle covers, z being the triangle's height there."""
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
        i0, i1, j0, j1 = self._span(min(ax, bx, cx), max(ax, bx, cx), min(ay, by, cy), max(ay, by, cy))
        if i1 < i0 or j1 < j0:
            return None
        px, py = self.centers(i0, i1, j0, j1)
        det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(det) < 1e-9:
            return None
        s = ((px - ax) * (cy - ay) - (cx - ax) * (py - ay)) / det
        t = ((bx - ax) * (py - ay) - (px - ax) * (by - ay)) / det
        inside = (s >= -1e-6) & (t >= -1e-6) & (s + t <= 1 + 1e-6)
        if not inside.any():
            return None
        jj, ii = np.meshgrid(np.arange(j0, j1 + 1), np.arange(i0, i1 + 1), indexing="ij")
        z = az + s * (bz - az) + t * (cz - az)
        return jj[inside], ii[inside], z[inside]

    def _stack(self, j: np.ndarray, i: np.ndarray, z: np.ndarray) -> np.ndarray:
        """Sort each cell's heights into floors, LEVEL_GAP apart, keeping each floor's highest."""
        key = j * self.nx + i
        order = np.lexsort((z, key))
        key, z = key[order], z[order]
        new_cell = np.r_[True, key[1:] != key[:-1]]
        new_floor = new_cell | np.r_[True, np.diff(z) > LEVEL_GAP]
        floor_id = np.cumsum(new_floor) - 1
        layer = floor_id - floor_id[new_cell][np.cumsum(new_cell) - 1]
        last = np.r_[new_floor[1:], True]
        levels = np.full((int(layer.max()) + 1, self.ny, self.nx), np.nan)
        levels[layer[last], key[last] // self.nx, key[last] % self.nx] = z[last]
        return levels

    def add_walls(self, footprints: list[Footprint]) -> int:
        blocked = 0
        for f in footprints:
            if f.hull is None:
                (x, y), r = f.center, f.radius
                i0, i1, j0, j1 = self._span(x - r, x + r, y - r, y + r)
            else:
                i0, i1, j0, j1 = self._span(
                    f.hull[:, 0].min(), f.hull[:, 0].max(), f.hull[:, 1].min(), f.hull[:, 1].max()
                )
            if i1 < i0 or j1 < j0:
                continue
            px, py = self.centers(i0, i1, j0, j1)
            if f.hull is None:
                inside = np.hypot(px - x, py - y) <= r
            else:
                inside = np.ones(px.shape, dtype=bool)
                hull = f.hull
                for k in range(len(hull)):
                    (ax, ay), (bx, by) = hull[k], hull[(k + 1) % len(hull)]
                    inside &= (bx - ax) * (py - ay) - (by - ay) * (px - ax) >= 0
            touched = False
            for layer in range(len(self.levels)):
                z = self.levels[layer, j0 : j1 + 1, i0 : i1 + 1]
                with np.errstate(invalid="ignore"):
                    embedded = (f.zlo <= z) & ((f.zhi - z) >= (Z_BOX_TOL if f.is_box else Z_EMBED_TOL))
                    overhead = (z < f.zlo) & ((f.zlo - z) <= Z_BAND_UP)
                    hit = inside & ~np.isnan(z) & (z <= f.zhi + Z_BAND_DOWN) & (embedded | overhead)
                if hit.any():
                    self.walls[layer, j0 : j1 + 1, i0 : i1 + 1] |= hit
                    touched = True
            blocked += touched
        return blocked

    def passable(self, radius: float, max_rise: float) -> np.ndarray:
        """Floor that is not wall, and that has floor at about its own height all around it
        within the player's radius: away from walls, and from the edge of a drop."""
        ok = ~np.isnan(self.levels) & ~self.walls
        out = ok.copy()
        r = max(1, round(radius / self.cell))
        for dj in range(-r, r + 1):
            for di in range(-r, r + 1):
                if not (di or dj) or di * di + dj * dj > r * r:
                    continue
                tol = max_rise * math.hypot(dj, di) * self.cell + STEP_TOL
                near_z = np.roll(self.levels, (dj, di), axis=(1, 2))
                near_ok = np.roll(ok, (dj, di), axis=(1, 2))
                for layer in range(len(self.levels)):
                    support = np.zeros((self.ny, self.nx), dtype=bool)
                    with np.errstate(invalid="ignore"):
                        for other in range(len(self.levels)):
                            support |= near_ok[other] & (np.abs(near_z[other] - self.levels[layer]) <= tol)
                    out[layer] &= support
        return out

    def index(self, x: float, y: float) -> tuple[int, int]:
        return int((y - self.y0) / self.cell), int((x - self.x0) / self.cell)

    def world(self, layer: int, j: int, i: int) -> tuple[float, float, float]:
        return self.x0 + (i + 0.5) * self.cell, self.y0 + (j + 0.5) * self.cell, float(self.levels[layer, j, i])

    def all_centers(self) -> tuple[np.ndarray, np.ndarray]:
        return self.centers(0, self.nx - 1, 0, self.ny - 1)


def nearest_node(grid: Grid, passable: np.ndarray, x: float, y: float, z: float | None = None) -> Node | None:
    """The passable floor nearest (x, y), weighing height so a point keeps to its own storey."""
    ls, js, is_ = np.nonzero(passable)
    if not len(js):
        return None
    j, i = grid.index(x, y)
    d = (js - j) ** 2 + (is_ - i) ** 2
    if z is not None:
        d = d + ((grid.levels[ls, js, is_] - z) / grid.cell * Z_RANK) ** 2
    k = int(np.argmin(d))
    return int(ls[k]), int(js[k]), int(is_[k])


def astar(
    grid: Grid,
    passable: np.ndarray,
    cost: np.ndarray,
    start: Node,
    goal: Node,
    max_rise: float,
    portals: dict[int, list[tuple[int, float]]] | None = None,
) -> list[Node] | None:
    """A cell-by-cell route, or None. *portals* maps a node's flat index to the nodes a link
    puts it on, and what taking it costs; a route through one has two non-adjacent nodes in a
    row. The distance heuristic does not know about links, so a route using one is found but
    not always the shortest."""
    layers, ny, nx = grid.levels.shape
    plane = ny * nx
    ok = passable.ravel().tolist()
    z = np.nan_to_num(grid.levels).ravel().tolist()
    mult = cost.ravel().tolist()
    s = start[0] * plane + start[1] * nx + start[2]
    g = goal[0] * plane + goal[1] * nx + goal[2]
    _, gj, gi = goal
    c = grid.cell
    steps = [(dj, di, math.hypot(dj, di) * c) for dj in (-1, 0, 1) for di in (-1, 0, 1) if dj or di]

    def standing(j: int, i: int, height: float, tol: float) -> bool:
        return any(ok[n] and abs(z[n] - height) <= tol for n in range(j * nx + i, layers * plane, plane))

    best = {s: 0.0}
    came = {s: -1}
    heap = [(0.0, 0.0, s)]
    done = set()
    while heap:
        _, g_cost, cur = heapq.heappop(heap)
        if cur in done:
            continue
        if cur == g:
            path = []
            while cur != -1:
                layer, rest = divmod(cur, plane)
                path.append((layer, *divmod(rest, nx)))
                cur = came[cur]
            return path[::-1]
        done.add(cur)
        rest = cur % plane
        cj, ci = divmod(rest, nx)
        height = z[cur]
        for dj, di, length in steps:
            nj, ni = cj + dj, ci + di
            if not (0 <= nj < ny and 0 <= ni < nx):
                continue
            tol = max_rise * length + STEP_TOL
            if dj and di and not (standing(cj, ni, height, tol) and standing(nj, ci, height, tol)):
                continue  # no cutting a wall's corner
            for nb in range(nj * nx + ni, layers * plane, plane):
                if nb in done or not ok[nb] or abs(z[nb] - height) > tol:
                    continue
                ng = g_cost + length * (mult[rest] + mult[nj * nx + ni]) / 2
                if ng < best.get(nb, math.inf):
                    best[nb] = ng
                    came[nb] = cur
                    heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
        for nb, extra in portals.get(cur, ()) if portals else ():
            if nb in done:
                continue
            ng = g_cost + extra
            if ng < best.get(nb, math.inf):
                best[nb] = ng
                came[nb] = cur
                nj, ni = divmod(nb % plane, nx)
                heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
    return None


def pieces(grid: Grid, passable: np.ndarray, max_rise: float) -> np.ndarray:
    """Each passable floor spot's connected piece, -1 elsewhere: astar's step rule, flood-filled."""
    layers, ny, nx = passable.shape
    plane = ny * nx
    label = np.full(layers * plane, -1, dtype=np.int32)
    ok = passable.ravel()
    z = np.nan_to_num(grid.levels).ravel()
    steps = [
        (dj, di, max_rise * math.hypot(dj, di) * grid.cell + STEP_TOL)
        for dj in (-1, 0, 1)
        for di in (-1, 0, 1)
        if dj or di
    ]
    current = 0
    for start in np.flatnonzero(ok):
        if label[start] >= 0:
            continue
        label[start] = current
        queue = deque([int(start)])
        while queue:
            node = queue.popleft()
            j, i = divmod(node % plane, nx)
            height = z[node]
            for dj, di, tol in steps:
                nj, ni = j + dj, i + di
                if not (0 <= nj < ny and 0 <= ni < nx):
                    continue
                for nb in range(nj * nx + ni, layers * plane, plane):
                    if ok[nb] and label[nb] < 0 and abs(z[nb] - height) <= tol:
                        label[nb] = current
                        queue.append(nb)
        current += 1
    return label.reshape(passable.shape)


def _runs(path: list[Node]) -> list[list[Node]]:
    """*path* cut where it jumps: at each link, the next node is not the last one's neighbor."""
    runs = [[path[0]]]
    for a, b in zip(path, path[1:]):
        if abs(b[1] - a[1]) > 1 or abs(b[2] - a[2]) > 1:
            runs.append([])
        runs[-1].append(b)
    return runs


# --- in-zone links -----------------------------------------------------------------------------
#
# Some zones are pieces of floor no walk joins: Triton Avenue's two halves, its ledge and its
# isle. The game joins them with teleport volumes: step into one and the server moves the
# wizard to an arrival point, and keeps it there, because the move was its own. volumes.xml
# places each volume and names the event it raises; nothing decoded so far says where it
# lands, so the landing is the arrival point whose name best matches the volume's. "to" and
# "from" break a tie ("From Kraken Isle" lands at "From_Kraken_Isle", not "To_Kraken_Isle"),
# two landings fitting as well is no link, and a link must land on another piece of floor
# than the one it starts from: a zone's exits match an arrival point on their own piece by
# name ("Street4 to Cavern", "Cavern to Street4") and are not links. Cinematic triggers are
# left out. The pairing is a convention, not data, so a walk that steps into a volume and is
# not moved drops the link and replans (probe_walk_input.py); a quest gate looks the same.

LINK_REACH = 100.0  # floor this near a volume's center steps into it (Triton's are some 177 across)
LINK_SECONDS = 4.0  # what taking a link costs a route, in seconds of walking: the move, and a margin
_LINK_VOLUME = re.compile(r"teleport|activator", re.IGNORECASE)
# zone.nav pairs only a volume whose name says it moves the wizard: an activator is as often a
# quest's trigger (Triton's "Activator Volume Kraken" spawns the boss on Kraken Isle).
_MOVING_VOLUME = re.compile(r"teleport|(?<![a-z])to(?![a-z])", re.IGNORECASE)
_FILLER = {"target", "location", "teleport", "teleportvol", "activator", "volume", "the", "trigger"}
_DIRECTIONS = {"to", "from"}


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if w} - _FILLER


@dataclass(frozen=True)
class Link:
    name: str  # the volume's, or the placed object's zone tag
    source: tuple[float, float, float]  # the volume's center, or the object's
    landing: str
    target: tuple[float, float, float]  # where it lands: an arrival point, a ride's far end, a nav node
    # "volume" is stepped into; "interact" and "ride" are pressed, "ride" carrying the wizard over.
    kind: str = "volume"

    @property
    def reach(self) -> float:
        """How near the source a wizard stands to take the link."""
        return LINK_REACH if self.kind == "volume" else INTERACT_REACH

    @property
    def seconds(self) -> float:
        return {"volume": LINK_SECONDS, "interact": INTERACT_SECONDS}.get(self.kind, RIDE_SECONDS)


def link_candidates(wad, named: dict[str, tuple[float, float, float]]) -> list[Link]:
    """Each teleport or activator volume, with the arrival point its name best matches."""
    try:
        found = volumes(bytes(wad["volumes.xml"]))
    except KeyError:
        return []
    out = []
    for name, center in found:
        words = _words(name)
        if not _LINK_VOLUME.search(name) or "cinematic" in words:
            continue
        core, way = words - _DIRECTIONS, words & _DIRECTIONS
        scored = []
        for landing, target in named.items():
            other = _words(landing)
            shared = len(core & (other - _DIRECTIONS))
            if shared and "cinematic" not in other:
                scored.append((2 * shared + (way == other & _DIRECTIONS), landing, target))
        if not scored:
            continue
        scored.sort(key=lambda row: row[0], reverse=True)
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            continue
        out.append(Link(name, tuple(center), scored[0][1], tuple(scored[0][2])))
    return out


# --- interact links ----------------------------------------------------------------------------
#
# Most of the game's in-zone links are not volumes but placed objects the wizard presses the
# interact key at: Triton's fast-travel stones, Floating Mountains' ziplines, ropes, boats,
# mine carts. The behavior that moves the wizard is stripped from the client's copy of every
# one (DeletedBehavior), so where each lands is put together from what the files do keep:
#
#   - a ride's template (a zipline's) holds its two ends as LOCATION records: it lands on the
#     arrival point nearest the second (both ziplines pressed live did, 3 units off);
#   - a stone of a TeleporterX family lands at the arrival point nearest its partner: X at the
#     one nearest X_ToMain, X_ToMain at the one nearest the hub's stones (Triton's four, pressed
#     live, all did; Krokotopia's and Hametsu's land on names like "Target_Arena", "BackToHub");
#   - a pair of travel objects named X and X_B: each at the arrival point nearest the other;
#   - zone.nav, the quest arrow's graph, has an edge no walk takes for most links (zone_nav.py):
#     one starting near a placed object whose prompt is a travel verb is that object's link, to
#     the arrival point nearest the edge's far end, or the far end itself. zone_links does the
#     same for a volume its name does not pair with another piece.
#
# None of these is the server's own word, so the walk drops a link it presses and is not moved
# by, as it does a volume's.

INTERACT_REACH = 150.0  # floor this near the object's center is near enough to press it
INTERACT_SECONDS = 6.0  # the press, the teleport, a margin
RIDE_SECONDS = 20.0  # a zipline's ride, before one is timed
NAV_OBJECT_REACH = 700.0  # a zone.nav edge's end, or a ride's first end, this near an object is its
RIDE_LANDING_REACH = 1000.0  # a ride lands on the arrival point this near its far end (800-920 seen)
PAD_REACH = 300.0  # an object this near a volume is the pad drawn at it
SHORTCUT_MIN = 1000.0  # a press that moves the wizard less than this, up included, is no link worth routing through
_TRAVEL_PROMPT = re.compile(r"\b(teleport|ride|climb|descend|ascend|travel|exit)\b", re.IGNORECASE)
# An object with no prompt the files resolve is taken by its template's name instead.
_TRAVEL_NAME = re.compile(r"teleport|rope|ladder|zip.?line|boat|elevator|lift|cart|dingh?y|portal", re.IGNORECASE)
_STONE = re.compile(r"Teleporter([A-F])(?![a-z])")  # not TeleporterArch
_STONE_BACK = re.compile(r"To(Main|Hub)|BackToHub", re.IGNORECASE)
# A template's InteractableBehavior: its prompt's GUI.lang key, and each LOCATION record's
# name, position and zone, by property hash.
_INTERACTABLE = b"InteractableBehavior"
_PROMPT_KEY = re.compile(rb"([A-Za-z]+)_(\d{8})")
_LOCATION_NAME = bytes.fromhex("cbdec46e")
_LOCATION_XYZ = tuple(bytes.fromhex(h) for h in ("bffe5a5a", "c0fe5a5a", "c1fe5a5a"))
_LOCATION_ZONE = bytes.fromhex("c122a8ba")


@dataclass(frozen=True)
class _Template:
    name: str
    prompt: str  # the interact prompt's English text, "" for none
    ends: tuple[tuple[tuple[float, float, float], str], ...]  # each LOCATION record: position, zone


def _short_string(data: bytes, at: int) -> str:
    """A string whose length byte is twice its length, as these records store a short one."""
    return data[at + 1 : at + 1 + (data[at] >> 1)].decode("latin-1")


def _locations_in(data: bytes) -> tuple[tuple[tuple[float, float, float], str], ...]:
    starts = [m.end() for m in re.finditer(re.escape(_LOCATION_NAME), data)]
    out = []
    for at, end in zip(starts, [*starts[1:], len(data)]):
        record = data[at:end]
        if b"LOCATION" not in record:
            continue
        found = [record.find(h) for h in _LOCATION_XYZ]
        if min(found) < 0:
            continue
        x, y, z = (struct.unpack_from("<f", record, k + 4)[0] for k in found)
        k = record.find(_LOCATION_ZONE)
        out.append(((x, y, z), _short_string(record, k + 4) if k >= 0 else ""))
    return tuple(out)


@functools.cache
def _templates() -> dict[int, _Template]:
    """Every placed-object template that can be interacted with, by template id."""
    root = root_wad()
    langs: dict[str, dict[str, str]] = {}

    def lang(stem: str, key: str) -> str:
        if stem not in langs:
            try:
                langs[stem] = parse_lang(bytes(root[f"Locale/en-US/{stem}.lang"]))
            except KeyError:
                langs[stem] = {}
        return langs[stem].get(key, "")

    manifest = Deserializer()(bytes(root["TemplateManifest.xml"]))
    out = {}
    for entry in manifest["m_serializedTemplates"]:
        try:
            data = bytes(root[text(entry["m_filename"])])
        except KeyError:
            continue
        at = data.find(_INTERACTABLE)
        if at < 0:
            continue
        # The prompt is the first key there that resolves: "Press X to Teleport" from GUI.lang, or
        # the object's own words ("Climb Rope", from WizardGameObjects.lang).
        keys = (lang(k.group(1).decode(), k.group(2).decode()) for k in _PROMPT_KEY.finditer(data, at, at + 600))
        prompt = next((words for words in keys if words), "")
        out[int(entry["m_id"])] = _Template(Path(text(entry["m_filename"])).stem, prompt, _locations_in(data))
    return out


def interact_candidates(zone: str, wad, record, named: dict[str, tuple[float, float, float]]) -> list[Link]:
    """Each placed object pressed to move the wizard, with where it is taken to land, best source first."""
    templates = _templates()
    placed = []
    for obj in record["m_objectList"] or []:
        template = templates.get(int(obj["m_templateID.m_full"])) if obj else None
        if template is not None:
            placed.append((text(obj["m_zoneTag"]) or template.name, xyz(obj["m_location"]), template))
    points = {name: p for name, p in named.items() if "cinematic" not in name.lower()}
    found: dict[str, Link] = {}

    # A ride is shown, then the server sets the wizard down on the arrival point past its far
    # end: ZipLine-3-Quest, pressed live, landed 3 units from "Target-ToMajorIsland", 800 past it.
    for tag, at, template in placed:
        ends = [p for p, where in template.ends if where == zone]
        if len(ends) == 2 and math.dist(ends[0][:2], at[:2]) <= NAV_OBJECT_REACH:
            landing, target = f"{template.name}'s far end", ends[1]
            if points:
                nearest = min(points, key=lambda n: math.dist(points[n][:2], ends[1][:2]))
                if math.dist(points[nearest][:2], ends[1][:2]) <= RIDE_LANDING_REACH:
                    landing, target = nearest, points[nearest]
            found[tag] = Link(tag, at, landing, target, "ride")

    stones = [
        (tag, at, m.group(1), bool(_STONE_BACK.search(tag))) for tag, at, _ in placed if (m := _STONE.search(tag))
    ]
    hub = [at for _, at, _, back in stones if not back]
    if hub and points:
        center = tuple(sum(c) / len(hub) for c in zip(*hub))
        for tag, at, letter, back in stones:
            partner = center if back else next((p for _, p, kin, ret in stones if kin == letter and ret), None)
            if partner is None:
                continue
            landing = min(points, key=lambda n: math.dist(points[n][:2], partner[:2]))
            found.setdefault(tag, Link(tag, at, landing, points[landing], "interact"))

    travel = [
        (tag, at)
        for tag, at, template in placed
        if _TRAVEL_PROMPT.search(template.prompt) or (not template.prompt and _TRAVEL_NAME.search(template.name))
    ]
    # A pair named X and X_B (Krokotopia's Forgotten Tombs teleporters): each lands at the
    # arrival point nearest the other.
    by_name = {tag.split(" instance")[0]: (tag, at) for tag, at in travel}
    if points:
        for name, (tag, at) in by_name.items():
            other = by_name.get(name.removesuffix("_B")) if name.endswith("_B") else by_name.get(name + "_B")
            if other is not None:
                landing = min(points, key=lambda n: math.dist(points[n][:2], other[1][:2]))
                found.setdefault(tag, Link(tag, at, landing, points[landing], "interact"))

    nav = zone_nav.load(wad)
    if nav is not None:
        for tag, (at, landing, target) in _nav_links(nav, travel, points).items():
            found.setdefault(tag, Link(tag, at, landing, target, "interact"))
    return list(found.values())


def _nav_links(nav, sources, points) -> dict[str, tuple[tuple[float, float, float], str, tuple[float, float, float]]]:
    """For each (name, position) of *sources* that a zone.nav link edge starts near, the nearest
    such edge's landing: the arrival point nearest its far end, or the far end itself."""
    edges = []
    for a, b in nav.link_edges():
        near, far = nav.nodes[a], nav.nodes[b]
        for name, at in sources:
            reach = math.dist(at, near)
            if reach <= NAV_OBJECT_REACH:
                edges.append((reach, name, at, b, far))
    out = {}
    for _, name, at, b, far in sorted(edges):
        if name in out:
            continue
        landing, target = f"zone.nav node {b}", far
        if points:
            nearest = min(points, key=lambda n: math.dist(points[n], far))
            if math.dist(points[nearest], far) <= NAV_OBJECT_REACH:
                landing, target = nearest, points[nearest]
        out[name] = (at, landing, target)
    return out


def zone_links(zone: str, grid: Grid, passable: np.ndarray, label: np.ndarray, record, named) -> list[Link]:
    """The zone's links: each volume that joins one piece of its floor to another, and each
    object pressed to move the wizard that joins pieces or is a shortcut worth its press. A
    volume whose name pairs it with no landing on another piece (Lemuria's tower lifts, each
    named for the end it stands at) takes its landing from zone.nav instead. An object standing
    at a volume is the pad drawn there (Triton's Telepapads), not a link of its own."""
    wad = zone_wad(zone)
    by_name = link_candidates(wad, named)
    try:
        every_volume = [(n, tuple(p)) for n, p in volumes(bytes(wad["volumes.xml"])) if _LINK_VOLUME.search(n)]
    except KeyError:
        every_volume = []

    def ends(link: Link) -> tuple[Node, Node] | None:
        here, there = nearest_node(grid, passable, *link.source), nearest_node(grid, passable, *link.target)
        if here is None or there is None or math.dist(grid.world(*here)[:2], link.source[:2]) > link.reach + grid.cell:
            return None
        return here, there

    out = []
    joined = set()
    for link in by_name:
        found = ends(link)
        if found and label[found[0]] != label[found[1]]:
            out.append(link)
            joined.add(link.name)
    nav = zone_nav.load(wad)
    if nav is not None:
        points = {n: p for n, p in named.items() if "cinematic" not in n.lower()}
        unpaired = [
            (n, p)
            for n, p in every_volume
            if n not in joined and "cinematic" not in n.lower() and _MOVING_VOLUME.search(n)
        ]
        for name, (at, landing, target) in _nav_links(nav, unpaired, points).items():
            link = Link(name, at, landing, target)
            found = ends(link)
            if found and label[found[0]] != label[found[1]]:
                out.append(link)
    for link in interact_candidates(zone, wad, record, named):
        found = ends(link)
        if found is None or any(math.dist(link.source[:2], v[1][:2]) <= PAD_REACH for v in every_volume):
            continue
        if label[found[0]] != label[found[1]] or math.dist(link.source, link.target) >= SHORTCUT_MIN:
            out.append(link)
    return out


@dataclass
class Leg:
    """A stretch walked: its waypoints, and the link stepped into at its end (None for the last)."""

    waypoints: list[tuple[float, float, float]]
    link: Link | None = None


def _straight_ok(
    grid: Grid,
    passable: np.ndarray,
    clearance: np.ndarray,
    a: Node,
    b: Node,
    least: float,
    max_rise: float,
) -> bool:
    """Whether a wizard can walk from a to b in a straight line, never passing closer to a
    patrol than *least*, nor stepping up or down more than a walk allows."""
    n = int(max(abs(b[1] - a[1]), abs(b[2] - a[2])) * 2) + 1
    js = np.rint(np.linspace(a[1], b[1], n)).astype(int)
    is_ = np.rint(np.linspace(a[2], b[2], n)).astype(int)
    if (clearance[js, is_] < least).any():
        return False
    step = math.hypot(b[1] - a[1], b[2] - a[2]) * grid.cell / max(n - 1, 1)
    tol = max_rise * max(step, grid.cell) + STEP_TOL
    if len(grid.levels) == 1:
        z = grid.levels[0, js, is_]
        return bool(passable[0, js, is_].all() and (np.abs(np.diff(z)) <= tol).all())
    # Several floors: follow the floor at about the height walked so far, cell by cell.
    heights = np.where(passable[:, js, is_], grid.levels[:, js, is_], np.nan).T.tolist()
    z = float(grid.levels[a])
    for column in heights:
        nearest = min((abs(h - z), h) for h in column if h == h) if any(h == h for h in column) else None
        if nearest is None or nearest[0] > tol:
            return False
        z = nearest[1]
    return abs(z - float(grid.levels[b])) <= tol


def smooth(
    grid: Grid,
    passable: np.ndarray,
    clearance: np.ndarray,
    path: list[Node],
    soft: float,
    max_rise: float,
) -> list[Node]:
    """Cut a cell-by-cell path down to the points where a walker has to turn.

    From each kept point, the next is the furthest point along the path still reachable in a
    straight line. The line may not pass closer to a patrol than the path itself did between
    the two, so straightening never trades safety for distance; beyond --soft it does not
    matter, as the cost there is nil."""
    if len(path) <= 2:
        return list(path)
    along = [float(clearance[j, i]) for _, j, i in path]
    out = [path[0]]
    a = 0
    while a < len(path) - 1:
        b = a + 1
        least = along[a]
        for k in range(a + 1, len(path)):
            least = min(least, along[k])
            if k > a + 1 and not _straight_ok(
                grid, passable, clearance, path[a], path[k], min(least, soft) - grid.cell, max_rise
            ):
                break
            b = k
        out.append(path[b])
        a = b
    return out


@dataclass
class Planner:
    """One zone's grid, walls and patrols, built once and asked for as many routes as needed."""

    zone: str
    grid: Grid
    passable: np.ndarray  # per floor: (layer, j, i)
    clearance: np.ndarray  # per cell: distance beyond the nearest patrol's trigger radius
    cost: np.ndarray  # per cell
    patrols: list[Patrol]
    named: dict[str, tuple[float, float, float]]
    soft: float
    max_rise: float
    summary: str
    links: list[Link] = dataclasses.field(default_factory=list)

    def node(self, x: float, y: float, z: float | None = None) -> Node | None:
        return nearest_node(self.grid, self.passable, x, y, z)

    def route(self, start: tuple[float, ...], goal: tuple[float, ...], avoid: bool = True) -> list[Node] | None:
        """A cell-by-cell route between two points, each (x, y) or (x, y, z)."""
        s, g = self.node(*start), self.node(*goal)
        if s is None or g is None:
            return None
        cost = self.cost if avoid else np.ones_like(self.cost)
        return astar(self.grid, self.passable, cost, s, g, self.max_rise, self.portals())

    def portals(self) -> dict[int, list[tuple[int, float]]]:
        """For astar: every passable spot within a link's reach of its source, at about its
        height, leads to the spot nearest where the link lands."""
        layers, ny, nx = self.passable.shape
        plane = ny * nx
        px, py = self.grid.all_centers()
        out: dict[int, list[tuple[int, float]]] = {}
        for link in self.links:
            landing = self.node(*link.target)
            if landing is None:
                continue
            to = landing[0] * plane + landing[1] * nx + landing[2]
            near = (np.hypot(px - link.source[0], py - link.source[1]) <= link.reach)[None] & self.passable
            with np.errstate(invalid="ignore"):
                near &= np.abs(self.grid.levels - link.source[2]) <= LEVEL_GAP
            for layer, j, i in zip(*np.nonzero(near)):
                out.setdefault(int(layer * plane + j * nx + i), []).append((to, link.seconds * WIZARD_SPEED))
        return out

    def turns(self, path: list[Node]) -> list[Node]:
        """The path cut to its turns, each stretch between links straightened on its own."""
        return self.turns_and_jumps(path)[0]

    def turns_and_jumps(self, path: list[Node]) -> tuple[list[Node], set[int]]:
        """turns(), and each k whose step from turn k to turn k + 1 is a link rather than walked."""
        out: list[Node] = []
        jumps: set[int] = set()
        for run in _runs(path):
            if out:
                jumps.add(len(out) - 1)
            out += smooth(self.grid, self.passable, self.clearance, run, self.soft, self.max_rise)
        return out, jumps

    def legs(self, path: list[Node]) -> list[Leg]:
        """The path as stretches to walk, each but the last ending in the link that joins it to the
        next: at a volume's center, or, for an object pressed, on the floor near it."""
        runs = _runs(path)
        out = []
        for k, run in enumerate(runs):
            straight = smooth(self.grid, self.passable, self.clearance, run, self.soft, self.max_rise)
            points = [self.grid.world(*n) for n in straight]
            link = None
            if k + 1 < len(runs):
                lands_on = runs[k + 1][0]
                ends_at = self.grid.world(*run[-1])
                for candidate in self.links:
                    near = math.dist(candidate.source[:2], ends_at[:2]) <= candidate.reach + self.grid.cell
                    if near and self.node(*candidate.target) == lands_on:
                        link = candidate
                        break
                if link is not None and link.kind == "volume":
                    points.append((link.source[0], link.source[1], points[-1][2]))
            out.append(Leg(points, link))
        return out

    def waypoints(self, path: list[Node]) -> list[tuple[float, float, float]]:
        """Every leg's waypoints in a row, start first."""
        return [p for leg in self.legs(path) for p in leg.waypoints]

    def drop(self, link: Link) -> None:
        """Stop routing through *link*: stepping into it moved nobody."""
        self.links = [other for other in self.links if other != link]

    def around(self, discs: list[tuple[float, float, float]]) -> Planner:
        """A copy whose floor is closed within each (x, y, radius) of *discs*, on every storey:
        the live mobs, to route around where they are now. The original is untouched."""
        px, py = self.grid.all_centers()
        closed = np.zeros(px.shape, dtype=bool)
        for x, y, radius in discs:
            closed |= np.hypot(px - x, py - y) <= radius
        return dataclasses.replace(self, passable=self.passable & ~closed[None])

    def block(self, x: float, y: float, z: float, radius: float) -> int:
        """Mark the floor within *radius* of (x, y), at about height z, impassable: something a
        walk ran into that the collision file does not have. Returns how many spots it closed."""
        grid = self.grid
        px, py = grid.all_centers()
        near = np.hypot(px - x, py - y) <= radius
        with np.errstate(invalid="ignore"):
            hit = near[None] & (np.abs(grid.levels - z) <= LEVEL_GAP / 2) & self.passable
        self.passable &= ~hit
        return int(hit.sum())


def build_planner(
    zone: str,
    *,
    cell: float = 40.0,
    radius: float = 50.0,
    hard: float = 250.0,
    soft: float = 1300.0,
    weight: float = 8.0,
    hard_weight: float = 50.0,
    max_rise: float = 1.0,
    ignore_cylinders: bool = False,
) -> Planner:
    """*ignore_cylinders* leaves every cylinder off the grid, as reading them wrong once did, so
    a route runs through the trees: a way to make a walk stall, for testing its recovery."""
    started = time.perf_counter()
    deserialize = Deserializer()
    solids = parse_collision(bytes(zone_wad(zone)["collision.bcd"]))
    if ignore_cylinders:
        solids = [s for s in solids if s.proxy != CYLINDER]
    tris = walkable_triangles(solids)
    grid = Grid(tris, cell)
    footprints = blocking_footprints(solids)
    walled = grid.add_walls(footprints)
    passable = grid.passable(radius, max_rise)
    floors = grid.floors

    # Both start node types walk their paths: type 1 checked live in Triton Avenue (probe_mob_paths.py).
    patrols = mob_patrols(zone, deserialize, starts=(0, 1))
    # Clearance: how far outside the nearest patrolling mob's own trigger radius a cell is,
    # so a fairy (275) keeps the planner further off than a ghost (200).
    px_, py_ = grid.all_centers()
    clearance = np.full(px_.shape, np.inf)
    for p in patrols:
        clearance = np.minimum(clearance, distance_to_segments(px_, py_, _segments(p, closed=True)) - p.proximity)
    ramp = np.clip((soft - clearance) / (soft - hard), 0, 1) ** 2
    cost = 1 + weight * ramp
    cost[clearance < hard] = 1 + hard_weight

    lines = [
        f"{zone}: {len(solids)} collision shapes, {len(tris)} floor triangles, "
        f"{len(footprints)} solids ({walled} touching a floor)",
        f"grid {grid.nx}x{grid.ny} at {grid.cell:.0f}: {(floors >= 1).sum()} floor cells, "
        f"{(floors >= 2).sum()} of them with more than one floor (up to {len(grid.levels)}), "
        f"{passable.sum()} passable floor spots  [{time.perf_counter() - started:.1f}s]",
    ]
    lines += [
        f"  patrol {p.spawner!r}: {p.template} x{p.max_spawns}, {len(p.points)} nodes, trigger radius {p.proximity:.0f}"
        for p in patrols
    ]
    record = zone_record(zone, deserialize)
    named = dict(locations(record))
    links = zone_links(zone, grid, passable, pieces(grid, passable, max_rise), record, named)
    lines += [f"  {link.kind} link {link.name!r} -> {link.landing!r}" for link in links]
    planner = Planner(zone, grid, passable, clearance, cost, patrols, named, soft, max_rise, "\n".join(lines))
    planner.links = links
    return planner


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
    open_segs = np.concatenate([_segments(p, closed=False) for p in patrols])
    closing = np.concatenate([_segments(p, closed=True)[-1:] for p in patrols if len(p.points) > 2])
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

    def path_line(path: list[Node], color: tuple[int, int, int], width: int) -> None:
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
    )
    print(planner.summary)
    grid, named = planner.grid, planner.named

    start_name, start_xy = resolve_point(args.start, named)
    routes = []
    for spec in args.to or DEFAULT_TO:
        goal_name, goal_xy = resolve_point(spec, named)
        t0 = time.perf_counter()
        shortest = planner.route(start_xy, goal_xy, avoid=False)
        avoiding = planner.route(start_xy, goal_xy)
        straight, straight_jumps = planner.turns_and_jumps(avoiding) if avoiding else (None, set())
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
