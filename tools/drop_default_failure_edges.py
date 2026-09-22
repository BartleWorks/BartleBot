"""Remove the failure edges the engine now draws for free.

A node's FAILURE (or FALSE) port with no edge on it falls through to the bot's
recovery state -- see ``BotMachine._fallthrough_dest`` and docs/RETURN_ROUTING.md.
An explicit edge saying the same thing is therefore redundant, and it is not a
small amount of redundancy: 790 of the shipped library's 2799 transitions were
one, which is 28% of every edge drawn on the canvas, nearly all of them long
backward edges converging on a single hub node.

Run once over the library after the default lands:

    uv run python tools/drop_default_failure_edges.py --dry-run
    uv run python tools/drop_default_failure_edges.py

An edge is dropped only when removing it provably changes nothing:

* it is a FALLTHROUGH_RETURNS edge into the bot's own ``recovery_state``;
* it carries no flag guard -- a guarded edge is an authorial statement about
  *when* to recover, and the fall-through cannot express it;
* its source is not the recovery state itself, which the fall-through skips to
  avoid looping the node onto itself;
* it is not the source's last outgoing edge, since a state with no outgoing
  transitions is terminal and stops the run rather than recovering; and
* the source is not a member of a linked subgraph region, whose edges belong to
  the definition in the subgraph library rather than to this file.

This rewrites tracked files in place. It is deliberately a one-shot tool and not
a load-time migration: silently rewriting a user's bot on open is the thing the
options resolution is careful never to do either.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from bartlebot.bot.schema import FALLTHROUGH_RETURNS, BotConfig

BOTS_DIR = Path(__file__).parent.parent / "bots"


def _locked_members(config: BotConfig) -> set[str]:
    """State ids owned by a linked subgraph instance."""
    locked: set[str] = set()
    for region in config.regions:
        if region.source is not None:
            locked.update(region.members)
    return locked


def redundant_edges(config: BotConfig) -> list[int]:
    """Indices into ``config.transitions`` of the edges the default makes redundant."""
    locked = _locked_members(config)
    outgoing: dict[str, int] = {}
    for t in config.transitions:
        outgoing[t.source] = outgoing.get(t.source, 0) + 1

    drop: list[int] = []
    for i, t in enumerate(config.transitions):
        if t.when not in FALLTHROUGH_RETURNS or t.dest != config.recovery_state:
            continue
        if t.flags or t.flag_groups:
            continue
        if t.source == config.recovery_state or t.source in locked:
            continue
        if outgoing[t.source] <= 1:
            continue
        drop.append(i)
        outgoing[t.source] -= 1
    return drop


def process(path: Path, *, dry_run: bool) -> int:
    """Rewrite one bot file. Returns how many edges were dropped."""
    try:
        config = BotConfig.load(path)
    except (ValidationError, ValueError) as exc:
        print(f"  !! {path.name}: {exc}", file=sys.stderr)
        return 0
    drop = set(redundant_edges(config))
    if not drop:
        return 0
    config.transitions = [t for i, t in enumerate(config.transitions) if i not in drop]
    if not dry_run:
        config.save(path)
    return len(drop)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--bots-dir", type=Path, default=BOTS_DIR, help="directory to walk (default: bots/)")
    args = parser.parse_args()

    paths = sorted(args.bots_dir.rglob("*.json"))
    total = 0
    touched = 0
    for path in paths:
        dropped = process(path, dry_run=args.dry_run)
        if dropped:
            touched += 1
            total += dropped
            print(f"  {path.relative_to(args.bots_dir)}: -{dropped}")
    verb = "would drop" if args.dry_run else "dropped"
    print(f"{verb} {total} redundant failure edges across {touched} of {len(paths)} bots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
