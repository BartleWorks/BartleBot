"""Diagnostic: when does one of our clients have another loaded, and what does it prove?

Deimos's ``same_place`` check (``deimoslang/vm.py``) calls clients "together" when each one's
entity list holds every other one's ``global_id_full()``. A client only loads players in its
own zone and realm, and only nearby, so:

  - seeing another client PROVES the two share a zone, a realm and an instance;
  - not seeing it proves nothing: another realm, too far away, or not loaded yet.

That makes it the cheap realm check BartleBot lacks (realm is otherwise read by opening the
spellbook), with uses in ambient-combat help (#116), port arrival, and Player Sync.

OUTCOME (one four-client run, 2026-09-29, Wizard City hub, Ravenwood and Unicorn Way; one
client on a separate realm, a fight, doors, and friends-list ports):

  - RADIUS has hysteresis. Another client loads when it comes within about 3850-4300 units
    and unloads beyond about 4400-5900. Everything within ~3800 was seen, unless the realm
    differed. Nothing within 1200 was ever unseen on a shared realm.
  - REALM is caught exactly. The client on the separate realm was never seen, even at
    1200-1500 units, by anyone on the main realm. A friends-list port moves the porting
    client onto the target's realm, and a zone door keeps the client's own realm. That is
    why the whole group lost and regained sight of P1 as P4 crossed between realms.
  - SYMMETRY is near-perfect. 15 of 820 two-way readings disagreed, all between 4965 and
    5457, inside the unload band. Up close, one direction is enough.
  - BATTLE: a nearby client in battle was seen 38/38 times.
  - ARRIVAL: after a port or zone change, the others saw the arrival, and it saw them, 1-2s
    BEFORE its loading screen ended. A teleport within the zone was seen on landing. A
    leaver drops out of the others' lists about 1s into its loading screen, and a loading
    client's own list is empty.
  - COST: ~5ms median and 13ms at worst per check, with 80-96 entities. No check failed.
    A client's own id was missing from its own list only while it was loading.
  - GID STABILITY: no client's id changed, across doors, ports and a fight.

So "seen" proves same zone, realm and instance, and within ~3800 units "not seen" (once
nobody is loading) proves a different realm. A group that confirmed itself together stays
on one realm until someone passes through a loading screen.

Before building on it, this probe measures the four things the design depends on:

  1. RADIUS. How far apart two clients on the same realm can be and still see each other,
     and whether loading and unloading happen at different distances. Believed ~4142.
  2. BATTLE. Whether a client in battle stays visible to one outside it. Believed yes.
  3. ARRIVAL. How long after a port or zone change the others see the new arrival, and the
     arrival sees them. Believed under 5s, sometimes before the loading screen ends.
  4. COST. How long one check takes per client in a busy zone. WizWalker's
     get_base_entity_list() retries internally for up to 3s on a race, so failures are
     counted too.

It also reports two things a design would otherwise assume:

  - SYMMETRY. Whether A sees B exactly when B sees A, so one direction could be enough.
  - GID STABILITY. Whether a client's own global id changes, for example on a zone change,
    which decides whether an id can be read once and remembered.

HOW TO RUN

    CLOSE BARTLEBOT FIRST. This probe hooks every client itself, and two sets of hooks in one
    game process corrupt each other (see the note in tools/probe_character_gid.py).

    1. Log in two or more clients, all on the SAME realm, in one zone.
    2. uv run python tools/probe_same_place.py
    3. Work through these, in any order; the analysis finds each in the recording:
         a. Radius: leave one client still and walk another away in a straight line until
            the status line shows it unseen, then walk back. Repeat in a small zone and in a
            large open one.
         b. Realm: hop one client to another realm, then stand the two together for ~10s.
         c. Battle: start a fight with one client while another watches from close by.
         d. Arrival: port a client to another through the friends list, from another zone
            and from the same zone; also have one change zone through a door and back.
    4. Ctrl+C prints the summary.

Each run writes its readings to its own tools/probe_same_place.<time>.local.csv
(gitignored). Re-read one or several without the game running:

    uv run python tools/probe_same_place.py --analyse tools/probe_same_place.*.local.csv

NO REAL PLAYER DATA: global ids identify characters, so they are never printed or written.
Clients are named by their slot (P1, P2...). The CSV holds zones, positions, and which
slots each client saw.
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
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SAMPLE_INTERVAL = 0.5
STATUS_INTERVAL = 1.0
# Pair observations closer than this are "near": used for the battle split and to flag a
# pair that never saw each other up close, which suggests different realms. Well inside the
# believed radius, so being out of range cannot explain an unseen pair.
DEFAULT_NEAR = 2000.0
# Two readings of one pair further apart in time than this do not form a crossing: whatever
# happened in between went unrecorded.
MAX_GAP = 2.0
# A position change larger than this between two readings, with no loading screen, is a
# teleport or a same-zone port rather than walking.
JUMP = 400.0
# How long after an arrival to look for the others to appear.
ARRIVAL_WINDOW = 30.0
# Runs are pooled across files, so each file's clock is shifted clear of the one before.
RUN_GAP = 1000.0


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    z: float

    def dist(self, other: Point) -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


@dataclass(frozen=True)
class Reading:
    """One client at one tick. Every client in a tick shares its ``t``."""

    t: float
    slot: int  # 1-based
    zone: str | None
    pos: Point | None
    loading: bool
    in_battle: bool
    # Goes up by one each time this client's own global id reads differently. The id itself
    # is never kept.
    gid_seq: int
    # None when the entity-list check failed this tick.
    self_seen: bool | None
    seen: frozenset[int] | None  # the other slots this client had loaded
    check_s: float | None
    entities: int | None
    error: str = ""

    @property
    def checked(self) -> bool:
        return self.seen is not None


@dataclass(frozen=True)
class PairObs:
    """What observer ``a`` knew about ``b`` at one tick, when the answer means something:
    both settled in the same zone, both positions readable, and a's check succeeded."""

    t: float
    a: int
    b: int
    zone: str
    dist: float
    seen: bool
    a_battle: bool
    b_battle: bool


