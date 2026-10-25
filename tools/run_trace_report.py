"""Summarize a run trace: where a run's time went. See bartlebot/bot/run_trace.py.

A trace is written for every run started with BARTLEBOT_DEV set, to logs/trace/.

    uv run python tools/run_trace_report.py                   # the newest trace
    uv run python tools/run_trace_report.py logs/trace/run_2026-10-23_09-07-37.jsonl
    uv run python tools/run_trace_report.py --rounds 5        # detail for the five slowest rounds

Sections, in order:

- **Pre-run**: the span tree of the setup before the first node, then every input sent
  during it in order, and the same inputs counted by who sent them. A window opened,
  closed and opened again is the same key or button twice from two callers.
- **Nodes**: time per node type, with the reads, inputs and UI-tree walks each made.
- **Combat**: per fight, a row per round. Each player's column is the time its round
  took us (``handle_round``: deciding and casting); *ours* runs from the first player
  starting the round to the last one committing, *game* from that commit to the next
  round's planning phase, which is the game's animations and the server.
- **Slowest rounds**: each player's priorities in those rounds, split into casts,
  conditions and the rest, which is reads and fixed waits.
- **UI-tree walks**, **stalls** and **background reads**: the costs that land on the
  event loop itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from bartlebot.paths import project_root

INPUT_KINDS = ("key", "hotkey", "click")


@dataclass
class Span:
    id: int
    parent: int | None
    name: str
    slot: int | None
    t: float
    dur: float
    reads: int
    read_s: float
    attrs: dict
    children: list[Span] = field(default_factory=list)

    @property
    def end(self) -> float:
        return self.t + self.dur


@dataclass
class Trace:
    spans: dict[int, Span]
    events: list[dict]
    stalls: list[dict]
    ticks: list[dict]
    duration: float

    def named(self, prefix: str) -> list[Span]:
        return sorted((s for s in self.spans.values() if s.name.startswith(prefix)), key=lambda s: s.t)

    def descendants(self, span: Span) -> list[Span]:
        found, stack = [], list(span.children)
        while stack:
            child = stack.pop()
            found.append(child)
            stack.extend(child.children)
        return found

    def ids_under(self, span: Span) -> set[int]:
        return {span.id} | {s.id for s in self.descendants(span)}


def load(path: Path) -> Trace:
    spans: dict[int, Span] = {}
    events, stalls, ticks = [], [], []
    duration = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        kind = record["kind"]
        duration = max(duration, record.get("t", 0.0) + record.get("dur", 0.0))
        if kind == "span":
            spans[record["id"]] = Span(
                record["id"],
                record["parent"],
                record["name"],
                record["slot"],
                record["t"],
                record["dur"],
                record["reads"],
                record["read_s"],
                record.get("attrs", {}),
            )
        elif kind in INPUT_KINDS or kind == "walk":
            events.append(record)
        elif kind == "stall":
            stalls.append(record)
        elif kind == "tick":
            ticks.append(record)
    for span in spans.values():
        if span.parent in spans:
            spans[span.parent].children.append(span)
    for span in spans.values():
        span.children.sort(key=lambda s: s.t)
    events.sort(key=lambda e: e["t"])
    return Trace(spans, events, stalls, ticks, duration)


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.2f} s"


def _player(slot: int | None) -> str:
    return f"P{slot}" if slot is not None else "--"


def _describe_input(event: dict) -> str:
    if event["kind"] == "key":
        return f"key {event['key']}" + (f" (held {event['hold']} s)" if event.get("hold") else "")
    if event["kind"] == "hotkey":
        return f"hotkey {event['keys']}"
    if event.get("window"):
        return f"click {event['window']}"
    return f"click at {tuple(event['at'])}" if event.get("at") else "click (unnamed window)"


def _who(event: dict) -> str:
    who = event.get("caller") or "?"
    return f"{who} via {event['via']}" if event.get("via") else who


def summary(trace: Trace) -> list[str]:
    inputs = [e for e in trace.events if e["kind"] in INPUT_KINDS]
    walks = [e for e in trace.events if e["kind"] == "walk"]
    return [
        f"Run: {_ms(trace.duration)}, {len(trace.spans)} spans, {len(inputs)} inputs, "
        f"{len(walks)} UI-tree walks ({_ms(sum(w['dur'] for w in walks))}), "
        f"{len(trace.stalls)} stalls ({_ms(sum(s['dur'] for s in trace.stalls))})",
    ]


def _tree(span: Span, origin: float, depth: int = 0) -> list[str]:
    attrs = " ".join(f"{k}={v}" for k, v in span.attrs.items())
    line = (
        f"{'  ' * depth}{span.name:<{34 - 2 * depth}} {_player(span.slot):>3}  +{span.t - origin:7.2f}  "
        f"{_ms(span.dur):>9}  {span.reads:>6} reads ({_ms(span.read_s)})"
    )
    lines = [line + (f"  {attrs}" if attrs else "")]
    for child in span.children:
        lines += _tree(child, origin, depth + 1)
    return lines


def pre_run(trace: Trace) -> list[str]:
    roots = trace.named("pre_run")
    root = next((s for s in roots if s.name == "pre_run"), None)
    if root is None:
        return ["No pre-run span in this trace."]
    lines = _tree(root, root.t)
    inside = [e for e in trace.events if e["kind"] in INPUT_KINDS and root.t <= e["t"] <= root.end]
    lines += ["", f"Inputs during the pre-run, in order ({len(inside)}):"]
    for event in inside:
        lines.append(
            f"  +{event['t'] - root.t:7.2f}  {_player(event['slot']):>3}  {_describe_input(event):<28} {_who(event)}"
        )
    lines += ["", "The same, counted by player, input and caller:"]
    counts = Counter((e["slot"] or 0, _describe_input(e), _who(e)) for e in inside)
    for (slot, what, who), n in sorted(counts.items()):
        lines.append(f"  {n:>3}x  {_player(slot or None):>3}  {what:<28} {who}")
    return lines


def nodes(trace: Trace) -> list[str]:
    by_type: dict[str, list[Span]] = defaultdict(list)
    for span in trace.named("node."):
        by_type[span.name.removeprefix("node.")].append(span)
    if not by_type:
        return ["No node spans in this trace."]
    lines = [
        f"  {'node type':<28} {'runs':>5} {'total':>9} {'mean':>9} {'max':>9} {'reads':>9} {'inputs':>7} {'walks':>6}"
    ]
    rows = []
    for node_type, spans in by_type.items():
        ids = set().union(*(trace.ids_under(s) for s in spans))
        under = [e for e in trace.events if e["span"] in ids]
        reads = sum(trace.spans[i].reads for i in ids)
        total = sum(s.dur for s in spans)
        rows.append((total, node_type, spans, reads, under))
    for total, node_type, spans, reads, under in sorted(rows, reverse=True):
        lines.append(
            f"  {node_type:<28} {len(spans):>5} {_ms(total):>9} {_ms(total / len(spans)):>9} "
            f"{_ms(max(s.dur for s in spans)):>9} {reads:>9} {sum(e['kind'] in INPUT_KINDS for e in under):>7} "
            f"{sum(e['kind'] == 'walk' for e in under):>6}"
        )
    return lines


@dataclass
class Round:
    fight: int
    number: int
    spans: dict[int, Span]  # player -> its combat.round span
    ours: float
    game: float | None

    @property
    def last(self) -> int:
        return max(self.spans, key=lambda slot: self.spans[slot].end)


def _fights(trace: Trace) -> list[list[Span]]:
    """Every player's combat.fight spans, grouped into the fights they overlap in."""
    groups: list[list[Span]] = []
    for span in trace.named("combat.fight"):
        if groups and span.t <= max(s.end for s in groups[-1]):
            groups[-1].append(span)
        else:
            groups.append([span])
    return groups


