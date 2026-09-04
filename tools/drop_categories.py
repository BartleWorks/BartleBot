"""Survey the drop categories used across the bot library.

Drop categories come from whatever heading the Wizard101 Central page grouped an
item under, so the vocabulary is discovered rather than declared.  Two things go
wrong quietly:

  * A page parses into an unexpected label ("Other", a truncated heading), which
    then shows up as its own entry in the picker's category dropdown.
  * A genuinely new category appears that nobody has classified as quicksellable
    or not, so the picker silently treats it as sellable.

This reports both, against the classification in
``bartlebot/nodes/utility/quicksell.py``.  Run it after importing drops for new
bots::

    uv run python tools/drop_categories.py
    uv run python tools/drop_categories.py --by-bot
    uv run python tools/drop_categories.py --json

Exits non-zero when a category is unclassified, so it can be used as a check.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.nodes.utility.quicksell import (  # noqa: E402
    NEVER_QUICKSOLD_CATEGORIES,
    QUICKSELLABLE_CATEGORIES,
)

_UNCATEGORIZED = "(uncategorized)"


def scan(bots_dir: Path) -> tuple[Counter, dict[str, set[str]], dict[str, tuple[int, int]]]:
    """Read every bot under *bots_dir*.

    Returns (drops per category, bot names per category, {bot: (total, uncategorized)}).
    """
    per_category: Counter = Counter()
    bots_using: dict[str, set[str]] = defaultdict(set)
    per_bot: dict[str, tuple[int, int]] = {}

    for path in sorted(bots_dir.rglob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"  [ERR]  {path}: {exc}", file=sys.stderr)
            continue

        drops = data.get("drops") or []
        if not isinstance(drops, list) or not drops:
            continue

        rel = str(path.relative_to(bots_dir))
        uncategorized = 0
        for drop in drops:
            # Bare strings are the pre-category storage form; count them as uncategorized.
            category = drop.get("category") if isinstance(drop, dict) else None
            if not category:
                uncategorized += 1
                category = _UNCATEGORIZED
            per_category[category] += 1
            bots_using[category].add(rel)
        per_bot[rel] = (len(drops), uncategorized)

    return per_category, bots_using, per_bot


def classify(category: str) -> str:
    if category == _UNCATEGORIZED:
        return "unknown"
    if category in NEVER_QUICKSOLD_CATEGORIES:
        return "never sold"
    if category in QUICKSELLABLE_CATEGORIES:
        return "sellable"
    return "UNCLASSIFIED"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--by-bot", action="store_true", help="also list each bot's uncategorized drop count")
    parser.add_argument("--json", action="store_true", help="emit the raw counts as JSON")
    args = parser.parse_args()

    bots_dir = PROJECT_ROOT / "bots"
    if not bots_dir.exists():
        print(f"ERROR: bots/ directory not found at {bots_dir}")
        sys.exit(1)

    per_category, bots_using, per_bot = scan(bots_dir)
    if args.json:
        print(
            json.dumps(
                {
                    "categories": {
                        c: {"drops": n, "bots": len(bots_using[c]), "class": classify(c)}
                        for c, n in per_category.items()
                    },
                    "bots": {b: {"drops": t, "uncategorized": u} for b, (t, u) in per_bot.items()},
                },
                indent=2,
            )
        )
        return

    total = sum(per_category.values())
    print(f"{len(per_bot)} bot(s) with drops, {total} drop(s) total\n")

    print(f"{'drops':>7} {'bots':>5}  {'classification':<14} category")
    for category, count in per_category.most_common():
        print(f"{count:7d} {len(bots_using[category]):5d}  {classify(category):<14} {category}")

    never = sum(n for c, n in per_category.items() if classify(c) == "never sold")
    unknown = per_category.get(_UNCATEGORIZED, 0)
    categorized = total - unknown
    if categorized:
        # Against the categorized drops, not the whole library: an uncategorized
        # drop is unknown, not sellable, and averaging it in understates the share.
        print(f"\n{never} of {categorized} categorized drop(s) ({never / categorized:.0%}) are never quicksold.")
    if total:
        print(f"{unknown} of {total} drop(s) ({unknown / total:.0%}) have no category — re-import those bots.")

    if args.by_bot:
        print("\nUncategorized drops by bot:")
        for bot, (bot_total, uncat) in sorted(per_bot.items(), key=lambda kv: -kv[1][1]):
            if uncat:
                print(f"{uncat:7d} / {bot_total:<6d} {bot}")

    unclassified = sorted(c for c in per_category if classify(c) == "UNCLASSIFIED")
    if unclassified:
        print("\nUNCLASSIFIED categories — add each to NEVER_QUICKSOLD_CATEGORIES or")
        print("QUICKSELLABLE_CATEGORIES in bartlebot/nodes/utility/quicksell.py:")
        for category in unclassified:
            example = sorted(bots_using[category])[0]
            print(f"  {category!r}  ({per_category[category]} drops, e.g. {example})")
        sys.exit(1)

    print("\nEvery category is classified.")


if __name__ == "__main__":
    main()
