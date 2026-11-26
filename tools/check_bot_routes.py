"""Which walks the shipped bots ask for can't be planned, cross a gate, or take too long.

Every move in a zone walks (``bartlebot/nodes/walking.py``), so a bot works only where each
authored point can be walked to from the point before it. For every shipped bot, composed
with its subgraphs, this plans each such pair on the zone maps core ships and reports:

- **no route**: the two points share no floor (``ZonePlanner.no_route`` says why);
- **a gate is the only way**: the route crosses a placed object's gate, and with that gate
  closed there is no way around, so a wizard whose quests left it standing can't walk there.
  Its ``Crossing`` is what a Teleport to XYZ past it would need (Stage 7 of #322);
- **a gate is crossed** with a way around it: noted, since the walk goes around when it's closed;
- **a long walk**: one over ``LONG_WALK`` seconds at a wizard's own speed.

A pair is two points one walk joins. Inside a node: Reset XYZ, then the node's other points in
order. Between nodes: the last point of one, and the first of the next along any edge, while
nothing in between changed the zone or moved the wizard (a fight, a branch or a wait keeps it
where it is; a port, a zone change or a node with no point of its own loses track of it).

    uv run --no-sync python tools/check_bot_routes.py [--jobs N] [--out PATH] [bots ...]

It reads the shipped maps only, and writes the report to ``--out`` (by default
``logs/check_bot_routes/<time>.md``). The first run builds any walking copy not yet saved.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: Seconds of walking past which a pair is reported as long.
LONG_WALK = 60.0

#: Nodes that leave every wizard where it stood: the walk after them starts from the point before.
KEEPS_PLACE = frozenset(
    {
        "handle_combat",
        "sleep",
        "branch",
        "check_health_and_mana",
        "check_level_scaling",
        "check_zone",
        "set_variables",
        "wait_for_dialogue",
        "click_path",
        "quicksell",
    }
)

#: The params that name the zone a node's points are in, and the zone it leaves for.
IN_ZONE = ("zone", "from_zone")

#: Nodes whose own two points are a hop, not a walk: walked to the first, then moved to the second.
HOPS = frozenset({"teleport_to_xyz"})
OUT_ZONE = ("to_zone", "dungeon_zone")

Point = tuple[float, float, float]


@dataclass(frozen=True)
class Pair:
    zone: str
    start: Point
    goal: Point
    where: str  # "Node A › Node B", or one node's two params
    step: str = ""  # the node type that walks it: the goal's node


@dataclass
class Finding:
    pair: Pair
    bots: list[str] = field(default_factory=list)
    kind: str = ""  # "no route", "only way", "around", "long", "ok"
    seconds: float = 0.0
    detail: str = ""
    gates: list[tuple[str, str]] = field(default_factory=list)  # (gate, "around" or the crossing)


def _xyz(value) -> Point | None:
    if isinstance(value, dict) and {"x", "y", "z"} <= set(value):
        point = (float(value["x"]), float(value["y"]), float(value["z"]))
        return None if point == (0.0, 0.0, 0.0) else point
    return None


def _points(params: dict) -> list[tuple[str, Point]]:
    """The node's points in walking order: Reset XYZ first, then the rest as written."""
    found = [(k, p) for k, v in params.items() if (p := _xyz(v)) is not None]
    return sorted(found, key=lambda kp: kp[0] != "reset_xyz")


def _zone(params: dict, keys) -> str | None:
    return next((params[k] for k in keys if isinstance(params.get(k), str) and params[k]), None)


def pairs_of(config) -> set[Pair]:
    """Every pair of points one walk of *config* joins."""
    states = {s.id: s for s in config.states}
    name = {s.id: s.label or s.id for s in config.states}
    out_of: dict[str, set[tuple[str, Point, str]]] = {sid: set() for sid in states}
    into: dict[str, set[tuple[str, Point, str]]] = {sid: set() for sid in states}
    edges = {(t.source, t.dest) for t in config.transitions if t.source in states and t.dest in states}
    pairs: set[Pair] = set()

    def _out(sid: str) -> set[tuple[str, Point, str]]:
        state = states[sid]
        params = state.params or {}
        points = _points(params)
        zone = _zone(params, IN_ZONE)
        if points and zone and _zone(params, OUT_ZONE) is None:
            return {(zone, points[-1][1], name[sid])}
        if not points and state.node_type in KEEPS_PLACE:
            return set(into[sid])
        return set()

    # Where each state may leave a wizard, to a fixed point over the edges.
    changed = True
    while changed:
        changed = False
        for sid in states:
            new = _out(sid)
            if new != out_of[sid]:
                out_of[sid], changed = new, True
        for a, b in edges:
            if not out_of[a] <= into[b]:
                into[b] |= out_of[a]
                changed = True

    for sid, state in states.items():
        params = state.params or {}
        points = _points(params)
        zone = _zone(params, IN_ZONE)
        if not points or not zone:
            continue
        for (k1, p1), (k2, p2) in zip(points, points[1:]):
            if state.node_type in HOPS:
                continue  # its start to its end is the hop past the gate, never walked
            pairs.add(Pair(zone, p1, p2, f"{name[sid]}: {k1} › {k2}", state.node_type))
        for from_zone, point, before in into[sid]:
            if from_zone == zone and point != points[0][1]:
                pairs.add(Pair(zone, point, points[0][1], f"{before} › {name[sid]}", state.node_type))
    return pairs


def _shipped_bots(names: list[str]) -> list[Path]:
    paths = sorted((REPO / "bots").rglob("*.json"))
    return [p for p in paths if not names or p.stem in names]


