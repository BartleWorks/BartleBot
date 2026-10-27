"""Diagnostic: do a zone's mobs walk the paths its spawn data gives them, or stand? (movement anti-cheat)

Every mob spawner names a path, and its start node type says how the mob begins on it: 0
starts at the path's first node, 1 picks one of its nodes. Type 0 walks its path in a loop:
Unicorn Way's ghosts did, at 89 units a second, within a median 23 units of the line. Type 1
is most of the game's mobs (3,978 spawns in 219 zones against 2,759 in 66), and whether it
then walks the path or stands at its node decides whether the walk planner's danger is a
line along the path or a disc at each node.

This watches without moving. It reads every mob's position for --seconds, then puts each
mob's track on the nearest of the zone's mob paths and says which it did:

  - stood: moved less than STOOD over the whole watch;
  - walked its path: moved, and stayed within ON_PATH of the path's line, its last node joined
    back to its first;
  - wandered: moved, and strayed further than that.

Mob game ids and template names are the game's; no wizard's name or id is read.

OUTCOME (Triton Avenue, 2026-10-27): type 1 walks its path too. All 6 type-1 ghosts and all 6
type-0 eels in sight followed their path's line (p90 within 27-50 units); none stood. The
ghosts moved at a median 39 units a second, the eels at 182. Only 12 of the zone's 96 spawns
were in the client's entity list, those near the wizard, so live readings cover only the
mobs nearby and the paths cover the rest; build_planner now avoids both start types.

HOW TO RUN
    1. Log a wizard in, standing in a zone with mobs (Triton Avenue has both kinds), out of
       their reach.
    2. uv run --with numpy python tools/probe_mob_paths.py --seconds 120

Writes logs/walk_route/mobpaths-<time>.csv: one row per mob read.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_walk_input import MobWatch  # noqa: E402
from probe_walk_route import REPO, Deserializer, Patrol, _segments, distance_to_segments, mob_patrols  # noqa: E402

STOOD = 50.0
ON_PATH = 150.0
# A track this far from every mob path is put on none.
NO_PATH = 1000.0
MIN_SAMPLES = 20


class _Rows:
    """A csv writer that also keeps every row, for the analysis afterwards."""

    def __init__(self, writer) -> None:
        self.writer, self.rows = writer, []

    def writerow(self, row: list) -> None:
        self.writer.writerow(row)
        self.rows.append(row)


def verdict(track: np.ndarray, patrols: list[Patrol]) -> tuple[str, Patrol | None, float, float]:
    """(what the mob did, the path it is on, how far it moved, its p90 distance from that path)."""
    moved = float(np.hypot(*(track[:, :2] - track[0, :2]).T).max())
    best, best_d = None, math.inf
    for p in patrols:
        d = float(np.median(distance_to_segments(track[:, 0], track[:, 1], _segments(p, closed=True))))
        if d < best_d:
            best, best_d = p, d
    if best is None or best_d > NO_PATH:
        return ("stood" if moved < STOOD else "wandered"), None, moved, math.inf
    off = float(np.percentile(distance_to_segments(track[:, 0], track[:, 1], _segments(best, closed=True)), 90))
    if moved < STOOD:
        return "stood", best, moved, off
    return ("walked its path" if off <= ON_PATH else "wandered"), best, moved, off


async def main(args: argparse.Namespace) -> int:
    from wizwalker.extensions.wizsprinter import WizSprinter

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    client = clients[args.client - 1]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = REPO / "logs" / "walk_route" / f"mobpaths-{stamp}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        await client.activate_hooks()
        zone = await client.zone_name()
        patrols = await asyncio.to_thread(mob_patrols, zone, Deserializer(), (0, 1))
        kinds = defaultdict(int)
        for p in patrols:
            kinds[p.start_node_type] += p.max_spawns
        print(f"{zone}: {len(patrols)} mob spawners with paths ({kinds[0]} type-0 spawns, {kinds[1]} type-1)")
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["t", "gid", "template", "x", "y", "z"])
            rows = _Rows(writer)
            watch = MobWatch(client, {}, rows, time.monotonic())
            watch.start()
            try:
                for left in range(int(args.seconds), 0, -10):
                    print(f"  watching: {len(watch.mobs)} mobs, {left}s left", flush=True)
                    await asyncio.sleep(min(10, left))
            finally:
                watch.stop()
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass

    tracks: dict[int, list] = defaultdict(list)
    names: dict[int, str] = {}
    for t, gid, name, x, y, z in rows.rows:
        tracks[gid].append((float(x), float(y), float(z), float(t)))
        names[gid] = name
    tally: dict[tuple[str, str], int] = defaultdict(int)
    speeds: dict[str, list[float]] = defaultdict(list)
    print(f"\n{'mob':28} {'samples':>7} {'moved':>6} {'p90 off path':>12}  path (start type)  verdict")
    for gid, samples in sorted(tracks.items(), key=lambda kv: names[kv[0]]):
        if len(samples) < MIN_SAMPLES:
            continue
        track = np.array(samples)
        what, patrol, moved, off = verdict(track, patrols)
        kind = f"type {patrol.start_node_type}" if patrol else "none"
        tally[(kind, what)] += 1
        steps = np.hypot(*np.diff(track[:, :2], axis=0).T) / np.maximum(np.diff(track[:, 3]), 1e-3)
        if what != "stood":
            speeds[kind].append(float(np.median(steps[steps > 5])) if (steps > 5).any() else 0.0)
        where = f"{patrol.spawner} ({kind})" if patrol else "none"
        print(f"{names[gid][:28]:28} {len(track):7} {moved:6.0f} {off:12.0f}  {where[:40]:40} {what}")
    print("\nby path start type:")
    for (kind, what), count in sorted(tally.items()):
        print(f"  {kind:7} {what:16} {count}")
    for kind, values in speeds.items():
        print(f"  {kind}: moving mobs' median speed {statistics.median(values):.0f} units/s")
    print(f"wrote {out}")
    return 0


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=120.0, help="how long to watch (default 120)")
    ap.add_argument("--client", type=int, default=1, help="which client, 1-based (default 1)")
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
