"""Diagnostic: how close can a wizard get to a mob before a battle triggers? (#117)

collect_wisps' "only safe" check rejects a wisp within 700 units of a mob, and wisps it
passed have still pulled the wizard into a fight. Deimos uses 2000. This probe measures
the real trigger distance, so the radius can be picked from evidence.

OUTCOME: eleven battles across five mob types, ten usable. Still or slow mobs triggered
at 270-390 units; mobs closing at 150-400/s triggered at 650-886, which fits a fixed
server-side radius plus client lag. 700 did not cover the 886 measured, so collect_wisps
now checks against node_helpers.SAFE_RADIUS of 1500, rescanned every hop. The per-trial
table is on #117. The phased design there (invulnerable hops in the ghost window, a
landing spot) was not built: most uses of the node leave the spot straight after, and
the radius alone fails safe. Keep the probe for re-measuring if wisps judged safe still
pull the group into fights.

What the game does, as far as this probe relies on it:

  - A battle triggers on proximity: a wizard who comes too close to a mob is pulled in.
    The radius is about the same for every mob, and it counts height.
  - At the trigger the wizard loses control and runs to a duel circle seat, in a straight
    line, at whatever speed gets it there in a set time. in_battle() only turns true some
    time later, and the run alone is hard to pick out of a live stream. So a hotkey marks
    roughly when, and the analysis then finds the run itself: the unbroken stretch of steps
    heading straight for the seat the wizard ends up in. The last sample before that
    stretch is the last one before the trigger. When no such stretch is found the mark,
    less --reaction, stands in for it.
  - The client ticks slowly. A mob walking toward the wizard is closer on the server
    than it looks here, so a trigger against an approaching mob reads long. The summary
    splits approaching mobs from the rest for that reason.
  - For 4-6s after a fight the survivors cannot be pulled in (the ghost window). A mark
    inside it is not a trigger and is set aside.

HOW TO RUN
    1. Log a wizard in, in a zone with mobs.
    2. Start the probe:

         uv run python tools/probe_aggro_radius.py

    3. Walk toward mobs until a battle triggers. The moment your wizard starts running to
       the circle on its own, press F9 (--key). Finish the fight, wait out the ghost window,
       and repeat. Vary it: walk into a still mob, stand in a walking mob's path, come from
       above or below.
    4. Ctrl+C prints the summary.

Each run writes its samples to its own tools/probe_aggro_radius.<time>.local.csv
(gitignored). Re-read one or several, pooled, without the game running:

         uv run python tools/probe_aggro_radius.py --analyse tools/probe_aggro_radius.*.local.csv

Distances are 3D unless labeled 2D. WizWalker's XYZ.distance, which the safety check in
WizSprinter uses, is 2D: it ignores height.

Wizard names and ids are never read or printed. Mob template names are game data and are.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import math
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_INTERVAL = 0.25
KEY_POLL = 0.02
# get_mobs() reads every entity's behaviors, so the mob list is refreshed rarely and
# only positions are read every sample.
MOB_REFRESH = 2.0
NEAREST = 3
STATUS_INTERVAL = 1.0
# A mark is matched to a battle whose in_battle() turned true this long after it...
MARK_BEFORE_BATTLE = 10.0
# ...or this long before it, in case the key went down after in_battle() did.
MARK_AFTER_BATTLE = 2.0
GHOST_WINDOW = 6.0
# The span used for a mob's velocity, and the trace printed before each trigger.
VELOCITY_SPAN = 0.5
TRACE_SPAN = 2.0
# The trace also shows this much after the trigger, so the start of the run is visible.
TRACE_AFTER = 0.5
# A mob closing on the wizard faster than this counts as approaching.
APPROACH_SPEED = 25.0
# Finding the forced run. It is looked for in the samples this long before the mark...
ONSET_LOOKBACK = 3.0
# ...as steps longer than this, heading within this many degrees of the seat...
RUN_MIN_STEP = 5.0
RUN_HEADING = 20.0
# ...until the wizard is within this of the seat, where it stands still.
SEATED = 15.0
DEFAULT_REACTION = 0.3
CURRENT_RADIUS = 700.0
DEIMOS_RADIUS = 2000.0
# Runs are pooled across files, so each file's clock is shifted clear of the one before.
RUN_GAP = 1000.0
VK_F1 = 0x70


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    z: float

    def dist3(self, other: Point) -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))

    def dist2(self, other: Point) -> float:
        return math.dist((self.x, self.y), (other.x, other.y))


@dataclass(frozen=True)
class MobPos:
    gid: int
    name: str
    pos: Point


@dataclass
class Sample:
    t: float
    player: Point
    mobs: list[MobPos]


@dataclass
class Events:
    marks: list[float] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Trial:
    trigger_t: float  # the last sample time before the trigger, or the best stand-in for it
    # "run": the forced run was found and trigger_t is the sample before it.
    # "hotkey": no run was found, so trigger_t is the mark less the reaction time.
    # "in_battle": no mark matched, so trigger_t is when in_battle() turned true (late).
    source: str
    battle_start: float
    mark: float | None = None


@dataclass(frozen=True)
class TrialResult:
    trial: Trial
    sample_t: float
    mob: str
    dist3: float
    dist2: float
    dz: float
    approach_speed: float | None  # the mob's own velocity toward the wizard, units/s
    trace: list[tuple[float, float]]  # (seconds relative to trigger, dist3)

    @property
    def approaching(self) -> bool:
        return self.approach_speed is not None and self.approach_speed > APPROACH_SPEED


# ---------------------------------------------------------------------------
# Analysis (pure)
# ---------------------------------------------------------------------------


def build_trials(
    events: Events, reaction: float, samples: list[Sample] | None = None
) -> tuple[list[Trial], list[tuple[float, str]]]:
    """Pair each battle with the mark that triggered it, and place the trigger.

    With samples, a marked trigger is placed at the start of the forced run where one is
    found. Returns the trials and the marks set aside, each with the reason.
    """
    claimed: set[int] = set()
    trials = []
    for start in events.starts:
        candidates = [
            i
            for i, m in enumerate(events.marks)
            if i not in claimed and start - MARK_BEFORE_BATTLE <= m <= start + MARK_AFTER_BATTLE
        ]
        if candidates:
            i = max(candidates, key=lambda i: events.marks[i])
            claimed.add(i)
            mark = events.marks[i]
            onset = find_run_onset(samples, mark, start) if samples else None
            if onset is not None:
                trials.append(Trial(onset, "run", start, mark))
            else:
                trials.append(Trial(mark - reaction, "hotkey", start, mark))
        else:
            trials.append(Trial(start, "in_battle", start))

    discarded = []
    for i, mark in enumerate(events.marks):
        if i in claimed:
            continue
        if any(end <= mark < end + GHOST_WINDOW for end in events.ends):
            discarded.append((mark, "inside the ghost window after a fight"))
        elif any(s <= mark < e for s, e in _battle_spans(events)):
            discarded.append((mark, "pressed during a fight"))
        else:
            discarded.append((mark, "no battle followed"))
    return trials, discarded


def _battle_spans(events: Events) -> list[tuple[float, float]]:
    spans = []
    for start in events.starts:
        end = next((e for e in sorted(events.ends) if e > start), math.inf)
        spans.append((start, end))
    return spans


def _heads_for_seat(a: Point, b: Point, seat: Point) -> bool:
    """Whether the step a -> b is part of the forced run to *seat*, or the rest at its end."""
    if a.dist2(seat) <= SEATED and b.dist2(seat) <= SEATED:
        return True
    step = (b.x - a.x, b.y - a.y)
    to_seat = (seat.x - a.x, seat.y - a.y)
    if math.hypot(*step) < RUN_MIN_STEP or math.hypot(*to_seat) < RUN_MIN_STEP:
        return False
    angle = math.atan2(step[1], step[0]) - math.atan2(to_seat[1], to_seat[0])
    return abs(math.degrees(math.atan2(math.sin(angle), math.cos(angle)))) <= RUN_HEADING


def find_run_onset(samples: list[Sample], mark: float, battle_start: float) -> float | None:
    """The time of the last sample before the forced run, or None if no run is found.

    The seat is where the wizard stands when in_battle() turns true. Working back from
    there, every step of the run heads straight for it; the first step that does not is
    the wizard's own movement, and the sample at its end is the last before the trigger.
    None when the run reaches back past the lookback (it cannot be told from a walk that
    already headed that way), when there was no run, or when it starts after the mark.
    """
    upto = [s for s in samples if s.t <= battle_start]
    window = [s for s in upto if s.t >= mark - ONSET_LOOKBACK]
    if len(window) < 2:
        return None
    seat = upto[-1].player
    k = len(window) - 1
    while k > 0 and _heads_for_seat(window[k - 1].player, window[k].player, seat):
        k -= 1
    if k == 0 or window[k].player.dist2(seat) <= SEATED or window[k].t > mark:
        return None
    return window[k].t


def _find(sample: Sample, gid: int) -> MobPos | None:
    return next((m for m in sample.mobs if m.gid == gid), None)


def analyse_trial(samples: list[Sample], trial: Trial) -> TrialResult | None:
    """The mob nearest the wizard at the trigger, how far it was, and how it was moving."""
    before = [s for s in samples if s.t <= trial.trigger_t]
    if not before:
        return None
    at = before[-1]
    if not at.mobs:
        return None
    mob = min(at.mobs, key=lambda m: m.pos.dist3(at.player))

    approach = None
    earlier = next((s for s in reversed(before) if s.t <= at.t - VELOCITY_SPAN and _find(s, mob.gid)), None)
    if earlier is not None:
        then = _find(earlier, mob.gid).pos
        dt = at.t - earlier.t
        vx, vy, vz = (mob.pos.x - then.x) / dt, (mob.pos.y - then.y) / dt, (mob.pos.z - then.z) / dt
        to_player = (at.player.x - mob.pos.x, at.player.y - mob.pos.y, at.player.z - mob.pos.z)
        length = math.hypot(*to_player)
        if length:
            approach = (vx * to_player[0] + vy * to_player[1] + vz * to_player[2]) / length

    trace = []
    for s in samples:
        if not trial.trigger_t - TRACE_SPAN <= s.t <= trial.trigger_t + TRACE_AFTER:
            continue
        if (m := _find(s, mob.gid)) is not None:
            trace.append((s.t - trial.trigger_t, m.pos.dist3(s.player)))

    return TrialResult(
        trial=trial,
        sample_t=at.t,
        mob=mob.name,
        dist3=mob.pos.dist3(at.player),
        dist2=mob.pos.dist2(at.player),
        dz=mob.pos.z - at.player.z,
        approach_speed=approach,
        trace=trace,
    )


def _stats(values: list[float]) -> str:
    if not values:
        return "no trials"
    return (
        f"n={len(values):<3} min {min(values):7.1f}   median {statistics.median(values):7.1f}   max {max(values):7.1f}"
    )


def summarize(samples: list[Sample], events: Events, reaction: float) -> str:
    trials, discarded = build_trials(events, reaction, samples)
    lines = ["=" * 78, "SUMMARY", f"  {len(samples)} samples, {len(trials)} battle(s)"]

    results = []
    for n, trial in enumerate(trials, start=1):
        result = analyse_trial(samples, trial)
        lines.append("")
        if result is None:
            lines.append(f"  battle {n} at t={trial.battle_start:.1f}s: no mob sample before the trigger")
            continue
        results.append(result)
        speed = "?" if result.approach_speed is None else f"{result.approach_speed:+.0f}/s"
        how = {
            "run": f"sample before the forced run, {(trial.mark or 0) - trial.trigger_t:.2f}s before the mark",
            "hotkey": f"NO RUN FOUND: mark less {reaction:.2f}s reaction",
            "in_battle": "LATE: no mark, in_battle() start",
        }[trial.source]
        lines.append(
            f"  battle {n} at t={trial.battle_start:.1f}s: {result.mob!r} {result.dist3:.1f} "
            f"(2D {result.dist2:.1f}, dz {result.dz:+.1f}), mob approaching at {speed}"
        )
        lines.append(f"    trigger: {how}")
        lines.append("    trace: " + "  ".join(f"{dt:+.2f}s {d:.0f}" for dt, d in result.trace))

    for mark, reason in discarded:
        lines.append(f"  mark at t={mark:.1f}s set aside: {reason}")

    marked = [r for r in results if r.trial.source == "run"]
    lines += [
        "",
        "  Triggers placed by the forced run, 3D distance:",
        f"    all                  {_stats([r.dist3 for r in marked])}",
        f"    mob approaching      {_stats([r.dist3 for r in marked if r.approaching])}",
        f"    mob still or leaving {_stats([r.dist3 for r in marked if not r.approaching])}",
        f"    all, 2D              {_stats([r.dist2 for r in marked])}",
    ]
    if guessed := [r for r in results if r.trial.source == "hotkey"]:
        lines.append(f"  No run found (mark less reaction) {_stats([r.dist3 for r in guessed])}")
    if late := [r for r in results if r.trial.source == "in_battle"]:
        lines.append(f"  Unmarked (late) triggers          {_stats([r.dist3 for r in late])}")
    if marked:
        worst = max(r.dist3 for r in marked)
        lines.append("")
        for label, radius in (("current", CURRENT_RADIUS), ("Deimos", DEIMOS_RADIUS)):
            verdict = "covers" if radius > worst else "does NOT cover"
            lines.append(f"  {label} radius {radius:.0f} {verdict} the farthest trigger ({worst:.1f})")
    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

CSV_FIELDS = ("t", "kind", "gid", "name", "x", "y", "z")


def write_sample(writer, sample: Sample) -> None:
    p = sample.player
    writer.writerow([f"{sample.t:.3f}", "player", "", "", p.x, p.y, p.z])
    for m in sample.mobs:
        writer.writerow([f"{sample.t:.3f}", "mob", m.gid, m.name, m.pos.x, m.pos.y, m.pos.z])


def write_event(writer, t: float, kind: str) -> None:
    writer.writerow([f"{t:.3f}", kind, "", "", "", "", ""])


def read_csvs(paths: list[Path]) -> tuple[list[Sample], Events]:
    """Several runs pooled into one timeline, each shifted to start after the last ends."""
    samples: list[Sample] = []
    events = Events()
    offset = 0.0
    for path in paths:
        run_samples, run_events = read_csv(path)
        for s in run_samples:
            s.t += offset
        samples += run_samples
        events.marks += [t + offset for t in run_events.marks]
        events.starts += [t + offset for t in run_events.starts]
        events.ends += [t + offset for t in run_events.ends]
        ends = [s.t for s in run_samples] + events.marks + events.starts + events.ends
        offset = max(ends, default=offset) + RUN_GAP
    return samples, events


def read_csv(path: Path) -> tuple[list[Sample], Events]:
    samples: list[Sample] = []
    events = Events()
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            t = float(row["t"])
            kind = row["kind"]
            if kind == "player":
                samples.append(Sample(t, Point(float(row["x"]), float(row["y"]), float(row["z"])), []))
            elif kind == "mob":
                pos = Point(float(row["x"]), float(row["y"]), float(row["z"]))
                samples[-1].mobs.append(MobPos(int(row["gid"]), row["name"], pos))
            elif kind == "mark":
                events.marks.append(t)
            elif kind == "battle_start":
                events.starts.append(t)
            elif kind == "battle_end":
                events.ends.append(t)
    return samples, events


# ---------------------------------------------------------------------------
# Live recording
# ---------------------------------------------------------------------------


def _vk(key: str) -> int:
    key = key.upper()
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return VK_F1 + int(key[1:]) - 1
    raise ValueError(f"--key must be F1-F12, got {key!r}")


def _watch_key(vk: int, marks: list[float], stop: threading.Event) -> None:
    """Record the time of each key press. A thread, so a slow memory read cannot delay a mark."""
    import ctypes

    user32 = ctypes.windll.user32
    down = False
    while not stop.is_set():
        pressed = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        if pressed and not down:
            marks.append(time.monotonic())
        down = pressed
        time.sleep(KEY_POLL)


async def _safe(coro):
    """The awaited value, or None if the read raised. Every read here is speculative."""
    try:
        return await coro
    except Exception:
        return None


def _point(xyz) -> Point:
    return Point(xyz.x, xyz.y, xyz.z)


async def _refresh_mobs(client) -> dict[int, tuple[object, str]]:
    mobs = {}
    for entity in await _safe(client.get_mobs()) or []:
        gid = await _safe(entity.global_id_full())
        if gid is not None:
            mobs[gid] = (entity, await _safe(entity.object_name()) or "?")
    return mobs


async def _take_sample(client, mobs: dict[int, tuple[object, str]], t: float) -> Sample | None:
    player = await _safe(client.body.position())
    if player is None:
        return None
    player = _point(player)
    found = []
    for gid, (entity, name) in mobs.items():
        if (loc := await _safe(entity.location())) is not None:
            found.append(MobPos(gid, name, _point(loc)))
    found.sort(key=lambda m: m.pos.dist3(player))
    return Sample(t, player, found[:NEAREST])


async def record(client, args, writer, samples: list[Sample], events: Events) -> None:
    t0 = time.monotonic()
    raw_marks: list[float] = []
    stop = threading.Event()
    threading.Thread(target=_watch_key, args=(_vk(args.key), raw_marks, stop), daemon=True).start()

    started = time.monotonic()
    mobs = await _refresh_mobs(client)
    print(f"{len(mobs)} mob(s) in range; a mob scan takes {time.monotonic() - started:.2f}s.")
    print(f"Recording. Press {args.key.upper()} the moment a battle triggers; Ctrl+C to finish.\n")

    last_refresh = time.monotonic()
    last_status = 0.0
    in_battle = bool(await _safe(client.in_battle()))
    seen_marks = 0
    next_tick = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if now - last_refresh >= MOB_REFRESH:
                mobs = await _refresh_mobs(client)
                last_refresh = time.monotonic()

            sample = await _take_sample(client, mobs, time.monotonic() - t0)
            if sample is not None:
                samples.append(sample)
                write_sample(writer, sample)

            battle = bool(await _safe(client.in_battle()))
            if battle != in_battle:
                t = time.monotonic() - t0
                (events.starts if battle else events.ends).append(t)
                write_event(writer, t, "battle_start" if battle else "battle_end")
                print(f"\n  t={t:7.2f}s  in_battle() -> {battle}" + ("" if battle else "  (ghost window starts)"))
                in_battle = battle

            while seen_marks < len(raw_marks):
                t = raw_marks[seen_marks] - t0
                seen_marks += 1
                events.marks.append(t)
                write_event(writer, t, "mark")
                nearest = samples[-1].mobs[0] if samples and samples[-1].mobs else None
                where = f"nearest {nearest.name!r} {nearest.pos.dist3(samples[-1].player):.1f}" if nearest else "no mob"
                print(f"\n  t={t:7.2f}s  MARK #{len(events.marks)}  {where}")

            if now - last_status >= STATUS_INTERVAL and sample is not None:
                last_status = now
                ghost = [e for e in events.ends if sample.t - e < GHOST_WINDOW]
                state = (
                    "IN BATTLE"
                    if in_battle
                    else (f"ghost {GHOST_WINDOW - (sample.t - ghost[-1]):.1f}s" if ghost else "")
                )
                mob = sample.mobs[0] if sample.mobs else None
                near = (
                    f"{mob.name[:28]:<28} {mob.pos.dist3(sample.player):7.1f} (2D {mob.pos.dist2(sample.player):7.1f})"
                    if mob
                    else "no mob"
                )
                print(f"\r  t={sample.t:7.1f}s  nearest {near}  {state:<12}", end="", flush=True)

            next_tick += SAMPLE_INTERVAL
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
    finally:
        stop.set()


async def main(args: argparse.Namespace) -> int:
    if args.analyse:
        paths = [Path(p) for pattern in args.analyse for p in sorted(glob.glob(pattern))]
        if not paths:
            print(f"No CSV matches {args.analyse}")
            return 2
        print("Pooling " + ", ".join(p.name for p in paths))
        samples, events = read_csvs(paths)
        print(summarize(samples, events, args.reaction))
        return 0

    csv_path = (
        Path(args.csv)
        if args.csv
        else Path(__file__).with_name(f"probe_aggro_radius.{time.strftime('%Y%m%d-%H%M%S')}.local.csv")
    )
    if not csv_path.name.endswith(".local.csv"):
        print(f"--csv must end in .local.csv so git ignores it; got {csv_path.name!r}")
        return 2

    from wizwalker.extensions.wizsprinter import WizSprinter

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= args.client <= len(clients):
        print(f"No client {args.client}; have {len(clients)}.")
        return 2
    client = clients[args.client - 1]
    print(f"Found {len(clients)} client(s); using client {args.client} (hwnd={client.window_handle}).")

    samples: list[Sample] = []
    events = Events()
    try:
        await client.activate_hooks()
        with csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)
            try:
                await record(client, args, writer, samples, events)
            except asyncio.CancelledError:
                # Ctrl+C under asyncio.run cancels this task; swallowing it still prints the summary.
                print("\n\nStopped by Ctrl+C.")
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass

    print(f"Samples written to {csv_path}")
    print(summarize(samples, events, args.reaction))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", default="F9", help="Hotkey that marks a trigger, F1-F12 (default F9).")
    parser.add_argument("--client", type=int, default=1, help="Which client to record, 1-based (default 1).")
    parser.add_argument(
        "--csv", help="Where to write samples; must end in .local.csv (default: a new timestamped file in tools/)."
    )
    parser.add_argument(
        "--reaction",
        type=float,
        default=DEFAULT_REACTION,
        help=f"Seconds subtracted from a mark when no forced run is found (default {DEFAULT_REACTION}).",
    )
    parser.add_argument(
        "--analyse",
        metavar="CSV",
        nargs="+",
        help="Summarize saved CSVs (globs allowed), pooled, instead of recording.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