@dataclass(frozen=True)
class Crossing:
    """A pair's seen flag changing between two consecutive observations."""

    a: int
    b: int
    t: float
    kind: str  # "appear" or "vanish"
    dist_before: float
    dist_after: float


@dataclass(frozen=True)
class Arrival:
    """A client landing somewhere: a loading screen ending, or a jump in position."""

    slot: int
    t: float  # the first settled reading after it
    start: float  # when the loading screen began; == t for a jump
    kind: str  # "load" or "jump"
    zone: str | None


@dataclass(frozen=True)
class ArrivalView:
    """When one other client appeared relative to an arrival, in one direction."""

    arrival: Arrival
    other: int
    direction: str  # "others see arriver" or "arriver sees others"
    # Seconds from the arrival's settled reading to the first appearance; negative when the
    # appearance came while the loading screen was still up. None when no appearance was
    # seen in the window.
    lag: float | None
    # True when the pair saw each other continuously through the arrival, so there was no
    # appearance to time.
    never_lost: bool = False
    # How far apart the two were at the arrival's settled reading, or None if unreadable.
    # A landing out of range says nothing about lag, so the summary only times near ones.
    dist: float | None = None


# ---------------------------------------------------------------------------
# Analysis (pure)
# ---------------------------------------------------------------------------


def group_ticks(readings: list[Reading]) -> list[dict[int, Reading]]:
    """Readings grouped into ticks, in time order, each keyed by slot."""
    ticks: dict[float, dict[int, Reading]] = defaultdict(dict)
    for r in readings:
        ticks[r.t][r.slot] = r
    return [ticks[t] for t in sorted(ticks)]


