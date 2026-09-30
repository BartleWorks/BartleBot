"""Diagnostic: where is a fight's center, and does teleporting there join it? (#110)

handle_combat's late clients re-aim at the mob, which walks to its seat, hides or is
swapped once the fight starts. The fix (#110) has the first client in battle publish
its fight center, `client.duel.position()`, and sends everyone else there instead.
This probe checks the three things that plan rests on:

  (a) duel.position() is the center of the circle: seated fighters sit about 600
      units from it, and every client in the same fight reads the same point.
  (b) A client teleported to that center joins the fight on the first try.
  (c) How long that join takes.

OUTCOME: (b) held live. A teleport to the center joins the fight exactly as a teleport
onto an engaged mob does, so the node kept its existing retry loop and only changed
where a late client aims. No leader stage and no new timing constant were needed. Keep
the probe for re-checking after a game patch moves the duel struct.

HOW TO RUN
    1. Log in the wizards you bot with (two or more), grouped, in a zone with a mob.
    2. Start the probe in one of these modes:

         uv run python tools/probe_duel_center.py
             Watch only. Start a fight by hand with one wizard.

         uv run python tools/probe_duel_center.py --lead 1 --teleport all
             Client 1 teleports onto the mob (the closest one, or --enemy NAME).
             Once it is in battle, every client not yet in battle teleports once
             to the center. This is the flow the node change will use.

         uv run python tools/probe_duel_center.py --teleport 2
             Start the fight by hand; client 2 teleports once to the center.

    3. It prints a table once a second (--interval) until the fight ends or Ctrl+C,
       then a summary that answers (a), (b) and (c).

Clients are numbered in the order printed at startup, with their window handle and
position so you can tell which is which. That order need not match the app's P1-P4.

A teleport is made ONCE per client and never retried: a retry would hide exactly the
failure (b) is looking for. Use --delay to wait between the center being published
and the teleport, e.g. to see whether an immediate teleport is too early.

Wizard names and ids are never read or printed. Mob and object template names are
game data, not player data, and are printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field

from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.nodes import node_helpers

POLL = 0.1
# Seated fighters are this far from the center, per Deimos (`590 < distance < 610`).
SEAT_RADIUS = 600.0
SEAT_TOLERANCE = 25.0
# How long after entering battle a client is assumed to have walked to its seat.
SEATED_AFTER = 3.0
CIRCLE_NAMES = ("duel circle", "duelcircle")


async def _safe(coro):
    """The awaited value, or None if the read raised. Every read here is speculative."""
    try:
        return await coro
    except Exception:
        return None


async def _in_battle(client) -> bool:
    return bool(await _safe(client.in_battle()))


def _fmt_xyz(xyz) -> str:
    if xyz is None:
        return "(unreadable)"
    return f"({xyz.x:9.1f}, {xyz.y:9.1f}, {xyz.z:8.1f})"


def _fmt_dist(a, b) -> str:
    if a is None or b is None:
        return "    -"
    return f"{a.distance(b):7.1f}"


async def _find_mob(client, enemy: str):
    if enemy:
        return await _safe(node_helpers.find_closest_fight_target(client, enemy))
    return await _safe(client.find_closest_mob())


async def _circles(client) -> list[tuple[str, object]]:
    """Every entity whose template name looks like a duel circle, with its location."""
    found = []
    for entity in await _safe(client.get_base_entity_list()) or []:
        name = await _safe(entity.object_name())
        if not name:
            continue
        lowered = name.lower()
        if lowered in CIRCLE_NAMES or ("duel" in lowered and "circle" in lowered):
            found.append((name, await _safe(entity.location())))
    return found


@dataclass
class ClientRecord:
    index: int  # 1-based, as printed
    client: object
    battle_since: float | None = None  # monotonic time in_battle() first read true
    duel_centers: set = field(default_factory=set)  # rounded duel.position() readings
    seated_distances: list[float] = field(default_factory=list)
    teleported_at: float | None = None
    teleport_result: str | None = None


def _round(xyz) -> tuple:
    return (round(xyz.x, 1), round(xyz.y, 1), round(xyz.z, 1))


async def _teleport_and_time(rec: ClientRecord, center, timeout: float) -> None:
    """Teleport once to *center*, then time how long until in_battle() reads true."""
    if await _in_battle(rec.client):
        rec.teleport_result = "skipped: already in battle"
        return
    before = await _safe(rec.client.body.position())
    rec.teleported_at = time.monotonic()
    try:
        await rec.client.teleport(center)
    except Exception as exc:
        rec.teleport_result = f"teleport raised {type(exc).__name__}: {exc}"
        return
    after = await _safe(rec.client.body.position())
    print(
        f"  >> client {rec.index} teleported to center: moved {_fmt_dist(before, after).strip()} units, "
        f"now {_fmt_dist(after, center).strip()} from center"
    )
    deadline = rec.teleported_at + timeout
    while time.monotonic() < deadline:
        if await _in_battle(rec.client):
            elapsed = time.monotonic() - rec.teleported_at
            rec.teleport_result = f"JOINED after {elapsed:.2f}s"
            print(f"  >> client {rec.index}: {rec.teleport_result}")
            return
        await asyncio.sleep(POLL)
    rec.teleport_result = f"NOT JOINED within {timeout:.0f}s"
    print(f"  >> client {rec.index}: {rec.teleport_result}")


async def _lead(rec: ClientRecord, enemy: str, timeout: float) -> float | None:
    """Teleport the lead client onto the mob; seconds until it is in battle, or None."""
    mob = await _find_mob(rec.client, enemy)
    if mob is None:
        print(f"Lead client {rec.index}: no mob found ({enemy or 'closest'}).")
        return None
    name = await _safe(mob.object_name())
    print(f"Lead client {rec.index}: teleporting onto {name!r} at {_fmt_xyz(await _safe(mob.location()))}")
    start = time.monotonic()
    if not await _safe(rec.client.tp_to(mob)):
        print(f"Lead client {rec.index}: tp_to failed.")
        return None
    while time.monotonic() - start < timeout:
        if await _in_battle(rec.client):
            return time.monotonic() - start
        await asyncio.sleep(POLL)
    return None


async def main(args: argparse.Namespace) -> int:
    sprinter = WizSprinter()
    clients = sprinter.get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1

    print(f"Found {len(clients)} client(s); activating hooks...")
    try:
        await asyncio.gather(*[c.activate_hooks() for c in clients])
    except Exception as exc:
        print(f"Hook activation failed: {exc!r}")
        return 1

    records = [ClientRecord(i, c) for i, c in enumerate(clients, start=1)]
    try:
        return await _run(args, records)
    except asyncio.CancelledError:
        print("\nStopped before a fight started.")
        return 0
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass


def _parse_teleport(value: str | None, count: int) -> list[int]:
    if value is None:
        return []
    if value.lower() == "all":
        return list(range(1, count + 1))
    return [int(part) for part in value.split(",")]


async def _run(args: argparse.Namespace, records: list[ClientRecord]) -> int:
    print("\nClients:")
    for rec in records:
        pos = await _safe(rec.client.body.position())
        zone = await _safe(rec.client.zone_name())
        print(
            f"  [{rec.index}] hwnd={rec.client.window_handle}  in_battle={await _in_battle(rec.client)}  "
            f"pos={_fmt_xyz(pos)}  zone={zone}"
        )

    try:
        teleport_ids = _parse_teleport(args.teleport, len(records))
    except ValueError:
        print(f"--teleport must be 'all' or client numbers like 2 or 2,3; got {args.teleport!r}")
        return 2
    for n in [*teleport_ids, *([args.lead] if args.lead else [])]:
        if not 1 <= n <= len(records):
            print(f"No client {n}; have {len(records)}.")
            return 2
    if any([await _in_battle(rec.client) for rec in records]):
        print("\nNOTE: a client is already in battle. The duel struct may be from a fight this probe did not see")
        print("      start, so join timings below are only meaningful for clients teleported by the probe.")

    lead_time = None
    if args.lead:
        lead_time = await _lead(records[args.lead - 1], args.enemy, args.timeout)
        print(
            f"Lead client {args.lead}: "
            + (
                f"in battle after {lead_time:.2f}s"
                if lead_time is not None
                else f"NOT in battle within {args.timeout:.0f}s"
            )
        )

    print("\nWaiting for any client to be in battle...")
    leader = None
    while leader is None:
        for rec in records:
            if await _in_battle(rec.client):
                leader = rec
                break
        else:
            await asyncio.sleep(POLL)
    leader.battle_since = time.monotonic()

    # Guarded by in_battle(): outside a fight the struct still holds the last one's data.
    center = await _safe(leader.client.duel.position())
    yaw = await _safe(leader.client.duel.yaw())
    if center is None:
        print(f"Client {leader.index} is in battle but duel.position() could not be read.")
        return 1
    print(f"\nClient {leader.index} is in battle first. Published center {_fmt_xyz(center)}  yaw={yaw}")
    mob = await _find_mob(leader.client, args.enemy)
    if mob is not None:
        print(
            f"  closest mob {await _safe(mob.object_name())!r} is {_fmt_dist(await _safe(mob.location()), center).strip()} from center"
        )

    teleports = []
    if teleport_ids:
        if args.delay:
            print(f"  waiting {args.delay:.1f}s before teleporting...")
            await asyncio.sleep(args.delay)
        teleports = [
            asyncio.create_task(_teleport_and_time(records[n - 1], center, args.timeout)) for n in teleport_ids
        ]

    # Ctrl+C under asyncio.run cancels this task rather than raising KeyboardInterrupt
    # here; swallowing the cancel still prints the summary, and asyncio.run then exits.
    ended_polls = 0
    try:
        while True:
            await _tick(args, records, leader, center)
            if not await _in_battle(leader.client):
                ended_polls += 1
                if ended_polls >= 2:
                    print(f"\nClient {leader.index} has left battle; the fight is over.")
                    break
            await asyncio.sleep(args.interval)
    except asyncio.CancelledError:
        print("\nStopped by Ctrl+C.")

    for task in teleports:
        if not task.done():
            task.cancel()
    _summary(records, leader, center, lead_time)
    return 0


async def _tick(args, records: list[ClientRecord], leader: ClientRecord, center) -> None:
    now = time.monotonic()
    print(f"\n[t+{now - leader.battle_since:6.1f}s]")

    live = await _safe(leader.client.duel.position()) if await _in_battle(leader.client) else None
    moved = "" if live is None or _round(live) == _round(center) else "  <-- MOVED since published"
    print(f"  center (client {leader.index} duel.position): {_fmt_xyz(live)}{moved}")

    mob = await _find_mob(leader.client, args.enemy)
    if mob is not None:
        loc = await _safe(mob.location())
        print(f"  mob {await _safe(mob.object_name())!r:<28} {_fmt_xyz(loc)}  dist to center {_fmt_dist(loc, center)}")
    else:
        print("  mob: none found")

    for name, loc in await _circles(leader.client):
        print(f"  circle {name!r:<25} {_fmt_xyz(loc)}  dist to center {_fmt_dist(loc, center)}")

    for rec in records:
        in_battle = await _in_battle(rec.client)
        if in_battle and rec.battle_since is None:
            rec.battle_since = now
        pos = await _safe(rec.client.body.position())
        own = await _safe(rec.client.duel.position()) if in_battle else None
        if own is not None:
            rec.duel_centers.add(_round(own))
        if in_battle and pos is not None and now - rec.battle_since >= SEATED_AFTER:
            rec.seated_distances.append(pos.distance(center))
        own_note = ""
        if own is not None and _round(own) != _round(center):
            own_note = f"  own duel.position {_fmt_xyz(own)} DIFFERS"
        print(
            f"  client {rec.index}: in_battle={str(in_battle):<5} pos={_fmt_xyz(pos)}  "
            f"dist to center {_fmt_dist(pos, center)}{own_note}"
        )


def _summary(records: list[ClientRecord], leader: ClientRecord, center, lead_time: float | None) -> None:
    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  center published by client {leader.index}: {_fmt_xyz(center)}")
    if lead_time is not None:
        print(f"  lead client entered battle {lead_time:.2f}s after its teleport onto the mob")

    print("\n  (a) is duel.position() the center?")
    all_seated = []
    for rec in records:
        if not rec.seated_distances:
            print(f"      client {rec.index}: never seated in this fight")
            continue
        lo, hi = min(rec.seated_distances), max(rec.seated_distances)
        all_seated.extend(rec.seated_distances)
        verdict = "OK" if abs(lo - SEAT_RADIUS) <= SEAT_TOLERANCE and abs(hi - SEAT_RADIUS) <= SEAT_TOLERANCE else "OFF"
        print(f"      client {rec.index}: seated {lo:.1f}-{hi:.1f} units from center  [{verdict}]")
    centers = set().union(*(rec.duel_centers for rec in records))
    if len(centers) == 1:
        print("      every in-battle client read the same duel.position()")
    elif centers:
        print(f"      clients read {len(centers)} different duel.position() values: {sorted(centers)}")
    if all_seated:
        ok = all(abs(d - SEAT_RADIUS) <= SEAT_TOLERANCE for d in all_seated)
        print(f"      => {'YES' if ok else 'NO'}: seats are {'' if ok else 'not '}~{SEAT_RADIUS:.0f} units from it")

    print("\n  (b)/(c) teleport to the center, once:")
    tried = [rec for rec in records if rec.teleport_result is not None]
    if not tried:
        print("      no teleport was made (use --teleport N or --teleport all)")
    for rec in tried:
        print(f"      client {rec.index}: {rec.teleport_result}")
    print("=" * 72)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--teleport",
        metavar="N|N,M|all",
        help="Once a fight starts, teleport these clients (1-based, not yet in battle) once to its center.",
    )
    parser.add_argument("--lead", type=int, metavar="N", help="Teleport client N onto the mob to start the fight.")
    parser.add_argument(
        "--enemy", default="", help="Mob name for --lead and the mob column; default is the closest mob."
    )
    parser.add_argument(
        "--delay", type=float, default=0.0, help="Seconds between the center publishing and --teleport."
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="Seconds to wait for a join before giving up.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between table prints.")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
