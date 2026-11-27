"""Diagnostic: how do wisps move, and how close must a wizard be to take one? (#343)

Collect Wisps walks to a wisp where it was when the walk was planned. Whether that is good
enough, or a walk must aim again, or ahead, depends on how wisps move and how near a wizard must
come, which nobody had measured (Stage 4 of epic #340). This probe records both.

It watches one client: the wizard's own position and health and mana ratios, and the position of
every entity whose template name holds "Wisp", every --interval seconds. The list of wisps is read
again every LIST_REFRESH seconds, so a wisp that lingers in memory after it is taken shows as one
still listed.

A pickup is a rise in the wizard's health or mana ratio, put down to the nearest wisp of that kind
at the sample before it. It is timed by the ratio and not by the wisp leaving the list, since a
taken wisp flies to the wizard and lingers beside it for a second or more before it goes.

The summary says, per wisp:

  - how it moved until taken: still (strayed under STILL from where it was first seen), loops
    (left its ground and came back to it, as a wisp on a path or an orbit would), heads one way
    (net travel over STRAIGHT of its path), follows a path (wanders, but along other wisps' tracks:
    a path seen for less than a lap), or wanders; its median speed, how far it strayed on the
    ground, and how far up and down it went;
  - how it ended: still there at the end, taken, or gone untaken (despawned, or out of render
    range);
  - for one taken, how far off it was at the last sample before the ratio rose, by how much the
    ratio rose, where it came to rest and how soon, and how long it lingered after the rise;
  - for one a full wizard came within FULL_NEAR of and did not take, the nearest it came.

It writes nothing to the game: it only reads. You walk, or stand; it watches.

HOW TO RUN
    1. Log a wizard in, near wisps (after a fight, or in a world's hub). Close BartleBot first:
       two programs hooking one client clash.
    2. Start it, for two minutes here:

         uv run --no-sync python tools/probe_wisps.py --seconds 120

    3. For how wisps move, stand still. For the pickup distance, be well below full on a wisp's
       kind (several wisps' worth) and stand still on a moving wisp's way, so it comes to you at
       its own speed, or walk slowly toward a still one.
    4. Ctrl+C stops early and prints the summary.

Each run writes its samples to its own tools/probe_wisps.<time>.local.csv (gitignored). Re-read one
without the game running:

         uv run --no-sync python tools/probe_wisps.py --analyze tools/probe_wisps.*.local.csv

Runs are not pooled: each file is summarized on its own, since a wisp id means nothing across runs.

Distances are 2D, as a walk measures them.

Not in the plan: it samples every 0.1 s, not POLL_INTERVAL (0.25 s), so a pickup is placed to
within a tenth of a second of a wisp's way.

Wizard names and ids are never read or printed. Wisp template names and game ids are game data.

OUTCOME, 2026-11-27: Ahmarra's Eastern Sea, outdoors, ten WC_WispHealth wisps. All ten go round one
shared loop at 88-89 units a second, lapping in about 99 s, and one taken is replaced on the loop
11-22 s later. A wizard below full took three, each 401-418 off at the last sample before its health
rose (25% of max each, the third filling it). The wisp then flew in, resting 44-46 off 0.9 s
after the rise, and left the entity list 1.3-1.8 s after it. Full, the wizard let the
rest pass within 137-146.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import csv
import glob
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_INTERVAL = 0.1
#: The entity list is read this often; between reads only the listed wisps' positions are.
LIST_REFRESH = 1.0
#: A wisp that strayed less than this on the ground is still.
STILL = 10.0
#: Net travel over this share of the path walked: it heads one way.
STRAIGHT = 0.8
#: A wisp back within this of where it was, after being more than twice this away, loops.
REVISIT = 15.0
#: A wisp this far from where it was first seen has left it; back within REVISIT, it has lapped.
LAP_AWAY = 200.0
#: A wisp whose samples lie within this of another's track, at the median, shares its path.
SHARED = 30.0
#: A ratio this close to 1 is full.
FULL = 0.999
#: A ratio up by more than this has risen.
RISE = 1e-4
#: A full wizard this near a wisp it did not take gets a line: a needy one would have taken it.
FULL_NEAR = 600.0

CSV_FIELDS = ["t", "what", "id", "name", "x", "y", "z", "health", "mana"]
KINDS = ("health", "mana")


def kind_of(name: str) -> str:
    """The resource a wisp gives, from its template name: health, mana, or other."""
    lowered = name.lower()
    if "health" in lowered:
        return "health"
    if "mana" in lowered:
        return "mana"
    return "other"


@dataclass
class Wizard:
    t: float
    at: tuple[float, float, float]
    health: float
    mana: float

    def ratio(self, kind: str) -> float | None:
        return {"health": self.health, "mana": self.mana}.get(kind)


@dataclass
class Sighting:
    t: float
    wisp: int
    name: str
    at: tuple[float, float, float]


@dataclass
class Run:
    """One run's samples: the wizard's, in time order, and each wisp's sightings."""

    wizard: list[Wizard] = field(default_factory=list)
    sightings: list[Sighting] = field(default_factory=list)
    zone: str = ""

    @property
    def end(self) -> float:
        times = [w.t for w in self.wizard] + [s.t for s in self.sightings]
        return max(times, default=0.0)

    def tracks(self) -> dict[int, list[Sighting]]:
        tracks: dict[int, list[Sighting]] = {}
        for sighting in self.sightings:
            tracks.setdefault(sighting.wisp, []).append(sighting)
        for track in tracks.values():
            track.sort(key=lambda s: s.t)
        return tracks

    def wizard_at(self, t: float) -> Wizard | None:
        """The wizard's sample nearest *t*."""
        if not self.wizard:
            return None
        times = [w.t for w in self.wizard]
        i = bisect.bisect_left(times, t)
        near = [j for j in (i - 1, i) if 0 <= j < len(times)]
        return self.wizard[min(near, key=lambda j: abs(times[j] - t))]