def pair_observations(ticks: list[dict[int, Reading]]) -> list[PairObs]:
    out = []
    for tick in ticks:
        for a, ra in tick.items():
            if not ra.checked or ra.loading or ra.zone is None or ra.pos is None:
                continue
            for b, rb in tick.items():
                if b == a or rb.loading or rb.zone != ra.zone or rb.pos is None:
                    continue
                out.append(PairObs(ra.t, a, b, ra.zone, ra.pos.dist(rb.pos), b in ra.seen, ra.in_battle, rb.in_battle))
    return out


def _by_pair(obs: list[PairObs]) -> dict[tuple[int, int], list[PairObs]]:
    pairs: dict[tuple[int, int], list[PairObs]] = defaultdict(list)
    for o in sorted(obs, key=lambda o: o.t):
        pairs[(o.a, o.b)].append(o)
    return pairs


def find_crossings(obs: list[PairObs]) -> list[Crossing]:
    """Every appear and vanish, with the distance either side of it.

    Only pairs of consecutive observations in the same zone and within MAX_GAP count, so a
    loading screen or a failed check in between does not read as a crossing.
    """
    out = []
    for (a, b), series in _by_pair(obs).items():
        for prev, cur in zip(series, series[1:]):
            if prev.seen == cur.seen or cur.t - prev.t > MAX_GAP or prev.zone != cur.zone:
                continue
            out.append(Crossing(a, b, cur.t, "appear" if cur.seen else "vanish", prev.dist, cur.dist))
    return sorted(out, key=lambda c: c.t)


def never_seen_close(obs: list[PairObs], near: float) -> list[tuple[int, int, str, float, int]]:
    """(a, b, zone, closest distance, observations) for each pair that came within *near* in
    a zone and was never once seen there. Different realms is the likely reason."""
    groups: dict[tuple[int, int, str], list[PairObs]] = defaultdict(list)
    for o in obs:
        groups[(o.a, o.b, o.zone)].append(o)
    out = []
    for (a, b, zone), series in sorted(groups.items()):
        close = [o for o in series if o.dist <= near]
        if close and not any(o.seen for o in series):
            out.append((a, b, zone, min(o.dist for o in close), len(close)))
    return out


def asymmetries(obs: list[PairObs]) -> tuple[int, list[tuple[float, int, int, float]]]:
    """(ticks where both directions of a pair were observed, examples where they disagreed).

    Each example is (t, the slot that saw, the slot that did not, distance).
    """
    index = {(o.t, o.a, o.b): o for o in obs}
    both = 0
    disagree = []
    for (t, a, b), o in index.items():
        if a > b or (back := index.get((t, b, a))) is None:
            continue
        both += 1
        if o.seen != back.seen:
            seer, blind = (a, b) if o.seen else (b, a)
            disagree.append((t, seer, blind, o.dist))
    return both, sorted(disagree)


def battle_split(obs: list[PairObs], near: float) -> dict[bool, tuple[int, int]]:
    """{b in battle: (seen, total)} over near observations where the observer is not in battle."""
    out = {True: (0, 0), False: (0, 0)}
    for o in obs:
        if o.dist > near or o.a_battle:
            continue
        seen, total = out[o.b_battle]
        out[o.b_battle] = (seen + o.seen, total + 1)
    return out


def find_arrivals(ticks: list[dict[int, Reading]]) -> list[Arrival]:
    """Each loading screen that ended, and each settled jump in position, per client."""
    series: dict[int, list[Reading]] = defaultdict(list)
    for tick in ticks:
        for slot, r in tick.items():
            series[slot].append(r)
    out = []
    for slot, readings in series.items():
        load_start = None
        for i, (prev, cur) in enumerate(zip(readings, readings[1:]), start=1):
            if cur.loading and not prev.loading:
                load_start = cur.t
            elif prev.loading and not cur.loading:
                out.append(Arrival(slot, cur.t, load_start if load_start is not None else prev.t, "load", cur.zone))
                load_start = None
            elif _step(prev, cur) > JUMP and _step_at(readings, i - 1) <= JUMP / 2 >= _step_at(readings, i + 1):
                # Isolated: a mount covers JUMP in a tick too, but it keeps doing so.
                out.append(Arrival(slot, cur.t, cur.t, "jump", cur.zone))
    return sorted(out, key=lambda a: a.t)


