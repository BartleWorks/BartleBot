"""Renumber a bot file's state ids ``state_1..N`` in canvas order.

The editor never reuses a state id (see ``_unique_id`` in
``bartlebot/gui/canvas/edit_scene.py``), so a bot iterated on for a while ends up
with ids like ``state_8, state_9, state_11, ... state_20`` that say nothing about
where a node sits. This rewrites them left to right, top to bottom, and sorts the
``states`` list to match, so the file reads in the order the canvas does.

**A developer tool, for bots still in initial development.** Combat strategies
bind to raw state ids, and a user's strategies live in their own
``%APPDATA%`` where no renumber of a shipped bot can reach them. Renumbering a bot
people already run silently points their assignments at different nodes. For
that reason this is deliberately not offered in the canvas.

The strategies it *can* reach are rekeyed with it: the repo's
``bot_strategies/`` and this machine's user store, each rewritten in place.

Members of a linked subgraph instance keep their ids -- they are derived from
the region id and the definition's local ids, which the definition owns.

Usage::

    uv run python tools/renumber_states.py bots/templates/boss_farmer_template.json
    uv run python tools/renumber_states.py bots/templates/boss_farmer_template.json --write

Without ``--write`` it prints the mapping and changes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.bot.schema import BotConfig, StateModel  # noqa: E402
from bartlebot.shared.vendor import product_dir  # noqa: E402
from bartlebot.strategy_store import rekey_bot_strategy_states  # noqa: E402
from bartlebot.subgraphs import rename_states  # noqa: E402


def _canvas_key(state: StateModel) -> tuple[float, float]:
    """Left to right, then top to bottom. A state with no position sorts last."""
    if state.position is None:
        return (float("inf"), float("inf"))
    return (float(state.position.get("x", 0.0)), float(state.position.get("y", 0.0)))


def renumber_map(config: BotConfig) -> dict[str, str]:
    """``{old id: new id}`` for every state that changes, numbering in canvas order.

    Instance members are skipped and keep their ids. Raises ``ValueError`` if a new
    id would collide with one of those kept ids.
    """
    locked = {m for r in config.regions if r.source is not None for m in r.members}
    ordered = sorted((s for s in config.states if s.id not in locked), key=_canvas_key)
    full = {s.id: f"state_{n}" for n, s in enumerate(ordered, start=1)}
    clashes = locked & set(full.values())
    if clashes:
        raise ValueError(f"new ids would collide with subgraph member ids: {sorted(clashes)}")
    return {old: new for old, new in full.items() if old != new}


def renumber(config: BotConfig) -> dict[str, str]:
    """Renumber ``config`` in place and sort its states to canvas order. Returns the id map."""
    id_map = renumber_map(config)
    rename_states(config, id_map)
    config.states.sort(key=_canvas_key)
    return id_map


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bot", type=Path, help="bot JSON file to renumber")
    ap.add_argument("--write", action="store_true", help="save the bot and rekey its combat strategies")
    args = ap.parse_args(argv)

    config = BotConfig.load(args.bot)
    before = [s.id for s in config.states]
    id_map = renumber(config)
    after = [s.id for s in config.states]

    if not id_map and before == after:
        print("Already numbered in canvas order; nothing to do.")
        return 0

    labels = {s.id: s.label or s.node_type for s in config.states}
    for new in after:
        old = next((o for o, n in id_map.items() if n == new), new)
        shown = f"{old:>12} -> {new:<12}" if old != new else f"{'':>12}    {new:<12}"
        print(f"{shown}  {labels[new]}")

    if not args.write:
        print("\nDry run. Pass --write to save.")
        return 0

    config.save(args.bot)
    print(f"\nSaved {args.bot}")
    for root in (PROJECT_ROOT, product_dir()):
        n = rekey_bot_strategy_states(config.id, id_map, root=root)
        if n:
            print(f"Rekeyed {n} combat strateg{'y' if n == 1 else 'ies'} under {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