def rounds(trace: Trace) -> list[Round]:
    found = []
    for index, fight in enumerate(_fights(trace), 1):
        by_number: dict[int, dict[int, Span]] = defaultdict(dict)
        for player_fight in fight:
            played = [c for c in player_fight.children if c.name == "combat.round"]
            for position, span in enumerate(played, 1):
                by_number[span.attrs.get("round", position)][span.slot or 0] = span
        numbers = sorted(by_number)
        fight_end = max(s.end for s in fight)
        for i, number in enumerate(numbers):
            spans = by_number[number]
            first = min(s.t for s in spans.values())
            committed = max(s.end for s in spans.values())
            if i + 1 < len(numbers):
                game = min(s.t for s in by_number[numbers[i + 1]].values()) - committed
            else:
                game = fight_end - committed
            found.append(Round(index, number, spans, committed - first, game))
    return found


def combat(trace: Trace) -> list[str]:
    all_rounds = rounds(trace)
    if not all_rounds:
        return ["No combat in this trace."]
    lines = []
    for fight in sorted({r.fight for r in all_rounds}):
        these = [r for r in all_rounds if r.fight == fight]
        players = sorted({slot for r in these for slot in r.spans})
        lines.append(
            f"Fight {fight}: {len(these)} rounds, ours {_ms(sum(r.ours for r in these))}, "
            f"game {_ms(sum(r.game or 0 for r in these))}"
        )
        lines.append("  round " + "".join(f"{_player(p):>9}" for p in players) + f"{'last':>6}{'ours':>10}{'game':>10}")
        for r in these:
            cells = "".join(f"{_ms(r.spans[p].dur) if p in r.spans else '':>9}" for p in players)
            lines.append(f"  {r.number:>5} {cells}{_player(r.last):>6}{_ms(r.ours):>10}{_ms(r.game or 0):>10}")
        lines.append("")
    return lines