def _step(prev: Reading, cur: Reading) -> float:
    """How far a settled client moved within one zone between two readings; 0 otherwise."""
    if prev.loading or cur.loading or prev.zone != cur.zone or prev.pos is None or cur.pos is None:
        return 0.0
    return prev.pos.dist(cur.pos)


def _step_at(readings: list[Reading], i: int) -> float:
    """The step into readings[i], or 0 at either end of the series."""
    return _step(readings[i - 1], readings[i]) if 0 < i < len(readings) else 0.0


def _sees(tick: dict[int, Reading], observer: int, target: int) -> bool | None:
    r = tick.get(observer)
    if r is None or not r.checked:
        return None
    return target in r.seen


def arrival_views(ticks: list[dict[int, Reading]], arrivals: list[Arrival]) -> list[ArrivalView]:
    """For each arrival and each other client that ended up in its zone, when they appeared.

    "Appeared" is the first reading, from the moment the loading screen began, where the
    observer has the other loaded after a reading where it did not. That skips a stale entry
    left over from before the port. A pair loaded throughout is reported as never lost.
    """
    out = []
    for arrival in arrivals:
        # The window ends when the arriver moves on, or the next visit's appearance is
        # credited to this one.
        leaves = next((a.start for a in arrivals if a.slot == arrival.slot and a.start > arrival.t), math.inf)
        end = min(arrival.t + ARRIVAL_WINDOW, leaves)
        window = [tick for tick in ticks if arrival.start <= _tick_t(tick) < end]
        settled = next((tick for tick in window if _tick_t(tick) >= arrival.t), None)
        if settled is None:
            continue
        others = [s for s, r in settled.items() if s != arrival.slot and r.zone == arrival.zone and not r.loading]
        here = settled[arrival.slot].pos
        for other in others:
            there = settled[other].pos
            dist = here.dist(there) if here is not None and there is not None else None
            for direction, observer, target in (
                ("others see arriver", other, arrival.slot),
                ("arriver sees others", arrival.slot, other),
            ):
                flags = [(_tick_t(tick), _sees(tick, observer, target)) for tick in window]
                flags = [(t, f) for t, f in flags if f is not None]
                if arrival.kind == "jump":
                    # Within one zone the entity is the same object before and after, so there
                    # is no stale entry to skip: seen on landing is a lag of zero.
                    appear = next((t for t, f in flags if f), None)
                    out.append(
                        ArrivalView(
                            arrival, other, direction, None if appear is None else appear - arrival.t, dist=dist
                        )
                    )
                    continue
                if flags and all(f for _, f in flags):
                    out.append(ArrivalView(arrival, other, direction, None, never_lost=True, dist=dist))
                    continue
                appear = next(
                    (t for (_, was), (t, now) in zip(flags, flags[1:]) if now and not was),
                    None,
                )
                lag = None if appear is None else appear - arrival.t
                out.append(ArrivalView(arrival, other, direction, lag, dist=dist))
    return out


def near_misses(
    obs: list[PairObs], arrivals: list[Arrival], near: float, excluded: set[tuple[int, int]]
) -> list[tuple[float, int, int, float, float | None]]:
    """Every near observation that was not seen, outside the *excluded* pairs.

    Each is (t, observer, target, distance, seconds since either one last arrived). A miss
    shortly after an arrival is loading lag; one long after it is something the design has
    to know about.
    """
    out = []
    for o in obs:
        if o.seen or o.dist > near or (o.a, o.b) in excluded:
            continue
        latest = max((a.start for a in arrivals if a.slot in (o.a, o.b) and a.start <= o.t), default=None)
        out.append((o.t, o.a, o.b, o.dist, None if latest is None else o.t - latest))
    return sorted(out)


