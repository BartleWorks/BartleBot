"""Diagnostic: how do wisps move, how close must a wizard be to take one, and does a full one? (#343)

Collect Wisps walks to a wisp where it was when the walk was planned. Whether that is good
enough, or a walk must aim again, or ahead, depends on how wisps move, which nobody had measured
(Stage 4 of epic #340). This probe records it.

It watches one client: the wizard's own position and health and mana ratios, and the position of
every entity whose template name holds "Wisp", every --interval seconds. The list of wisps is read
again every LIST_REFRESH seconds, so a wisp that lingers in memory after it is taken shows as one
still listed. The summary says, per wisp:

  - how it moves: still (strayed under STILL from where it was first seen), loops (left its ground
    and came back to it, as a wisp on a path or an orbit would), heads one way (net travel over
    STRAIGHT of its path), or wanders; its median speed, how far it strayed on the ground, and how
    far up and down it went;
  - how it ended: still there at the end, taken (gone with the wizard within NEAR, and a ratio of
    its kind rose), or gone (with the wizard farther off: despawned, or out of render range);
  - for one taken, how far off the wizard stood at the last sample the wisp was seen, and at the
    first it was gone: the pickup radius lies between, less the lag;
  - for one a full wizard came near and did not take, the nearest it came while full.

It writes nothing to the game: it only reads. You walk, or stand; it watches.

HOW TO RUN
    1. Log a wizard in, near wisps (after a fight, or in a world's hub). Close BartleBot first:
       two programs hooking one client clash.
    2. Start it, for two minutes here:

         uv run --no-sync python tools/probe_wisps.py --seconds 120

    3. For how wisps move, stand still. For the pickup radius, a wizard below full on a wisp's
       kind takes one: stand on a moving wisp's way and let it come to you, since at its own speed
       it crosses the radius a few units a sample, or walk toward a still one slowly. For a full
       wizard, do the same at full: a wisp that passes or stays is in the summary.
    4. Ctrl+C stops early and prints the summary.

Each run writes its samples to its own tools/probe_wisps.<time>.local.csv (gitignored). Re-read one
without the game running:

         uv run --no-sync python tools/probe_wisps.py --analyze tools/probe_wisps.*.local.csv

Runs are not pooled: each file is summarized on its own, since a wisp id means nothing across runs.

Distances are 2D, as a walk measures them, unless a line says 3D.

Not in the plan: it samples every 0.1 s, not POLL_INTERVAL (0.25 s). A walking wizard covers about
100 units in 0.25 s, so the pickup radius would be bracketed no finer than that.

Wizard names and ids are never read or printed. Wisp template names and game ids are game data.
"""

from __future__ import annotations

import argparse
import asyncio
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
#: A wisp gone with the wizard within this was taken, if a ratio of its kind rose.
NEAR = 300.0
#: A ratio this close to 1 is full.
FULL = 0.999
#: How long after a wisp is gone a rise in its kind's ratio still counts as its.
RISE_WINDOW = 1.5

CSV_FIELDS = ["t", "what", "id", "name", "x", "y", "z", "health", "mana"]


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
        return min(self.wizard, key=lambda w: abs(w.t - t), default=None)


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
    first seen, the height it went through, and how long it was seen."""
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
class Ending:
    how: str
    seen_at: float | None = None
    gone_at: float | None = None
    rose: float | None = None


def ending(run: Run, track: list[Sighting]) -> Ending:
    """How *track*'s wisp ended: still there, taken (with the distances that bracket the pickup), or gone."""
    last = track[-1]
    after = [w for w in run.wizard if w.t > last.t]
    if not after:
        return Ending("still there")
    kind = kind_of(last.name)
    before = run.wizard_at(last.t)
    gone = after[0]
    seen_at = flat(before.at, last.at) if before else None
    gone_at = flat(gone.at, last.at)
    rose = _rise(run, kind, last.t)
    near = min(d for d in (seen_at, gone_at) if d is not None)
    if near <= NEAR and rose is not None:
        return Ending("taken", seen_at, gone_at, rose)
    return Ending("gone", seen_at, gone_at, rose)


def _rise(run: Run, kind: str, t: float) -> float | None:
    """How much *kind*'s ratio rose from the wizard's last sample at or before *t* to its highest
    within RISE_WINDOW after, or None if it did not."""
    before = [w for w in run.wizard if w.t <= t]
    window = [w for w in run.wizard if t < w.t <= t + RISE_WINDOW]
    if not before or not window:
        return None
    start = before[-1].ratio(kind)
    if start is None:
        return None
    rise = max(w.ratio(kind) for w in window) - start
    return rise if rise > 0 else None


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
    if not tracks:
        return f"No wisp seen in {run.end:.0f} s ({len(run.wizard)} wizard samples)."
    where = f" in {run.zone}" if run.zone else ""
    lines = [f"{len(tracks)} wisps{where} over {run.end:.0f} s, {len(run.wizard)} wizard samples"]
    radii: list[tuple[float, float]] = []
    for wisp, track in sorted(tracks.items(), key=lambda kv: kv[1][0].t):
        m = motion(track)
        end = ending(run, track)
        lines.append(
            f"{track[0].name} {wisp}: seen {track[0].t:.1f}-{track[-1].t:.1f} s ({len(track)} samples), {m.shape}, "
            f"speed {m.speed:.0f}/s, strayed {m.stray:.0f}, height range {m.rise:.0f}"
            + (f", lapped in {m.lap:.1f} s" if m.lap is not None else "")
        )
        if shared := sharers(tracks, wisp):
            lines.append(f"  its path is {shared} other wisps' too")
        if end.how == "taken":
            radii.append((end.seen_at, end.gone_at))
            lines.append(
                f"  taken: the wizard {end.seen_at:.0f} off at its last sighting, {end.gone_at:.0f} at the first "
                f"without it; {kind_of(track[0].name)} rose {end.rose:.0%}"
            )
        elif end.how == "gone":
            off = ", ".join(f"{d:.0f}" for d in (end.seen_at, end.gone_at) if d is not None)
            lines.append(f"  gone with the wizard {off} off" + (" (a ratio rose)" if end.rose else ""))
        else:
            lines.append("  still there at the end")
        full = nearest_while_full(run, track)
        if full is not None and full <= NEAR and end.how != "taken":
            lines.append(f"  a full wizard came within {full:.0f} and it stayed")
    if radii:
        seen = max(r[0] for r in radii)
        gone = min(r[1] for r in radii)
        lines.append(
            f"Pickups: {len(radii)}; the farthest last sighting {seen:.0f}, the nearest first absence {gone:.0f}"
        )
    return "\n".join(lines)


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
        t = now - started
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
        seen: dict[int, Sighting] = {}
        for wisp, name, entity in listed:
            try:
                loc = await entity.location()
            except Exception:  # noqa: BLE001, S112 - freed since it was listed: taken, or despawned
                continue
            sighting = Sighting(t, wisp, name, (loc.x, loc.y, loc.z))
            seen[wisp] = sighting
            run.sightings.append(sighting)
            writer.writerow([f"{t:.2f}", "wisp", wisp, name, *sighting.at, "", ""])
        nearest = min((flat(wizard.at, s.at) for s in seen.values()), default=None)
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