def slowest_rounds(trace: Trace, count: int) -> list[str]:
    worst = sorted(rounds(trace), key=lambda r: r.ours, reverse=True)[:count]
    lines = []
    for r in worst:
        lines.append(f"Fight {r.fight}, round {r.number}: ours {_ms(r.ours)}, last to commit {_player(r.last)}")
        for slot, span in sorted(r.spans.items()):
            tried = [c for c in span.children if c.name == "combat.priority"]
            rest = span.dur - sum(c.dur for c in tried)
            lines.append(
                f"  {_player(slot)}  {_ms(span.dur)}, {span.reads} reads ({_ms(span.read_s)}) "
                f"before or between priorities, {_ms(rest)} outside them"
            )
            for p in tried:
                under = trace.descendants(p)
                casts = sum(c.dur for c in under if c.name == "combat.cast")
                conditions = sum(c.dur for c in p.children if c.name == "combat.condition")
                reads = p.reads + sum(c.reads for c in under)
                lines.append(
                    f"      #{p.attrs.get('n', '?'):<3} {_ms(p.dur):>9}  casts {_ms(casts):>8}  "
                    f"conditions {_ms(conditions):>8}  rest {_ms(p.dur - casts - conditions):>8}  "
                    f"{reads:>5} reads  -> {p.attrs.get('result', '?')}"
                )
        lines.append("")
    return lines or ["No combat in this trace."]


def walks(trace: Trace) -> list[str]:
    by_where: dict[tuple, list[dict]] = defaultdict(list)
    for event in trace.events:
        if event["kind"] == "walk":
            what = (
                f"name={event['name']}"
                if "name" in event
                else f"type={event['type']}"
                if "type" in event
                else "predicate"
            )
            by_where[(what, _who(event))].append(event)
    if not by_where:
        return ["No UI-tree walks in this trace."]
    lines = [f"  {'count':>5} {'total':>9} {'mean':>9}  looking for / by"]
    for (what, who), found in sorted(by_where.items(), key=lambda item: -sum(e["dur"] for e in item[1])):
        total = sum(e["dur"] for e in found)
        lines.append(f"  {len(found):>5} {_ms(total):>9} {_ms(total / len(found)):>9}  {what}  {who}")
    return lines


def stalls(trace: Trace, count: int = 10) -> list[str]:
    if not trace.stalls:
        return ["No stalls."]
    lines = [
        f"{len(trace.stalls)} stalls, {_ms(sum(s['dur'] for s in trace.stalls))} in all. The longest, "
        "with the innermost spans open while each held the loop:"
    ]
    for stall in sorted(trace.stalls, key=lambda s: -s["dur"])[:count]:
        # A stall's t is when the heartbeat should have woken, so the loop was held then.
        at = stall["t"]
        open_spans = [
            s for s in trace.spans.values() if s.t <= at <= s.end and not any(c.t <= at <= c.end for c in s.children)
        ]
        names = ", ".join(sorted(f"{s.name} {_player(s.slot)}" for s in open_spans)) or "none"
        lines.append(f"  +{at:8.2f}  {_ms(stall['dur']):>9}  {names}")
    return lines


def background(trace: Trace) -> list[str]:
    if not trace.ticks:
        return ["No background reads recorded."]
    reads = sum(t["reads"] for t in trace.ticks)
    read_s = sum(t["read_s"] for t in trace.ticks)
    seconds = max(len(trace.ticks), 1)
    return [f"{reads} reads outside any span ({_ms(read_s)}), about {reads / seconds:.0f} a second"]


def report(trace: Trace, slow_rounds: int = 3) -> str:
    sections = [
        ("Summary", summary(trace)),
        ("Pre-run", pre_run(trace)),
        ("Nodes", nodes(trace)),
        ("Combat", combat(trace)),
        (f"The {slow_rounds} slowest rounds", slowest_rounds(trace, slow_rounds)),
        ("UI-tree walks", walks(trace)),
        ("Stalls", stalls(trace)),
        ("Background reads", background(trace)),
    ]
    return "\n\n".join(f"== {title} ==\n" + "\n".join(lines) for title, lines in sections) + "\n"


def newest_trace() -> Path | None:
    # Only developer mode writes traces, and its logs are the repo's own.
    traces = sorted((project_root() / "logs" / "trace").glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
    return traces[-1] if traces else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace", nargs="?", type=Path, help="a run_*.jsonl file; the newest when left out")
    parser.add_argument("--rounds", type=int, default=3, help="how many of the slowest rounds to break down")
    args = parser.parse_args(argv)
    path = args.trace or newest_trace()
    if path is None:
        print("No run traces found. Run a bot with BARTLEBOT_DEV=1 to write one.", file=sys.stderr)
        return 1
    print(f"{path}\n")
    sys.stdout.write(report(load(path), args.rounds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
