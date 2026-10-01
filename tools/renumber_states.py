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

Members of a linked subgraph instance keep their local ids -- they are derived
from the region id and the definition's local ids, which the definition owns.

``--prefix t_`` numbers in the template scheme instead (see
``bartlebot/template_store.py``): nodes become ``t_1..N``, and each subgraph
instance's region id gains the prefix too (``sync_players_1`` becomes
``t_sync_players_1``), carrying its members' ids with it. Only for a draft that
has never been released: a released id is never renamed or reused.

Usage::

    uv run python tools/renumber_states.py bots/01_wizard_city/some_bot.json
    uv run python tools/renumber_states.py bots/01_wizard_city/some_bot.json --write
    uv run python tools/renumber_states.py templates/boss-farmer/draft.json --prefix t_

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
from bartlebot.subgraphs import is_nested_in, local_id, member_id, outermost_linked_regions, rename_states  # noqa: E402

#: The prefix a bot's nodes are numbered with; see ``--prefix``.
DEFAULT_PREFIX = "state_"


def _canvas_key(state: StateModel) -> tuple[float, float]:
    """Left to right, then top to bottom. A state with no position sorts last."""
    if state.position is None:
        return (float("inf"), float("inf"))
    return (float(state.position.get("x", 0.0)), float(state.position.get("y", 0.0)))


def instance_prefix_for(prefix: str) -> str:
    """What an instance region id carries under ``prefix``: nothing in a bot, ``t_`` in a template."""
    return "" if prefix == DEFAULT_PREFIX else prefix


def prefix_instances(config: BotConfig, prefix: str) -> dict[str, str]:
    """Give every outermost subgraph instance's region id ``prefix``, in place. Returns the state id map.

    Its members, and any instance nested in it, are renamed to match, since their ids are
    derived from the region id. An instance already carrying the prefix is left alone.
    """
    id_map: dict[str, str] = {}
    if not prefix:
        return id_map
    for region in outermost_linked_regions(config.regions):
        old = region.id
        if old.startswith(prefix):
            continue
        new = f"{prefix}{old}"
        members = set(region.members)
        for inner in config.regions:
            if inner is region or (inner.source is not None and is_nested_in(inner, region)):
                inner.id = new if inner is region else member_id(new, local_id(old, inner.id) or inner.id)
        id_map.update({m: member_id(new, local_id(old, m) or m) for m in members})
    rename_states(config, id_map)
    return id_map


def renumber_map(config: BotConfig, prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """``{old id: new id}`` for every state that changes, numbering ``<prefix>N`` in canvas order.

    Instance members are skipped and keep their ids. Raises ``ValueError`` if a new
    id would collide with one of those kept ids.
    """
    locked = {m for r in config.regions if r.source is not None for m in r.members}
    ordered = sorted((s for s in config.states if s.id not in locked), key=_canvas_key)
    full = {s.id: f"{prefix}{n}" for n, s in enumerate(ordered, start=1)}
    clashes = locked & set(full.values())
    if clashes:
        raise ValueError(f"new ids would collide with subgraph member ids: {sorted(clashes)}")
    return {old: new for old, new in full.items() if old != new}


def renumber(config: BotConfig, prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """Renumber ``config`` in place and sort its states to canvas order. Returns the id map.

    Under a prefix other than ``state_``, instance region ids gain it first
    (:func:`prefix_instances`), and the map covers their members too.
    """
    id_map = prefix_instances(config, instance_prefix_for(prefix))
    numbered = renumber_map(config, prefix)
    rename_states(config, numbered)
    config.states.sort(key=_canvas_key)
    # Disjoint: renumber_map never touches an instance member, and the prefix touches nothing else.
    return {**id_map, **numbered}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bot", type=Path, help="bot JSON file to renumber")
    ap.add_argument("--write", action="store_true", help="save the bot and rekey its combat strategies")
    ap.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help="id prefix: state_ for a bot (the default), t_ for a template draft that was never released",
    )
    args = ap.parse_args(argv)

    config = BotConfig.load(args.bot)
    before = [s.id for s in config.states]
    id_map = renumber(config, args.prefix)
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
