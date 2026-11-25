"""Diagnostic: which of a zone's gates does the client list, and what does it hold for each? (Stage 3.2 of #322)

A gate is a placed object whose template names a solid file (core's docs/WALKING.md, Gates). The
maps hold 3,152 of them, open until a walk finds one closed, since the game's files cannot say
which are there for a given player. This reads the client to find out what it can say instead,
for three questions:

  1. Is a blocker a quest removed missing from the client's entity list? Stand in the Shopping
     District with a wizard past Colossus Boulevard (BigGenericBlocker_CollossusGate gone for
     them), then with one who hasn't opened it.
  2. Does a door's open or closed state read, and does its collision follow it? A live object's
     ObjectStateBehavior holds m_stateList, one id per category of its template's state set
     (Root.wad's StateData/*.xml: CLOSED, OPENING, OPEN); --watch prints each change, and where
     the wizard stood, while you walk into it before and after.
  3. How far away does the client list placed objects, the m_exemptFromAOI ones among them?
     --watch while you walk across the zone; the summary gives each gate's farthest listed and
     nearest unlisted distance.

Each pass reads the zone's gates from its wad once (template id, zone tag, where it stands, the
AOI flag, whether a quest can remove it, the spawn requirements, the start state and the state
set), then the client's entity list once: every entity's template id and location, the walker's
own cost, then the details of the ones that match a gate, the live object of the gate's template
nearest where it is placed, within MATCH. --time-mobs also times wizsprinter's get_mobs(), the
pass core's client_mobs makes today, for comparison.

Nothing is written to the game but wizwalker's hooks. No wizard's name or id is read: entities
that are no gate are counted, never named.

HOW TO RUN
    1. Log a wizard in and stand in the zone.
    2. uv run python tools/probe_gates.py                  one pass, a table nearest first
       uv run python tools/probe_gates.py --watch 300      a pass every 2 s, each change printed
       add --gate Colossus to print only gates whose tag or template has it

Writes logs/gates/gates-<time>.csv: one row per gate per pass.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import functools
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_walk_route import REPO, _reader  # noqa: E402
from zone_files import text, to_plain, xyz, zone_record  # noqa: E402

#: Units from where a gate is placed within which a live object of its template is taken as it.
MATCH = 200.0
#: Units from where a gate is placed within which any live object is reported, when none of its
#: template is: in case the live template id is not the wad's.
NEAR = 5.0
#: Seconds between passes in --watch: core's WALK_MOB_REFRESH, the walker's own cadence.
EVERY = 2.0
#: The ObjectStateBehavior fields, from the type dump (r815378).
STATE_LIST = 136
STATE_OVERRIDE = 168


@dataclass(frozen=True)
class Placed:
    """A gate as the zone's wad places it."""

    tag: str
    template_id: int
    template: str  # the template's m_objectName
    at: tuple[float, float, float]
    exempt: bool  # m_exemptFromAOI
    removable: bool  # the template has a DeletedBehavior
    requirements: str  # the spawn requirements' entry names, such as Halloween
    start_state: str
    client_only: bool  # CollisionBehavior's m_bClientOnly
    state_set: str


@dataclass
class Seen:
    """What one pass read of one gate."""

    listed: bool
    offset: float = math.nan  # units from where it is placed to where the client has it
    name: str = ""
    live_exempt: bool | None = None
    behaviors: str = ""
    states: str = ""
    override: str = ""
    near: str = ""  # another object within NEAR, when none of its template is


@dataclass
class Range:
    """How far one gate was when listed and when not, over every pass."""

    farthest_listed: float = -math.inf
    nearest_unlisted: float = math.inf
    states: set[str] = field(default_factory=set)


