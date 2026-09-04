"""Move quicksell's node params into the bot's options, in place.

Phase 6 of the declarations/overrides epic (#150) turns quicksell's five params
into **bot options**: four that describe the user (Enable Quicksell and the three
sell flags) and one that is authorial (the keep list).  A param left in a state's
``params`` block after that is dead -- ``run()`` never looks there again -- so a
bot whose author picked out this dungeon's valuable drops would quietly start
selling them.

Every bot file is in this repo, so this migrates the files rather than the loader.
``CLAUDE.md`` says why load-time rewriting is the wrong tool, and the earlier
quicksell migration is the precedent.

What it does, per bot:

* ``keep_list`` becomes a ``Quicksell Keep List`` declaration -- the author's
  recommendation, which a user's own list then replaces.
* a pinned ``sell_*`` becomes a declaration of that option.  ``null`` meant "use
  the app setting" and still does, so it needs no declaration at all.
* the params block is emptied.

Written back through ``BotConfig.save``, so the save-time prune has the last word:
a declaration that says nothing the NodeSpec does not -- an empty keep list --
comes straight back out, and the file matches what the editor would write.

Two quicksell nodes in one bot can disagree about the keep list, and one bot does
(``hall_of_heroes``).  A bot-wide option cannot hold two answers, so the union is
taken: never sell what an author flagged as valuable in *either* place.  Folding
that bot's two nodes into one is Phase 7's judgment call, not this script's.

    uv run python scripts/migrate_quicksell_options.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from bartlebot.bot.schema import BotConfig, BotOptionModel  # noqa: E402
from bartlebot.nodes.utility import quicksell  # noqa: E402

BOTS_DIR = REPO_ROOT / "bots"


def migrate(config: BotConfig) -> list[str]:
    """Rewrite *config* in place. Returns a line per change, empty if nothing moved."""
    states = [s for s in config.states if s.node_type == "quicksell"]
    if not states:
        return []

    changes: list[str] = []
    keep_list: list[str] = []
    pinned: dict[str, bool] = {}
    for state in states:
        params = state.params or {}
        for keyword in params.get("keep_list") or []:
            if keyword not in keep_list:
                keep_list.append(keyword)
        for key in quicksell.SELL_FLAG_OPTIONS:
            value = params.get(key)
            if isinstance(value, bool):
                # Two states disagreeing on a bool is not expressible either; the
                # library has no such case, so say so loudly rather than picking.
                if pinned.get(key, value) != value:
                    raise SystemExit(f"  states disagree about {key!r}: {pinned[key]} and {value}")
                pinned[key] = value
        if params:
            changes.append(f"  {state.id}: cleared {sorted(params)}")
        state.params = {}

    specs = {spec.name: spec for spec in quicksell.NODE_SPEC.required_bot_options}
    by_name = {o.name: o for o in config.options}

    def declare(name: str, value) -> None:
        spec = specs[name]
        if name in by_name:
            by_name[name].default = value
        else:
            config.options.append(
                BotOptionModel(name=name, type=spec.type, default=value, description=spec.description)
            )
        changes.append(f"  declared {name!r} = {value!r}")

    if keep_list:
        declare(quicksell.OPTION_KEEP_LIST, keep_list)
    for key, value in pinned.items():
        declare(quicksell.SELL_FLAG_OPTIONS[key], value)
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args()

    touched = 0
    for path in sorted(BOTS_DIR.rglob("*.json")):
        config = BotConfig.load(path)
        changes = migrate(config)
        if not changes:
            continue
        touched += 1
        print(path.relative_to(REPO_ROOT))
        for line in changes:
            print(line)
        if not args.dry_run:
            config.save(path)
    print(f"\n{touched} bot(s) {'would be ' if args.dry_run else ''}migrated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
