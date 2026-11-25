"""Diagnostic: walk a planned route on a live client, with core's walker. (movement anti-cheat)

probe_walk_route.py plans a route from a zone's own files. This walks one: it builds the zone's
planner from its wad (so --test-stall can leave the trees out), plans from wherever the wizard
stands to --to, and hands both to core's RouteWalker (bartleby.runtime.movement.walk), on a
movement lease of its own arbiter. The walker is what a bot will walk with; core's
docs/WALKING.md, Walking a route, says what it does: forward held and the yaw written each tick,
resets walked again, stalls backed off from, links stood in or pressed, live mobs detoured around
before they are waited for, every plan off the event loop, and no speed or position ever written.

The keys are the account's own, read from the client's memory (core's clients/game_keys).

--shortest walks the route that ignores patrols, which is the way to test the mob watcher.
--test-stall builds the grid without its cylinders (the trees), which makes stalls to recover
from. --interact, on arriving, presses the interact key and reports where the game moves the
wizard: how an interact link whose destination is only on the server is learned.

OUTCOME (the probe's own walk, which core's walker was moved from, 2026-10-27 to 2026-11-08):

Unicorn Way: from the Commons gate to T1, 26 legs and 20,614 units, it arrived 46 units from the
goal in 35.2s against 35.5s planned: median 9 units off the route, max 68, never nearer than 253
beyond a trigger radius, no jumps, no stalls, no battle, never once stopping to turn.

Housing_FarmHouse/Interior: from the main floor down two flights to the basement (z 3100 to 2718
to 2300), 18 units from the goal in 13.1s against 11.3s, stopping twice to turn on the landing
with the turn keys. Back up writing the yaw: 16 units off in 11.3s against 11.3s, median 5 units
off the route and max 28, no stop to turn, and none of the 12 yaws written read back changed. So
the yaw is the wizard's own to set, and writing it steers better than the turn keys, which the
walker does not use.

Unicorn Way again, on a +40% mount: 840 units a second expected, 838 measured. Twice the server
threw the wizard back mid-walk (585 and 644 units, to where it stood some 0.7s before), the reset
that ordinary play sees; reading the position back every tick absorbed both, re-walking the
stretch. With --test-stall a wizard grazing a trunk slides around it, the yaw re-aimed each tick;
aimed at a trunk's middle it stalls, and then it backed off, closed 23 spots ahead, replanned
around in 2 legs and arrived 4 units off. Half a second of the backward key had carried it 413
units, so a back-off is by distance. Mobs: Unicorn Way's ghosts walk their loops at 89 units a
second and never pause. Waiting alone, --shortest down the Ghosts 02 corridor sat out its 30s,
the loop never clear; with detours, the same patrols were crossed to Street 1 House 1 in 29.2s
(2 detours, 20.5s waiting while a detour beside a ghost was too tight) and back to T1 in 11.2s
(3 detours, 2.1s waiting), never nearer than 11 units beyond a trigger radius and margin, and no
battle.

Triton Avenue, links: To TempestNexus, High_Ledge, Tele Down and Tele Up were each taken on a
walk, the server moving the wizard 835 to 7,597 units and leaving it there; each landed within 5
units of the arrival point its name paired it with. Most fired at the volume's edge. Two stalls
on the street, against something the collision file lacks, were recovered.

Triton Avenue, interact links (--interact): its fast-travel stones' destinations exist only on
the server. All four were pressed: the hub's TeleporterA and TeleporterB land on "Target location
Triton_A" and "Triton_B" (1 and 5 units off), and TeleporterA_ToMain and TeleporterB_ToMain on
"Target location Hub Teleporter" (2 off), each in one jump of 17,724 to 20,479 units, 0.2s after
the press, settled by 1.2s. Every approach stalled once on the stone itself: a placed object's
collision is not in collision.bcd, so the grid lacks it.

Floating Mountains, pressed links in a route: AZ-ZipLine-3 moved nobody and was dropped, the
replan took AZ-ZipLine-3-Quest at the same spot, landing 3 units from "Target-ToMajorIsland"; 33
units from the goal in 29.9s. Back over AZ-ZipLine-4: moved 2.2s after the press, 5,148 units in
one tick, 3 units from "Target-FromMajorIsland"; 13 from the goal in 23.7s. A zipline is the
server's teleport after the ride is shown, not a ride.

Mirage's hub, ladders: up Ladder 02, pressed one tick after stopping, moved nobody; the game takes
0.2 to 0.4s to show the prompt, and a press before it does nothing. So a press waits for the
prompt (is_in_npc_range). Then Ladder 02 down, Ladder 05 up, and Ladder 01 down and Ladder 07 up
in one route each landed 0 to 4 units off the arrival point paired, in one tick 0.06 to 0.12s
after the press.

Not yet run on core's walker in game (2026-11-25).

HOW TO RUN
    1. Log a wizard in, standing in a zone (anywhere: the route starts where it stands).
    2. Check the plan without moving:

         uv run python tools/probe_walk_input.py --to "WC-UW T1 Exit" --dry-run

    3. Walk it, hands off the keyboard:

         uv run python tools/probe_walk_input.py --to "WC-UW T1 Exit"
         uv run python tools/probe_walk_input.py --to "WC-UW T1 Exit" --shortest      # through patrols
         uv run python tools/probe_walk_input.py --to "WC-UW T1 Exit" --test-stall    # into trees

Each walk writes logs/walk_route/walk-<time>.csv (time, position, yaw, waypoint, keys, what it
chose and the room it had; no names or ids), logs/walk_route/mobs-<time>.csv (each mob's game id,
template and position as read), and draws the trail over the routes on walk-<time>.png.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bartleby.runtime.arbiter import Arbiter, Owner  # noqa: E402
from bartleby.runtime.game.key_bindings import GameAction  # noqa: E402
from bartleby.runtime.movement.walk import RouteWalker, WalkTick, client_mobs  # noqa: E402
from probe_walk_route import (  # noqa: E402
    REPO,
    Planner,
    _reader,
    build_planner,
    render,
    resolve_point,
)
from zone_files import zone_record  # noqa: E402 - core's wad reader, beside probe_walk_route's

# --interact: after pressing the interact key, watch this long for the game to move the wizard,
# and call it settled once it has moved under SETTLED_MOVE in the last SETTLED_FOR seconds.
INTERACT_WAIT = 30.0
SETTLED_MOVE = 20.0
SETTLED_FOR = 1.0
LINK_MOVED = 300.0
STALL_MIN_MOVE = 60.0
TICK = 0.05
SETTLE = 0.3
BATTLE_POLL = 0.5


async def read(client) -> tuple[float, float, float]:
    pos = await client.body.position()
    return pos.x, pos.y, pos.z


async def interact_and_watch(client, lease, planner: Planner) -> None:
    """Press the interact key where the wizard stands, and report what moved it: how soon, how
    far, whether in one jump (a teleport) or many small steps (a ride, such as a zipline), and
    which arrival point it settled nearest. Some interact links keep their destination only on
    the server (the client's template has DeletedBehavior in its place), so this is how one is
    learned."""
    x0, y0, z0 = await read(client)
    zone = await client.zone_name()
    pressed = time.monotonic()
    await lease.input.press(GameAction.INTERACT)
    trail = [(0.0, x0, y0, z0)]
    first = None
    last_zone_check = 0.0
    while time.monotonic() - pressed < INTERACT_WAIT:
        await asyncio.sleep(TICK)
        x, y, z = await read(client)
        t = time.monotonic() - pressed
        trail.append((t, x, y, z))
        if first is None and math.dist((x0, y0), (x, y)) > STALL_MIN_MOVE:
            first = t
        if t - last_zone_check >= BATTLE_POLL:
            last_zone_check = t
            if await client.zone_name() != zone:
                print(f"  interact: the zone changed {t:.1f}s after pressing: a zone exit, not an in-zone link")
                return
        if first is not None and math.dist((x0, y0), (x, y)) > LINK_MOVED:
            recent = [p for p in trail if p[0] >= t - SETTLED_FOR]
            if recent[0][0] <= t - SETTLED_FOR + TICK and math.dist(recent[0][1:3], (x, y)) < SETTLED_MOVE:
                break
    if first is None:
        print(f"  interact: pressed, and nothing moved the wizard in {INTERACT_WAIT:.0f}s")
        return
    t, x, y, z = trail[-1]
    steps = [math.dist(a[1:3], b[1:3]) for a, b in zip(trail, trail[1:])]
    kind = "one jump" if max(steps) > LINK_MOVED else f"a ride over {sum(1 for s in steps if s > 5)} steps"
    nearest = sorted((math.dist(p[:2], (x, y)), name) for name, p in planner.named.items())[:2]
    print(
        f"  interact: first moved {first:.1f}s after pressing, settled at {t:.1f}s, {math.dist((x0, y0), (x, y)):.0f} "
        f"units from where it stood ({sum(steps):.0f} travelled, {kind}), at ({x:.0f}, {y:.0f}, {z:.0f})"
    )
    print("  landed nearest: " + "; ".join(f"{name} ({d:.0f} away)" for d, name in nearest))


def clearance_at(planner: Planner, x: float, y: float) -> float:
    j, i = planner.grid.index(x, y)
    if 0 <= j < planner.grid.ny and 0 <= i < planner.grid.nx:
        return float(planner.clearance[j, i])
    return math.nan


def deviation(points: np.ndarray, route: list[tuple[float, float, float]]) -> np.ndarray:
    best = np.full(len(points), np.inf)
    for (ax, ay, _), (bx, by, _) in zip(route, route[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1.0
        t = np.clip(((points[:, 0] - ax) * dx + (points[:, 1] - ay) * dy) / length2, 0, 1)
        best = np.minimum(best, np.hypot(points[:, 0] - (ax + t * dx), points[:, 1] - (ay + t * dy)))
    return best


def logged_mobs(client, writer, started: float):
    """core's client_mobs, every reading also written to the mobs CSV."""
    read = client_mobs(client)

    async def read_and_log():
        seen = await read()
        t = time.monotonic() - started
        for key, name, (x, y, z) in seen:
            writer.writerow([f"{t:.3f}", key, name, f"{x:.1f}", f"{y:.1f}", f"{z:.1f}"])
        return seen

    return read_and_log


async def main(args: argparse.Namespace) -> int:
    from wizwalker.extensions.wizsprinter import WizSprinter

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= args.client <= len(clients):
        print(f"No client {args.client}; have {len(clients)}.")
        return 2
    client = clients[args.client - 1]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = REPO / "logs" / "walk_route"
    out_dir.mkdir(parents=True, exist_ok=True)
    arbiter = Arbiter()
    try:
        await client.activate_hooks()
        zone = await client.zone_name()
        print(f"client {args.client} is in {zone}; building its grid...")
        planner = await asyncio.to_thread(build_planner, zone, ignore_cylinders=args.test_stall)
        reader = _reader()
        placed = {f"object {o.tag}": o.at for o in reader.placed_objects(zone_record(zone, reader.deserialize))}
        goal_name, goal = resolve_point(args.to, {**planner.named, **placed})
        x, y, z = await read(client)
        route = await asyncio.to_thread(planner.route, (x, y, z), goal, not args.shortest)
        if route is None:
            print(f"no route from ({x:.0f}, {y:.0f}) to {goal_name}")
            return 1
        legs = planner.legs(route)
        legs[0].waypoints[0] = (x, y, legs[0].waypoints[0][2])
        planned = sum(math.dist(a[:2], b[:2]) for leg in legs for a, b in zip(leg.waypoints, leg.waypoints[1:]))
        links = [leg.link for leg in legs if leg.link]
        print(f"route to {goal_name}: {planned:.0f} units walked, {len(links)} links")
        k = 0
        for leg in legs:
            for wx, wy, wz in leg.waypoints:
                print(f"  {k:2}  ({wx:9.1f}, {wy:9.1f}, {wz:7.1f})  clearance {clearance_at(planner, wx, wy):6.0f}")
                k += 1
            if leg.link:
                how = "step into" if leg.link.kind == "volume" else f"press ({leg.link.kind})"
                print(f"      {how} {leg.link.name!r}, landing at {leg.link.landing!r}")
        if args.dry_run:
            png = out_dir / f"plan-{stamp}.png"
            render(png, planner, 250.0, [(goal_name, None, route, planner.turns(route))], np.empty((0, 2)))
            print(f"dry run; wrote {png}")
            return 0

        csv_path = out_dir / f"walk-{stamp}.csv"
        mobs_path = out_dir / f"mobs-{stamp}.csv"
        with (
            csv_path.open("w", newline="", encoding="utf-8") as f,
            mobs_path.open("w", newline="", encoding="utf-8") as g,
        ):
            writer, mob_writer = csv.writer(f), csv.writer(g)
            writer.writerow(["t", "x", "y", "z", "yaw", "waypoint", "keys", "mode", "room_ahead"])
            mob_writer.writerow(["t", "gid", "template", "x", "y", "z"])

            def tick(t: WalkTick) -> None:
                writer.writerow(
                    [
                        f"{t.t:.3f}",
                        *(f"{v:.1f}" for v in t.position),
                        f"{t.yaw:.4f}",
                        t.waypoint,
                        "+".join(sorted(a.name for a in t.held)),
                        t.mode,
                        f"{t.room:.0f}",
                    ]
                )

            walker = RouteWalker(
                client,
                planner,
                avoid=not args.shortest,
                mobs=logged_mobs(client, mob_writer, time.monotonic()),
                label=f"client {args.client}",
                on_tick=tick,
            )
            async with arbiter.acquire(client, Owner.MOVEMENT, detail="probe_walk_input") as lease:
                result = await walker.walk(lease, goal)
                print(f"\n{result.describe()}")
                if result.arrived and args.interact:
                    await asyncio.sleep(SETTLE)
                    await interact_and_watch(client, lease, planner)

        print(
            f"  waited for mobs {result.waited:.1f}s in all; least room beyond a trigger radius and margin "
            f"{result.least_room:.0f}; {result.detours} detours; recovered from {result.recoveries} stalls; "
            f"replanned {result.replans} times; spent {result.planning:.1f}s planning"
        )
        if result.links_taken or result.links_dropped:
            print(f"  took {result.links_taken} links; dropped {result.links_dropped} that moved nobody")
        trail = np.array([p[:2] for p in result.trail]) if result.trail else np.empty((0, 2))
        if len(trail):
            off = np.min([deviation(trail, r) for r in result.routes], axis=0)
            print(
                f"  off the nearest route walked: p50 {np.percentile(off, 50):.0f}  p90 {np.percentile(off, 90):.0f}  max {off.max():.0f}"
            )
        png = out_dir / f"walk-{stamp}.png"
        render(png, planner, 250.0, [(goal_name, None, route, planner.turns(route))], trail, "the walked trail")
        print(f"wrote {csv_path}\n      {mobs_path}\n      {png}")
    finally:
        await arbiter.revoke_all()
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass
    return 0


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--to",
        required=True,
        help="arrival point or placed object (zone tag), by any unique substring, or x,y or x,y,z, in the wizard's zone",
    )
    ap.add_argument("--client", type=int, default=1, help="which client, 1-based (default 1)")
    ap.add_argument("--dry-run", action="store_true", help="plan and draw the route; press nothing")
    ap.add_argument("--shortest", action="store_true", help="walk the shortest route, not the one avoiding patrols")
    ap.add_argument(
        "--test-stall", action="store_true", help="plan without the zone's cylinders, so the walk stalls on trees"
    )
    ap.add_argument(
        "--interact",
        action="store_true",
        help="on arriving, press the interact key and report where the game moves the wizard",
    )
    return ap.parse_args()


if __name__ == "__main__":
    # Which app this is, for the parts of bartleby-core that say so, as bartlebot.py does first.
    from bartleby.shared.product import use

    from bartlebot.packaging.product import product

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    use(product())
    sys.exit(asyncio.run(main(_parse_args())))