def placed_gates(zone: str) -> list[Placed]:
    """The zone's placed objects with a solid file, as its gamedata.bin places them."""
    reader = _reader()
    record = zone_record(zone, reader.deserialize, reader.game_data)
    out = []
    for obj in record["m_objectList"] or []:
        if not obj:
            continue
        template_id = int(obj["m_templateID.m_full"])
        found = reader.solid_template(template_id)
        if found is None:
            continue
        _, filename, _ = found
        template = to_plain(reader.deserialize(reader.template_bytes(filename)))
        behaviors = [b for b in template.get("m_behaviors") or [] if isinstance(b, dict)]
        names = {b.get("m_behaviorName") for b in behaviors}
        state_set = next((text(b.get("m_stateSetName")) for b in behaviors if "m_stateSetName" in b), "")
        client_only = any(b.get("m_bClientOnly") for b in behaviors if b.get("m_behaviorName") == "CollisionBehavior")
        plain = to_plain(obj)
        needs = (plain.get("m_spawnRequirements") or {}).get("m_requirements") or []
        out.append(
            Placed(
                text(plain.get("m_zoneTag")) or text(template.get("m_objectName")),
                template_id,
                text(template.get("m_objectName")),
                xyz(obj["m_location"]),
                bool(template.get("m_exemptFromAOI")),
                "DeletedBehavior" in names,
                "+".join(text(r.get("m_entryName")) or "?" for r in needs if isinstance(r, dict)),
                text(plain.get("m_startState")),
                bool(client_only),
                state_set,
            )
        )
    return out


@functools.cache
def state_names() -> dict[int, str]:
    """Every state name in Root.wad's state sets, by both the game's string ids katsuba knows."""
    from katsuba.utils import djb2, string_id

    reader = _reader()
    out: dict[int, str] = {}
    for name in reader.root.iter_glob("StateData/**"):
        if not name.endswith(".xml"):
            continue
        try:
            record = to_plain(reader.deserialize(bytes(reader.root[name])))
        except Exception:  # noqa: BLE001 - a state set file that is not BINd
            continue
        for category in (record or {}).get("m_categories") or []:
            for state in (category or {}).get("m_states") or []:
                state_name = text((state or {}).get("m_stateName"))
                if state_name:
                    for hashed in (string_id(state_name), djb2(state_name)):
                        out.setdefault(hashed & 0xFFFFFFFF, state_name)
    return out