def _tick_t(tick: dict[int, Reading]) -> float:
    return next(iter(tick.values())).t


def gid_changes(ticks: list[dict[int, Reading]]) -> list[tuple[int, float, bool]]:
    """(slot, t, across a zone change or loading screen) for each change of a client's own id."""
    out = []
    last: dict[int, Reading] = {}
    for tick in ticks:
        for slot, r in tick.items():
            prev = last.get(slot)
            if prev is not None and r.gid_seq != prev.gid_seq:
                out.append((slot, r.t, prev.loading or r.loading or prev.zone != r.zone))
            last[slot] = r
    return out


def _range(values: list[float]) -> str:
    if not values:
        return "none"
    return (
        f"n={len(values):<3} min {min(values):7.1f}   median {statistics.median(values):7.1f}   max {max(values):7.1f}"
    )


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summarize(readings: list[Reading], near: float = DEFAULT_NEAR) -> str:
    ticks = group_ticks(readings)
    obs = pair_observations(ticks)
    slots = sorted({r.slot for r in readings})
    lines = ["=" * 78, "SUMMARY", f"  {len(ticks)} ticks, clients {', '.join(f'P{s}' for s in slots)}"]

    # 1. Radius
    crossings = find_crossings(obs)
    lines += ["", "1. RADIUS (same zone, both settled)"]
    lines.append(f"  seen at        {_range([o.dist for o in obs if o.seen])}")
    lines.append(f"  not seen at    {_range([o.dist for o in obs if not o.seen])}")
    for kind in ("appear", "vanish"):
        these = [c for c in crossings if c.kind == kind]
        lines.append(f"  {kind:<6} between  {_range([c.dist_before for c in these])}")
        lines.append(f"                 and  {_range([c.dist_after for c in these])}")
    per_zone: dict[str, list[Crossing]] = defaultdict(list)
    zone_of = {(o.t, o.a, o.b): o.zone for o in obs}
    for c in crossings:
        per_zone[zone_of.get((c.t, c.a, c.b), "?")].append(c)
    for zone, cs in sorted(per_zone.items()):
        detail = ", ".join(f"{c.kind} P{c.a}->P{c.b} {c.dist_before:.0f}..{c.dist_after:.0f}" for c in cs[:6])
        lines.append(f"  {zone}: {detail}{' ...' if len(cs) > 6 else ''}")

    # Realm
    lines += ["", f"   REALM: pairs within {near:.0f} that never saw each other (different realms?)"]
    blind = never_seen_close(obs, near)
    lines += [f"  P{a} never saw P{b} in {zone}, closest {d:.0f}, {n} reading(s)" for a, b, zone, d, n in blind]
    if not blind:
        lines.append("  none")

    # Symmetry
    both, disagree = asymmetries(obs)
    lines += ["", f"   SYMMETRY: {len(disagree)} of {both} two-way readings disagreed"]
    lines += [f"  t={t:7.1f}s  P{s} saw P{x}, not back, at {d:.0f}" for t, s, x, d in disagree[:8]]

    # 2. Battle
    split = battle_split(obs, near)
    lines += ["", f"2. BATTLE (observer out of battle, within {near:.0f})"]
    for in_battle, label in ((True, "target in battle    "), (False, "target out of battle")):
        seen, total = split[in_battle]
        lines.append(f"  {label}  seen {seen}/{total}" + (f"  ({seen / total:.0%})" if total else ""))

    # Near misses, with the realm suspects set aside
    arrivals = find_arrivals(ticks)
    misses = near_misses(obs, arrivals, near, {(a, b) for a, b, *_ in blind})
    lines += ["", f"   NEAR MISSES: {len(misses)} unseen reading(s) within {near:.0f}, realm suspects aside"]
    lines += [
        f"  t={t:7.1f}s  P{a} did not see P{b} at {d:.0f}, "
        + ("no arrival before it" if since is None else f"{since:.1f}s after one of them arrived")
        for t, a, b, d, since in misses[:12]
    ]

    # 3. Arrival
    views = arrival_views(ticks, arrivals)
    lines += [
        "",
        f"3. ARRIVAL within {near:.0f} of the other, realm suspects aside",
        "   (seconds from the first settled reading; negative = while still loading)",
    ]
    blind_pairs = {(a, b) for a, b, *_ in blind}
    landed = [
        v
        for v in views
        if v.dist is not None
        and v.dist <= near
        and (v.arrival.slot, v.other) not in blind_pairs
        and (v.other, v.arrival.slot) not in blind_pairs
    ]
    for direction in ("others see arriver", "arriver sees others"):
        for kind in ("load", "jump"):
            these = [v for v in landed if v.direction == direction and v.arrival.kind == kind]
            if not these:
                continue
            lags = [v.lag for v in these if v.lag is not None]
            lost = [v for v in these if v.lag is None and not v.never_lost]
            kept = sum(1 for v in these if v.never_lost)
            lines.append(
                f"  {direction:<20} after {kind:<4} {_range(lags)}"
                + (f"   not within {ARRIVAL_WINDOW:.0f}s: {len(lost)}" if lost else "")
                + (f"   loaded throughout: {kept}" if kept else "")
            )
            lines += [
                f"      not seen: P{v.arrival.slot} arrived t={v.arrival.t:.1f}s, P{v.other} at {v.dist:.0f}"
                for v in lost[:4]
            ]
    far = sum(1 for v in views if v.dist is None or v.dist > near)
    lines.append(f"  ({far} view(s) of landings further than {near:.0f}, or unreadable, not timed)")
    if not views:
        lines.append("  no arrivals recorded")

    # 4. Cost
    lines += ["", "4. COST (one entity-list check, all clients concurrently)"]
    for slot in slots:
        mine = [r for r in readings if r.slot == slot]
        times = [r.check_s * 1000 for r in mine if r.check_s is not None]
        counts = [r.entities for r in mine if r.entities is not None]
        failed = sum(1 for r in mine if not r.checked)
        if times:
            lines.append(
                f"  P{slot}: median {statistics.median(times):6.1f}ms  p95 {_pct(times, 0.95):6.1f}ms  "
                f"max {max(times):6.1f}ms  entities median {statistics.median(counts):.0f} max {max(counts)}"
                + (f"  FAILED {failed}/{len(mine)}" if failed else "")
            )
        else:
            lines.append(f"  P{slot}: no successful check ({failed} failed)")
    self_missing = [r for r in readings if r.self_seen is False]
    lines.append(
        f"  own id missing from own list: {len(self_missing)} reading(s), "
        f"{sum(1 for r in self_missing if r.loading)} of them while loading"
    )

    # GID stability
    changes = gid_changes(ticks)
    lines += ["", f"   GID STABILITY: {len(changes)} change(s) of a client's own id"]
    lines += [
        f"  P{s} at t={t:.1f}s {'across a zone change or load' if moved else 'WITHOUT moving zone'}"
        for s, t, moved in changes
    ]
    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