def _walk_seconds(planner, route) -> float:
    from bartleby.runtime.movement.route import WIZARD_SPEED  # noqa: PLC0415

    legs = planner.legs(route)
    walked = sum(math.dist(a[:2], b[:2]) for leg in legs for a, b in zip(leg.waypoints, leg.waypoints[1:]))
    return walked / WIZARD_SPEED + sum(leg.link.seconds for leg in legs if leg.link)


def _check_zone(zone: str, pairs: list[Pair]) -> list[Finding]:
    """Plan every pair in one zone: in a worker process, so zones plan side by side."""
    from bartleby.runtime.movement import zone_maps  # noqa: PLC0415

    findings = []
    planner = zone_maps.SHIPPED.planner(zone)
    for pair in pairs:
        finding = Finding(pair)
        route = planner.route(pair.start, pair.goal)
        if route is None:
            finding.kind, finding.detail = "no route", planner.no_route(pair.start, pair.goal).describe()
            findings.append(finding)
            continue
        finding.seconds = _walk_seconds(planner, route)
        for gate in planner.gates_on(route):
            closed = zone_maps.SHIPPED.planner(zone)
            closed.close(gate)
            if closed.route(pair.start, pair.goal) is not None:
                finding.gates.append((gate.name, "around"))
                continue
            crossing = closed.no_route(pair.start, pair.goal).crossing
            how = (
                "no crossing found"
                if crossing is None
                else f"{crossing.length:.0f} units, {_fmt(crossing.before)} to {_fmt(crossing.after)}"
            )
            finding.gates.append((gate.name, how))
        if any(how != "around" for _, how in finding.gates):
            finding.kind = "only way"
        elif finding.seconds > LONG_WALK:
            finding.kind = "long"
        elif finding.gates:
            finding.kind = "around"
        else:
            finding.kind = "ok"
        findings.append(finding)
    return findings


def _fmt(p) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f})"


def collect(paths: list[Path]) -> dict[Pair, list[str]]:
    """Every pair the bots at *paths* walk, and which bots walk it."""
    from bartlebot.bot.schema import BotConfig  # noqa: PLC0415
    from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary  # noqa: PLC0415
    from bartlebot.content.subgraphs.subgraphs import compose_bot  # noqa: PLC0415

    library = SubgraphLibrary(REPO, REPO)
    walked: dict[Pair, list[str]] = defaultdict(list)
    for path in paths:
        config, _report = compose_bot(BotConfig.load(path), library)
        for pair in pairs_of(config):
            walked[pair].append(path.relative_to(REPO / "bots").as_posix())
    return walked


def report(findings: list[Finding], bots: int, seconds: float) -> str:
    by_kind = defaultdict(list)
    for f in findings:
        by_kind[f.kind].append(f)
    lines = [
        "# Walks the shipped bots ask for",
        "",
        f"{datetime.now():%Y-%m-%d %H:%M}, `tools/check_bot_routes.py`: {bots} bots, {len(findings)} distinct "
        f"walks, planned in {seconds:.0f} s on the zone maps core ships.",
        "",
        f"- No route: {len(by_kind['no route'])}",
        f"- A gate is the only way: {len(by_kind['only way'])}",
        f"- Over {LONG_WALK:.0f} s: {len(by_kind['long'])}",
        f"- A gate crossed, with a way around: {len(by_kind['around'])}",
        f"- Clear: {len(by_kind['ok'])}",
    ]
    sections = [
        ("No route", "no route", lambda f: f.detail),
        (
            "A gate is the only way",
            "only way",
            lambda f: "; ".join(f"{g}: {how}" for g, how in f.gates if how != "around"),
        ),
        (f"Over {LONG_WALK:.0f} s", "long", lambda f: f"{f.seconds:.0f} s"),
        ("A gate crossed, with a way around", "around", lambda f: ", ".join(g for g, _ in f.gates)),
    ]
    for title, kind, what in sections:
        rows = sorted(by_kind[kind], key=lambda f: (f.pair.zone, f.bots[0], f.pair.where))
        if not rows:
            continue
        lines += [
            "",
            f"## {title}",
            "",
            "| zone | bots | walk | step | from | to | seconds | what |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for f in rows:
            bots_text = ", ".join(f.bots[:3]) + (f" and {len(f.bots) - 3} more" if len(f.bots) > 3 else "")
            lines.append(
                f"| {f.pair.zone} | {bots_text} | {f.pair.where} | {f.pair.step} | {_fmt(f.pair.start)} | {_fmt(f.pair.goal)} "
                f"| {f.seconds:.0f} | {what(f)} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bots", nargs="*", help="bot file names without .json (every shipped bot by default)")
    parser.add_argument("--jobs", type=int, default=4, help="zones planned side by side (default 4)")
    parser.add_argument("--out", type=Path, help="where to write the report")
    args = parser.parse_args(argv)

    started = time.monotonic()
    paths = _shipped_bots(args.bots)
    walked = collect(paths)
    by_zone: dict[str, list[Pair]] = defaultdict(list)
    for pair in walked:
        by_zone[pair.zone].append(pair)
    print(f"{len(paths)} bots, {len(walked)} distinct walks in {len(by_zone)} zones; planning...")

    findings: list[Finding] = []
    if args.jobs > 1:
        with ProcessPoolExecutor(args.jobs) as pool:
            for result in pool.map(_check_zone, list(by_zone), list(by_zone.values())):
                findings.extend(result)
    else:
        for zone, pairs in by_zone.items():
            findings.extend(_check_zone(zone, pairs))
    for f in findings:
        f.bots = sorted(walked[f.pair])

    text = report(findings, len(paths), time.monotonic() - started)
    out = args.out or REPO / "logs" / "check_bot_routes" / f"{datetime.now():%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text.split("\n## ")[0])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
