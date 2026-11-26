"""Diagnostic: how far from a reagent can a wizard stand and still get its pickup prompt? (#322)

A pickup walks now (Stage 5 of #322), and a walk stops within 50 units of its goal, not on it.
Two of the pickup's rules stand a wizard off the spawn on purpose: a spawn inside a zone exit is
picked up from the floor just outside it, and one beside an object named in Keep Away From
from its far side. Both rest on node_helpers.PICKUP_REACH, which this probe measures.

It writes nothing to the game: it only reads. You walk; it watches.

HOW TO RUN
    1. Log a wizard in, next to a reagent of the kind you want to measure, with no other object
       that shows a prompt nearby (an NPC, a sign, a portal): the prompt read here is the game's
       one flag for "some prompt is showing", whose it is not said.
    2. Start the probe, naming the reagent as the Entity Browser does:

         uv run --no-sync python tools/probe_pickup_reach.py --entity "Reagent_AethyrBurst*"

    3. Walk straight away from the reagent until the prompt goes, then back until it shows, a few
       times and from a few sides. Walk slowly near the edge.
    4. Ctrl+C prints the summary.

Each run writes its samples to its own tools/probe_pickup_reach.<time>.local.csv (gitignored).
Re-read one or several, pooled, without the game running:

         uv run --no-sync python tools/probe_pickup_reach.py --analyze tools/probe_pickup_reach.*.local.csv

The game's prompt flag lags the walk both ways: walking out, the prompt stays on a little past
the edge, and walking in, it comes on a little inside it. So the summary brackets the edge from
the crossings: the prompt came on no farther out than one distance, so the edge is at least
that; and went off no nearer than another, so the edge is at most that. The farthest it showed
and the nearest it was gone come after, overlapping by the lag. Distances are 2D (what the
pickup's rules measure). PICKUP_REACH goes a little inside the bracket's low end.

OUTCOME, 2026-11-25: Nightside's FrostedFlax_01, 17 times out and in over two runs. On at 150 to
285, off at 300 to 467 (further each way the faster the walk), with one flicker on at 325 and off
at 327, so the edge is about 285 to 300. PICKUP_REACH is 275.

Wizard names and ids are never read or printed. Reagent template names are game data and are.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SAMPLE_INTERVAL = 0.1
#: Entities are listed this often; only their positions are read on every sample.
LIST_REFRESH = 2.0
CSV_FIELDS = ["t", "x", "y", "z", "prompt", "entity", "ex", "ey", "ez"]


@dataclass
class Sample:
    t: float
    at: tuple[float, float, float]
    prompt: bool
    entity: str
    spot: tuple[float, float, float]

    @property
    def flat(self) -> float:
        return math.dist(self.at[:2], self.spot[:2])

    @property
    def full(self) -> float:
        return math.dist(self.at, self.spot)


def summarize(samples: list[Sample]) -> str:
    """The farthest the prompt showed and the nearest it was gone, per reagent and in all."""
    if not samples:
        return "No samples."
    lines = []
    by_entity: dict[str, list[Sample]] = {}
    for sample in samples:
        by_entity.setdefault(sample.entity, []).append(sample)
    for name, group in sorted(by_entity.items()):
        lines.append(f"{name}: {len(group)} samples")
        lines.extend(_edges(group))
    if len(by_entity) > 1:
        lines.append(f"All reagents: {len(samples)} samples")
        lines.extend(_edges(samples))
    return "\n".join(lines)


def _edges(samples: list[Sample]) -> list[str]:
    shown = [s for s in samples if s.prompt]
    gone = [s for s in samples if not s.prompt]
    lines = []
    # A crossing is between samples of one run: pooled runs each start their clock at 0.
    steps = [(a, b) for a, b in zip(samples, samples[1:]) if b.t > a.t]
    came_on = [b.flat for a, b in steps if b.prompt and not a.prompt]
    went_off = [b.flat for a, b in steps if a.prompt and not b.prompt]
    if came_on and went_off:
        low, high = max(came_on), min(went_off)
        lines.append(
            f"  the edge: on no farther out than {low:.0f}, off no nearer than {high:.0f}"
            f" ({len(came_on)} on, {len(went_off)} off)"
        )
        if low > high:
            lines.append("  they cross: a flicker, or the edge depends on the side; look at the crossings in the CSV")
    if shown:
        far = max(shown, key=lambda s: s.flat)
        lines.append(f"  farthest with the prompt: {far.flat:.0f} (3D {far.full:.0f})")
    else:
        lines.append("  the prompt never showed")
    if gone:
        near = min(gone, key=lambda s: s.flat)
        lines.append(f"  nearest without it:       {near.flat:.0f} (3D {near.full:.0f})")
    if shown and gone and min(s.flat for s in gone) < max(s.flat for s in shown):
        lines.append("  the two overlap: another prompt showing, or the edge depends on the side")
    return lines


def read_csvs(paths: list[Path]) -> list[Sample]:
    samples = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                samples.append(
                    Sample(
                        float(row["t"]),
                        (float(row["x"]), float(row["y"]), float(row["z"])),
                        row["prompt"] == "1",
                        row["entity"],
                        (float(row["ex"]), float(row["ey"]), float(row["ez"])),
                    )
                )
    return samples


async def record(client, pattern: str, writer, samples: list[Sample]) -> None:
    from bartlebot.nodes.node_helpers import entity_name_predicate  # noqa: PLC0415

    predicate = entity_name_predicate(pattern)
    entities: list = []
    listed = -math.inf
    started = time.monotonic()
    while True:
        now = time.monotonic()
        if now - listed >= LIST_REFRESH:
            entities = await client.get_base_entities_with_predicate(predicate)
            listed = now
        pos = await client.body.position()
        at = (pos.x, pos.y, pos.z)
        nearest = None
        for entity in entities:
            try:
                loc = await entity.location()
                name = await entity.object_name()
            except Exception:  # noqa: BLE001 - freed since it was listed: picked up, or despawned
                continue
            spot = (loc.x, loc.y, loc.z)
            if nearest is None or math.dist(at[:2], spot[:2]) < math.dist(at[:2], nearest[1][:2]):
                nearest = (name, spot)
        if nearest is not None:
            sample = Sample(now - started, at, bool(await client.is_in_npc_range()), *nearest)
            samples.append(sample)
            writer.writerow([f"{sample.t:.2f}", *at, int(sample.prompt), sample.entity, *sample.spot])
            print(
                f"\r{sample.entity[:40]:<40} {sample.flat:7.0f} units  prompt {'yes' if sample.prompt else 'no '}",
                end="",
                flush=True,
            )
        else:
            print(f"\rno {pattern!r} in the entity list" + " " * 30, end="", flush=True)
        await asyncio.sleep(SAMPLE_INTERVAL)


async def main(args: argparse.Namespace) -> int:
    if args.analyze:
        paths = [Path(p) for pattern in args.analyze for p in sorted(glob.glob(pattern))]
        if not paths:
            print(f"No CSV matches {args.analyze}")
            return 2
        print("Pooling " + ", ".join(p.name for p in paths))
        print(summarize(read_csvs(paths)))
        return 0
    if not args.entity:
        print("Name the reagent with --entity, as the Entity Browser shows it.")
        return 2

    csv_path = Path(__file__).with_name(f"probe_pickup_reach.{time.strftime('%Y%m%d-%H%M%S')}.local.csv")
    from wizwalker.extensions.wizsprinter import WizSprinter  # noqa: PLC0415

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= args.client <= len(clients):
        print(f"No client {args.client}; have {len(clients)}.")
        return 2
    client = clients[args.client - 1]

    samples: list[Sample] = []
    try:
        await client.activate_hooks()
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)
            try:
                await record(client, args.entity, writer, samples)
            except asyncio.CancelledError:
                print("\n\nStopped by Ctrl+C.")
    finally:
        for c in clients:
            try:
                await c.close()
            except Exception:  # noqa: BLE001, S110 - closing on the way out; nothing to do about it
                pass

    print(f"Samples written to {csv_path}")
    print(summarize(samples))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--entity", help="The reagent's Entity ID, as the Entity Browser shows it; * matches any run.")
    parser.add_argument("--client", type=int, default=1, help="Which client to read, 1-based (default 1).")
    parser.add_argument("--analyze", metavar="CSV", nargs="+", help="Summarize saved CSVs (globs allowed), pooled.")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