CSV_FIELDS = (
    "t",
    "slot",
    "zone",
    "x",
    "y",
    "z",
    "loading",
    "in_battle",
    "gid_seq",
    "self_seen",
    "seen",
    "check_ms",
    "entities",
    "error",
)


def _flag(value: bool | None) -> str:
    return "-" if value is None else str(int(value))


def _unflag(text: str) -> bool | None:
    return None if text == "-" else text == "1"


def write_reading(writer, r: Reading) -> None:
    pos = ("", "", "") if r.pos is None else (r.pos.x, r.pos.y, r.pos.z)
    writer.writerow(
        [
            f"{r.t:.3f}",
            r.slot,
            r.zone or "",
            *pos,
            _flag(r.loading),
            _flag(r.in_battle),
            r.gid_seq,
            _flag(r.self_seen),
            "-" if r.seen is None else ";".join(str(s) for s in sorted(r.seen)),
            "" if r.check_s is None else f"{r.check_s * 1000:.2f}",
            "" if r.entities is None else r.entities,
            r.error,
        ]
    )


def read_csv(path: Path, offset: float = 0.0) -> list[Reading]:
    out = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            seen = None if row["seen"] == "-" else frozenset(int(s) for s in row["seen"].split(";") if s)
            out.append(
                Reading(
                    t=float(row["t"]) + offset,
                    slot=int(row["slot"]),
                    zone=row["zone"] or None,
                    pos=Point(float(row["x"]), float(row["y"]), float(row["z"])) if row["x"] else None,
                    loading=row["loading"] == "1",
                    in_battle=row["in_battle"] == "1",
                    gid_seq=int(row["gid_seq"]),
                    self_seen=_unflag(row["self_seen"]),
                    seen=seen,
                    check_s=float(row["check_ms"]) / 1000 if row["check_ms"] else None,
                    entities=int(row["entities"]) if row["entities"] else None,
                    error=row["error"],
                )
            )
    return out