def flat(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a[:2], b[:2])


@dataclass
class Motion:
    shape: str
    speed: float
    stray: float
    rise: float
    seconds: float
    lap: float | None = None


def motion(track: list[Sighting]) -> Motion:
    """How a wisp moved over *track*: its shape, median ground speed, ground stray from where it was
    first seen, the height it went through, how long it was seen, and its lap time if it lapped."""
    start = track[0].at
    stray = max(flat(start, s.at) for s in track)
    heights = [s.at[2] for s in track]
    seconds = track[-1].t - track[0].t
    steps = [(a, b) for a, b in zip(track, track[1:]) if b.t > a.t]
    speeds = [flat(a.at, b.at) / (b.t - a.t) for a, b in steps]
    speed = statistics.median(speeds) if speeds else 0.0
    walked = sum(flat(a.at, b.at) for a, b in steps)
    return Motion(_shape(track, stray, walked), speed, stray, max(heights) - min(heights), seconds, lap(track))


def lap(track: list[Sighting]) -> float | None:
    """Seconds from the wisp's first sighting until it came back within REVISIT of that spot, after
    going over LAP_AWAY from it, or None if it never did."""
    start = track[0]
    left = False
    for later in track[1:]:
        away = flat(start.at, later.at)
        if away > LAP_AWAY:
            left = True
        elif left and away <= REVISIT:
            return later.t - start.t
    return None


def sharers(tracks: dict[int, list[Sighting]], wisp: int) -> int:
    """How many other wisps' tracks *wisp*'s lies along: its samples within SHARED of theirs, at the median."""
    mine = _ground(tracks[wisp])
    count = 0
    for other, theirs in tracks.items():
        if other == wisp:
            continue
        gaps = np.linalg.norm(mine[:, None, :] - _ground(theirs)[None, :, :], axis=2).min(axis=1)
        if float(np.median(gaps)) <= SHARED:
            count += 1
    return count


def _shape(track: list[Sighting], stray: float, walked: float) -> str:
    if stray < STILL:
        return "still"
    if _loops(track):
        return "loops"
    if walked and flat(track[0].at, track[-1].at) / walked >= STRAIGHT:
        return "heads one way"
    return "wanders"


def _ground(track: list[Sighting]) -> np.ndarray:
    return np.array([s.at[:2] for s in track], dtype=float)


def _loops(track: list[Sighting]) -> bool:
    """Whether the track came back within REVISIT of a spot it held, after going over twice that away."""
    points = _ground(track)
    gaps = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    for i in range(len(points)):
        later = gaps[i, i + 1 :]
        away = np.flatnonzero(later > 2 * REVISIT)
        if away.size and (later[away[0] :] <= REVISIT).any():
            return True
    return False


@dataclass
class Pickup:
    """A rise in one kind's ratio, and the wisp it is put down to (None: no wisp of the kind in sight)."""

    t: float
    kind: str
    rose: float
    wisp: int | None = None
    off: float | None = None
    arrived: float | None = None
    rest: float | None = None
    linger: float | None = None