def flat(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


async def details(entity, placed: Placed, offset: float) -> Seen:
    """Everything the client holds of a matched gate's object."""
    from wizwalker.constants import Primitive

    seen = Seen(True, offset)
    try:
        template = await entity.object_template()
        seen.name = await template.object_name()
        seen.live_exempt = await template.exempt_from_aoi()
    except Exception as e:  # noqa: BLE001 - reported, not fatal
        seen.name = f"<template unreadable: {type(e).__name__}>"
    kinds, states = [], []
    try:
        for behavior in await entity.inactive_behaviors():
            kind = await behavior.read_type_name()
            kinds.append(kind)
            if "ObjectState" in kind:
                for address in await behavior.read_linked_list(STATE_LIST):
                    value = await behavior.read_typed(address, Primitive.uint32)
                    states.append(state_names().get(value, f"#{value:08x}"))
                seen.override = await behavior.read_string_from_offset(STATE_OVERRIDE)
    except Exception as e:  # noqa: BLE001 - reported, not fatal
        kinds.append(f"<behaviors unreadable: {type(e).__name__}>")
    seen.behaviors = ";".join(kinds)
    seen.states = ";".join(states)
    return seen


@dataclass
class Pass:
    """One read of the entity list."""

    entities: int
    lean_ms: float  # template id and location of every entity: what the walker's pass costs
    detail_ms: float
    farthest_entity: float
    seen: dict[int, Seen]  # by index in the placed list


async def read_pass(client, gates: list[Placed], here: tuple[float, float, float]) -> Pass:
    began = time.perf_counter()
    entities = await client.get_base_entity_list()
    lean = []
    for entity in entities:
        try:
            template = await entity.object_template()
            template_id = await template.template_id() if template is not None else None
            loc = await entity.location()
        except Exception:  # noqa: BLE001 - freed while listed
            continue
        lean.append((entity, template_id, (loc.x, loc.y, loc.z)))
    lean_ms = (time.perf_counter() - began) * 1000
    farthest = max((flat(at, here) for _, _, at in lean), default=0.0)

    began = time.perf_counter()
    seen: dict[int, Seen] = {}
    for k, placed in enumerate(gates):
        best = min(
            ((flat(at, placed.at), entity) for entity, tid, at in lean if tid == placed.template_id),
            key=lambda t: t[0],
            default=None,
        )
        if best is not None and best[0] <= MATCH:
            seen[k] = await details(best[1], placed, best[0])
            continue
        miss = Seen(False)
        close = [entity for entity, _, at in lean if flat(at, placed.at) <= NEAR]
        if close:
            try:
                miss.near = await (await close[0].object_template()).object_name()
            except Exception:  # noqa: BLE001
                miss.near = "<unreadable>"
        seen[k] = miss
    detail_ms = (time.perf_counter() - began) * 1000
    return Pass(len(entities), lean_ms, detail_ms, farthest, seen)


def flags(placed: Placed) -> str:
    return "".join(
        mark
        for mark, on in (
            ("X", placed.exempt),
            ("R", placed.removable),
            ("Q", bool(placed.requirements)),
            ("C", placed.client_only),
        )
        if on
    )


def table(gates: list[Placed], one: Pass, here, pattern: str) -> None:
    print(
        f"\n{one.entities} entities, farthest {one.farthest_entity:.0f} units; "
        f"lean pass {one.lean_ms:.0f} ms, details {one.detail_ms:.0f} ms"
    )
    print("flags: X exempt from AOI, R a quest can remove it, Q spawn requirements, C client-only collision")
    print(f"{'dist':>6} {'dz':>5}  {'listed':6} {'off':>5}  {'flags':5} tag  [template]  states / requirements")
    order = sorted(range(len(gates)), key=lambda k: flat(gates[k].at, here))
    for k in order:
        p, s = gates[k], one.seen[k]
        if pattern and pattern.lower() not in (p.tag + p.template).lower():
            continue
        extra = []
        if s.listed:
            if s.name != p.template:
                extra.append(f"live name {s.name}")
            if s.states:
                extra.append(f"states {s.states}")
            if s.override:
                extra.append(f"override {s.override}")
        elif s.near:
            extra.append(f"near: {s.near}")
        if p.requirements:
            extra.append(f"needs {p.requirements}")
        if p.start_state:
            extra.append(f"starts {p.start_state}")
        off = f"{s.offset:5.0f}" if s.listed else "    -"
        print(
            f"{flat(p.at, here):6.0f} {p.at[2] - here[2]:5.0f}  {'yes' if s.listed else 'no':6} {off}  "
            f"{flags(p):5} {p.tag}  [{p.template}]  {'; '.join(extra)}"
        )


async def main(args: argparse.Namespace) -> int:
    from wizwalker.extensions.wizsprinter import WizSprinter

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    client = clients[args.client - 1]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = REPO / "logs" / "gates" / f"gates-{stamp}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    ranges: dict[int, Range] = {}
    try:
        await client.activate_hooks()
        zone = await client.zone_name()
        print(f"{zone}: reading its gates from the wad...")
        gates = await asyncio.to_thread(placed_gates, zone)
        print(f"{len(gates)} gates, {sum(p.exempt for p in gates)} exempt from AOI")
        await asyncio.to_thread(state_names)
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "t", "zone", "tag", "template_id", "template", "x", "y", "z", "dist", "dz",
                    "exempt", "removable", "requirements", "start_state", "client_only", "state_set",
                    "listed", "offset", "live_name", "live_exempt", "behaviors", "states", "override", "near",
                    "wx", "wy", "wz", "entities", "lean_ms",
                ]
            )  # fmt: skip
            started = time.monotonic()
            last: dict[int, tuple[bool, str]] = {}
            while True:
                pos = await client.body.position()
                here = (pos.x, pos.y, pos.z)
                if await client.zone_name() != zone:
                    print("The zone changed: stopping.")
                    break
                one = await read_pass(client, gates, here)
                t = time.monotonic() - started
                if args.time_mobs:
                    began = time.perf_counter()
                    mobs = await client.get_mobs()
                    print(f"  get_mobs: {len(mobs)} mobs in {(time.perf_counter() - began) * 1000:.0f} ms")
                for k, p in enumerate(gates):
                    s = one.seen[k]
                    d = flat(p.at, here)
                    r = ranges.setdefault(k, Range())
                    if s.listed:
                        r.farthest_listed = max(r.farthest_listed, d)
                        if s.states:
                            r.states.add(s.states)
                    else:
                        r.nearest_unlisted = min(r.nearest_unlisted, d)
                    writer.writerow(
                        [
                            f"{t:.2f}", zone, p.tag, p.template_id, p.template, *(f"{v:.1f}" for v in p.at),
                            f"{d:.0f}", f"{p.at[2] - here[2]:.0f}", p.exempt, p.removable, p.requirements,
                            p.start_state, p.client_only, p.state_set, s.listed,
                            "" if math.isnan(s.offset) else f"{s.offset:.1f}", s.name, s.live_exempt, s.behaviors,
                            s.states, s.override, s.near, *(f"{v:.1f}" for v in here), one.entities,
                            f"{one.lean_ms:.0f}",
                        ]
                    )  # fmt: skip
                    now = (s.listed, s.states)
                    if (
                        args.watch
                        and k in last
                        and last[k] != now
                        and (not args.gate or args.gate.lower() in (p.tag + p.template).lower())
                    ):
                        print(
                            f"  {t:6.1f}s  {p.tag}: {'listed' if s.listed else 'unlisted'} at {d:.0f} units"
                            f"{', states ' + s.states if s.states else ''}; wizard at ({here[0]:.0f}, {here[1]:.0f}, {here[2]:.0f})"
                        )
                    last[k] = now
                f.flush()
                if not args.watch or t >= args.watch:
                    table(gates, one, here, args.gate)
                    break
                if t == 0 or int(t) % 30 < EVERY:
                    print(f"  {t:6.1f}s  {one.entities} entities, lean pass {one.lean_ms:.0f} ms", flush=True)
                await asyncio.sleep(EVERY)
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                pass

    if args.watch:
        print("\nRange over the watch (units, flat): farthest listed / nearest unlisted")
        listed = [r.farthest_listed for r in ranges.values() if r.farthest_listed > -math.inf]
        unlisted = [r.nearest_unlisted for r in ranges.values() if r.nearest_unlisted < math.inf]
        for exempt in (False, True):
            ks = [k for k in ranges if gates[k].exempt == exempt]
            fl = max((ranges[k].farthest_listed for k in ks), default=-math.inf)
            nu = min((ranges[k].nearest_unlisted for k in ks), default=math.inf)
            print(
                f"  {'exempt' if exempt else 'not exempt'}: {len(ks)} gates, farthest listed {fl:.0f}, nearest unlisted {nu:.0f}"
            )
        if listed and unlisted:
            print(f"  all: farthest listed {max(listed):.0f}, nearest unlisted {min(unlisted):.0f}")
        changed = [(gates[k].tag, sorted(r.states)) for k, r in ranges.items() if len(r.states) > 1]
        for tag, states in changed:
            print(f"  state changed: {tag}: {' -> '.join(states)}")
    print(f"\nWrote {out.relative_to(REPO)}")
    return 0


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", type=int, default=1, help="which client, 1-based (default 1)")
    ap.add_argument("--watch", type=float, default=0.0, help="seconds to watch, a pass every 2 s (default: one pass)")
    ap.add_argument("--gate", default="", help="print only gates whose tag or template holds this")
    ap.add_argument("--time-mobs", action="store_true", help="also time wizsprinter's get_mobs() each pass")
    return ap.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