def read_csvs(paths: list[Path]) -> list[Reading]:
    """Several runs pooled into one timeline, each shifted to start after the last ends."""
    out: list[Reading] = []
    offset = 0.0
    for path in paths:
        run = read_csv(path, offset)
        out += run
        offset = max((r.t for r in run), default=offset) + RUN_GAP
    return out


# ---------------------------------------------------------------------------
# Live recording
# ---------------------------------------------------------------------------


async def _safe(coro):
    """The awaited value, or None if the read raised. Every read here is speculative."""
    try:
        return await coro
    except Exception:
        return None


async def _entity_gids(client) -> tuple[set[int] | None, float, int | None, str]:
    """The global ids this client has loaded, how long reading them took, how many
    entities there were, and the error if the list could not be read."""
    started = time.perf_counter()
    try:
        entities = await client.get_base_entity_list()
    except Exception as exc:
        return None, time.perf_counter() - started, None, type(exc).__name__
    gids = await asyncio.gather(*(_safe(e.global_id_full()) for e in entities))
    return {g for g in gids if g is not None}, time.perf_counter() - started, len(entities), ""


class _Slot:
    """What the recorder remembers about one client between ticks. The id stays here."""

    def __init__(self) -> None:
        self.gid: int | None = None
        self.gid_seq = 0

    def update(self, gid: int | None) -> None:
        if gid is None:
            return
        if self.gid is not None and gid != self.gid:
            self.gid_seq += 1
        self.gid = gid


async def _tick(clients: list, slots: list[_Slot], t: float) -> list[Reading]:
    async def state(client):
        zone, pos, loading, battle, gid = await asyncio.gather(
            _safe(client.zone_name()),
            _safe(client.body.position()),
            _safe(client.is_loading()),
            _safe(client.in_battle()),
            _safe(client.client_object.global_id_full()),
        )
        return zone, pos, bool(loading), bool(battle), gid

    states = await asyncio.gather(*(state(c) for c in clients))
    checks = await asyncio.gather(*(_entity_gids(c) for c in clients))
    for slot, (*_, gid) in zip(slots, states):
        slot.update(gid)
    readings = []
    for i, ((zone, pos, loading, battle, _gid), (loaded, took, count, error)) in enumerate(zip(states, checks)):
        if loaded is None:
            seen, self_seen = None, None
        else:
            seen = frozenset(j + 1 for j, s in enumerate(slots) if j != i and s.gid is not None and s.gid in loaded)
            self_seen = slots[i].gid in loaded if slots[i].gid is not None else None
        readings.append(
            Reading(
                t=t,
                slot=i + 1,
                zone=zone,
                pos=None if pos is None else Point(pos.x, pos.y, pos.z),
                loading=loading,
                in_battle=battle,
                gid_seq=slots[i].gid_seq,
                self_seen=self_seen,
                seen=seen,
                check_s=took if loaded is not None else None,
                entities=count,
                error=error,
            )
        )
    return readings