def pickups(run: Run) -> list[Pickup]:
    """Every rise in the wizard's health or mana ratio, each put down to the nearest wisp of its kind
    at the sample before it, with how that wisp came in and how long it lingered after."""
    at_time: dict[float, list[Sighting]] = {}
    for sighting in run.sightings:
        at_time.setdefault(sighting.t, []).append(sighting)
    tracks = run.tracks()
    found = []
    for before, after in zip(run.wizard, run.wizard[1:]):
        for kind in KINDS:
            rose = after.ratio(kind) - before.ratio(kind)
            if rose <= RISE:
                continue
            pickup = Pickup(after.t, kind, rose)
            candidates = [s for s in at_time.get(before.t, []) if kind_of(s.name) == kind]
            if candidates:
                nearest = min(candidates, key=lambda s: flat(before.at, s.at))
                _follow(run, pickup, nearest, before, tracks[nearest.wisp])
            found.append(pickup)
    return found


def _follow(run: Run, pickup: Pickup, nearest: Sighting, before: Wizard, track: list[Sighting]) -> None:
    pickup.wisp = nearest.wisp
    pickup.off = flat(before.at, nearest.at)
    later = [s for s in track if s.t > before.t]
    if not later:
        return
    offs = [flat(run.wizard_at(s.t).at, s.at) for s in later]
    pickup.rest = min(offs)
    pickup.arrived = next(s.t for s, d in zip(later, offs) if d <= pickup.rest + STILL) - pickup.t
    if later[-1].t < run.end:
        pickup.linger = later[-1].t - pickup.t


def nearest_while_full(run: Run, track: list[Sighting]) -> float | None:
    """The nearest the wizard came to the wisp while full on its kind and the wisp was still seen."""
    kind = kind_of(track[0].name)
    best = None
    for sighting in track:
        wizard = run.wizard_at(sighting.t)
        ratio = wizard.ratio(kind) if wizard else None
        if ratio is None or ratio < FULL:
            continue
        d = flat(wizard.at, sighting.at)
        best = d if best is None or d < best else best
    return best


def summarize(run: Run) -> str:
    tracks = run.tracks()
    found = pickups(run)
    taken = {p.wisp: p for p in found if p.wisp is not None}
    if not tracks:
        return f"No wisp seen in {run.end:.0f} s ({len(run.wizard)} wizard samples)."
    where = f" in {run.zone}" if run.zone else ""
    lines = [f"{len(tracks)} wisps{where} over {run.end:.0f} s, {len(run.wizard)} wizard samples"]
    for wisp, track in sorted(tracks.items(), key=lambda kv: kv[1][0].t):
        pickup = taken.get(wisp)
        until = [s for s in track if pickup is None or s.t < pickup.t] or track[:1]
        m = motion(until)
        shared = sharers(tracks, wisp)
        # Seen for less than a lap, a wisp on a bending path others follow too looks like one wandering.
        shape = "follows a path" if m.shape == "wanders" and shared else m.shape
        lines.append(
            f"{track[0].name} {wisp}: seen {track[0].t:.1f}-{track[-1].t:.1f} s ({len(track)} samples), {shape}, "
            f"speed {m.speed:.0f}/s, strayed {m.stray:.0f}, height range {m.rise:.0f}"
            + (f", lapped in {m.lap:.1f} s" if m.lap is not None else "")
        )
        if shared:
            lines.append(f"  its path is {shared} other wisps' too")
        if pickup is not None:
            lines.append(f"  taken at {pickup.t:.1f} s: {_taken(pickup)}")
        elif track[-1].t < run.end:
            last = run.wizard_at(track[-1].t)
            lines.append(f"  gone untaken at {track[-1].t:.1f} s, {flat(last.at, track[-1].at):.0f} off")
        else:
            lines.append("  still there at the end")
        if pickup is None:
            full = nearest_while_full(run, track)
            if full is not None and full <= FULL_NEAR:
                lines.append(f"  a full wizard came within {full:.0f} and it stayed")
    lines.extend(_pickup_lines(found))
    return "\n".join(lines)


def _taken(p: Pickup) -> str:
    text = f"{p.kind} rose {p.rose:.0%} with it {p.off:.0f} off"
    if p.arrived is not None:
        text += f"; it flew in, coming to rest {p.rest:.0f} off {p.arrived:.1f} s after"
    if p.linger is not None:
        text += f", and left the list {p.linger:.1f} s after"
    return text


