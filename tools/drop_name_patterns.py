"""Survey the trailing "(...)" suffixes on drop names across the bot library.

A Wizard101 Central Wiki page cannot list two items under one title, so it disambiguates
variants with a suffix — "(60, Death)", "(Level 90+)", "(Triangle)". The game does
not need to, and mostly does not show them. Anything matching item names against
text read off the game's UI has to work in the in-game form, which
``bartlebot/wiki_drops.py`` derives.

That derivation is a lookup table of suffix kinds, so it fails in one specific way:
a wiki page introduces a suffix nobody has classified, and the drop silently keeps a
name the game never shows — a keep-list entry that matches nothing, and an item that
sells when the user asked to keep it. This reports those.

    uv run python tools/drop_name_patterns.py
    uv run python tools/drop_name_patterns.py --show-names
    uv run python tools/drop_name_patterns.py --json

Exits non-zero when a suffix is unclassified, so it can be used as a check.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.wiki_drops import (  # noqa: E402
    KIND_NONE,
    KIND_UNCLASSIFIED,
    WIKI_ONLY_KINDS,
    classify_suffix,
    match_name_for,
    suffix_of,
)


def scan(bots_dir: Path) -> tuple[Counter, dict[str, list[tuple[str, str, str | None]]], Counter]:
    """Read every bot under *bots_dir*.

    Returns (drops per suffix kind, examples per kind, count of names whose stored
    ``match_name`` disagrees with what the rules derive today).
    """
    per_kind: Counter = Counter()
    examples: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    stale: Counter = Counter()

    for path in sorted(bots_dir.rglob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"  [ERR]  {path}: {exc}", file=sys.stderr)
            continue

        rel = str(path.relative_to(bots_dir))
        for drop in data.get("drops") or []:
            # Bare strings are the pre-category storage form and have no category.
            name = drop.get("name") if isinstance(drop, dict) else drop
            if not isinstance(name, str):
                continue
            category = drop.get("category") if isinstance(drop, dict) else None
            suffix = suffix_of(name)
            kind = classify_suffix(suffix)
            if kind == KIND_NONE:
                continue
            per_kind[kind] += 1
            if len(examples[kind]) < 3:
                examples[kind].append((name, rel, category))

            stored = drop.get("match_name") if isinstance(drop, dict) else None
            if stored != match_name_for(name, category):
                stale[kind] += 1

    return per_kind, examples, stale


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show-names", action="store_true", help="list example item names under each kind")
    parser.add_argument("--json", action="store_true", help="emit the raw counts as JSON")
    args = parser.parse_args()

    bots_dir = PROJECT_ROOT / "bots"
    if not bots_dir.exists():
        print(f"ERROR: bots/ directory not found at {bots_dir}")
        sys.exit(1)

    per_kind, examples, stale = scan(bots_dir)
    if args.json:
        print(
            json.dumps(
                {
                    kind: {
                        "drops": n,
                        "wiki_only": kind in WIKI_ONLY_KINDS,
                        "needs_migration": stale[kind],
                        "examples": [name for name, _, _ in examples[kind]],
                    }
                    for kind, n in per_kind.items()
                },
                indent=2,
            )
        )
        return

    total = sum(per_kind.values())
    print(f"{total} drop name(s) carry a trailing suffix\n")
    print(f"{'drops':>7}  {'shown in-game':<14} suffix kind")
    for kind, count in per_kind.most_common():
        shown = "no" if kind in WIKI_ONLY_KINDS else "yes / n/a"
        print(f"{count:7d}  {shown:<14} {kind}")
        if args.show_names:
            for name, bot, category in examples[kind]:
                derived = match_name_for(name, category)
                arrow = f" -> {derived!r}" if derived else ""
                print(f"           {name!r}{arrow}  [{category or 'uncategorized'}, {bot}]")

    outstanding = sum(stale.values())
    if outstanding:
        # Not an error: the rules are the source of truth and the files catch up.
        print(f"\n{outstanding} drop(s) have no stored in-game name, or a stale one.")
        print("Run: uv run python scripts/migrate_drop_entries.py --apply")

    unclassified = per_kind.get(KIND_UNCLASSIFIED, 0)
    if unclassified:
        print(f"\n{unclassified} drop(s) carry a suffix no rule in bartlebot/wiki_drops.py accounts for.")
        print("Decide whether the game shows it, then add a rule or list it in KNOWN_KEPT_SUFFIXES:")
        for name, bot, category in examples[KIND_UNCLASSIFIED]:
            print(f"  {name!r}  [{category or 'uncategorized'}, {bot}]")
        sys.exit(1)

    print("\nEvery suffix in the library is classified.")


if __name__ == "__main__":
    main()