def _status(readings: list[Reading]) -> str:
    parts = []
    for r in readings:
        if r.loading:
            parts.append(f"P{r.slot} loading")
        elif r.seen is None:
            parts.append(f"P{r.slot} check failed")
        else:
            seen = ",".join(f"P{s}" for s in sorted(r.seen)) or "nobody"
            parts.append(f"P{r.slot}{' [battle]' if r.in_battle else ''} sees {seen}")
    by_slot = {r.slot: r for r in readings}
    for a, b in ((a, b) for a in by_slot for b in by_slot if a < b):
        ra, rb = by_slot[a], by_slot[b]
        if ra.pos and rb.pos and ra.zone == rb.zone:
            parts.append(f"P{a}-P{b} {ra.pos.dist(rb.pos):.0f}")
    worst = max((r.check_s for r in readings if r.check_s is not None), default=None)
    if worst is not None:
        parts.append(f"check {worst * 1000:.0f}ms")
    return "  |  ".join(parts)


async def record(clients: list, args, writer, readings: list[Reading]) -> None:
    slots = [_Slot() for _ in clients]
    t0 = time.monotonic()
    last_status = 0.0
    next_tick = time.monotonic()
    print("Recording. Ctrl+C to finish.\n")
    while True:
        tick = await _tick(clients, slots, time.monotonic() - t0)
        readings += tick
        for r in tick:
            write_reading(writer, r)
        now = time.monotonic()
        if now - last_status >= STATUS_INTERVAL:
            last_status = now
            print(f"\r  t={tick[0].t:7.1f}s  {_status(tick)}".ljust(150), end="", flush=True)
        next_tick += args.interval
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))


async def main(args: argparse.Namespace) -> int:
    if args.analyse:
        paths = [Path(p) for pattern in args.analyse for p in sorted(glob.glob(pattern))]
        if not paths:
            print(f"No CSV matches {args.analyse}")
            return 2
        print("Pooling " + ", ".join(p.name for p in paths))
        print(summarize(read_csvs(paths), args.near))
        return 0

    csv_path = (
        Path(args.csv)
        if args.csv
        else Path(__file__).with_name(f"probe_same_place.{time.strftime('%Y%m%d-%H%M%S')}.local.csv")
    )
    if not csv_path.name.endswith(".local.csv"):
        print(f"--csv must end in .local.csv so git ignores it; got {csv_path.name!r}")
        return 2

    from wizwalker.extensions.wizsprinter import WizSprinter

    clients = WizSprinter().get_new_clients()
    if len(clients) < 2:
        print(f"Found {len(clients)} client(s); this probe needs at least two logged in.")
        return 1
    clients.sort(key=lambda c: c.window_handle)
    for i, c in enumerate(clients, start=1):
        print(f"  P{i}: hwnd={c.window_handle}")

    readings: list[Reading] = []
    try:
        for c in clients:
            await c.activate_hooks()
        with csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)
            try:
                await record(clients, args, writer, readings)
            except asyncio.CancelledError:
                # Ctrl+C under asyncio.run cancels this task; swallowing it still prints the summary.
                print("\n\nStopped by Ctrl+C.")
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass

    print(f"Readings written to {csv_path}")
    print(summarize(readings, args.near))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--interval", type=float, default=SAMPLE_INTERVAL, help=f"Seconds between ticks (default {SAMPLE_INTERVAL})."
    )
    parser.add_argument(
        "--near",
        type=float,
        default=DEFAULT_NEAR,
        help=f"Distance counted as close for the battle and realm checks (default {DEFAULT_NEAR:.0f}).",
    )
    parser.add_argument(
        "--csv", help="Where to write readings; must end in .local.csv (default: a new timestamped file in tools/)."
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