def _pickup_lines(found: list[Pickup]) -> list[str]:
    lines = []
    put = [p for p in found if p.wisp is not None]
    if put:
        offs = [p.off for p in put]
        lines.append(f"Pickups: {len(put)}, each wisp {min(offs):.0f}-{max(offs):.0f} off at the last sample before")
        lingers = [p.linger for p in put if p.linger is not None]
        if lingers:
            lines.append(f"  taken wisps lingered in the list {min(lingers):.1f}-{max(lingers):.1f} s")
    if unput := [p for p in found if p.wisp is None]:
        rises = ", ".join(f"{p.kind} at {p.t:.1f} s" for p in unput)
        lines.append(f"Rises with no wisp of the kind in sight (a potion?): {rises}")
    return lines


def read_csv(path: Path) -> Run:
    run = Run()
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            at = (float(row["x"]), float(row["y"]), float(row["z"]))
            if row["what"] == "zone":
                run.zone = row["name"]
            elif row["what"] == "wizard":
                run.wizard.append(Wizard(float(row["t"]), at, float(row["health"]), float(row["mana"])))
            else:
                run.sightings.append(Sighting(float(row["t"]), int(row["id"]), row["name"], at))
    return run


async def record(client, writer, run: Run, seconds: float, interval: float) -> None:
    listed: list[tuple[int, str, object]] = []
    listed_at = -math.inf
    started = time.monotonic()
    while (now := time.monotonic()) - started < seconds:
        t = round(now - started, 2)
        if now - listed_at >= LIST_REFRESH:
            listed = []
            for entity in await client.get_base_entities_with_vague_name("Wisp"):
                try:
                    template = await entity.object_template()
                    listed.append((await entity.global_id_full(), await template.object_name(), entity))
                except Exception:  # noqa: BLE001, S112 - freed while listing
                    continue
            listed_at = now
        pos = await client.body.position()
        health, mana = await client.calc_health_ratio(), await client.calc_mana_ratio()
        wizard = Wizard(t, (pos.x, pos.y, pos.z), health, mana)
        run.wizard.append(wizard)
        writer.writerow([f"{t:.2f}", "wizard", "", "", *wizard.at, f"{health:.4f}", f"{mana:.4f}"])
        seen = []
        for wisp, name, entity in listed:
            try:
                loc = await entity.location()
            except Exception:  # noqa: BLE001, S112 - freed since it was listed: taken, or despawned
                continue
            sighting = Sighting(t, wisp, name, (loc.x, loc.y, loc.z))
            seen.append(sighting)
            run.sightings.append(sighting)
            writer.writerow([f"{t:.2f}", "wisp", wisp, name, *sighting.at, "", ""])
        nearest = min((flat(wizard.at, s.at) for s in seen), default=None)
        status = f"{len(seen)} wisps, nearest {nearest:.0f}" if nearest is not None else "no wisp in sight"
        print(f"\r{t:6.1f} s  {status}  health {health:.0%} mana {mana:.0%}" + " " * 10, end="", flush=True)
        await asyncio.sleep(interval)


async def main(args: argparse.Namespace) -> int:
    if args.analyze:
        paths = [Path(p) for pattern in args.analyze for p in sorted(glob.glob(pattern))]
        if not paths:
            print(f"No CSV matches {args.analyze}")
            return 2
        for path in paths:
            print(f"== {path.name}")
            print(summarize(read_csv(path)))
        return 0

    csv_path = Path(__file__).with_name(f"probe_wisps.{time.strftime('%Y%m%d-%H%M%S')}.local.csv")
    from wizwalker.extensions.wizsprinter import WizSprinter  # noqa: PLC0415

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= args.client <= len(clients):
        print(f"No client {args.client}; have {len(clients)}.")
        return 2
    client = clients[args.client - 1]

    run = Run()
    try:
        await client.activate_hooks()
        run.zone = await client.zone_name()
        print(f"Zone: {run.zone}")
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)
            writer.writerow(["0", "zone", "", run.zone, 0, 0, 0, "", ""])
            try:
                await record(client, writer, run, args.seconds, args.interval)
            except asyncio.CancelledError:
                print("\n\nStopped by Ctrl+C.")
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:  # noqa: BLE001, S110 - closing on the way out; nothing to do about it
                pass

    print(f"\nSamples written to {csv_path}")
    print(summarize(run))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=120.0, help="How long to record (default 120).")
    parser.add_argument("--interval", type=float, default=SAMPLE_INTERVAL, help="Seconds between samples.")
    parser.add_argument("--client", type=int, default=1, help="Which client to read, 1-based (default 1).")
    parser.add_argument("--analyze", metavar="CSV", nargs="+", help="Summarize saved CSVs (globs allowed).")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
