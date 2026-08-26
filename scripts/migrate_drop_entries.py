"""One-time migration: convert bare drop names into structured drop entries.

``BotConfig.drops`` used to be a plain ``list[str]``.  It is now a list of
``DropModel`` objects carrying the item's wiki category, so the quicksell keep-list
picker can filter and sort by category:

    "drops": ["Amber"]              ->  "drops": [{"name": "Amber"}]

The schema still *accepts* bare names on load, so an unmigrated file is not broken —
it just rewrites itself into the structured form the next time the app saves it.  This
script does that rewrite up front, so the whole library changes shape in one reviewable
commit rather than a file at a time as bots happen to get edited.

It also backfills ``match_name``: the name the *game* shows, where the wiki's title
carries a disambiguating suffix the game does not.  Unlike the category, this one is
derivable from what is already on disk — ``bartlebot/wiki_drops.py`` computes it from
the name and, where the rule needs one, the category::

    {"name": "Balance Crushing Pin (60, Death)"}
      ->  {"name": "Balance Crushing Pin (60, Death)",
           "match_name": "Balance Crushing Pin (60)"}

Categories cannot be backfilled: they only exist on the Wizard101 Central page the
drops were imported from, and nothing on disk records which page that was.  Migrated
drops stay uncategorized until the bot is re-imported from the wiki — and because two
of the ``match_name`` rules are gated on the category, an uncategorized drop can gain
one on re-import later.

Run from the project root.  Reports only by default; pass ``--apply`` to write:

    poetry run python scripts/migrate_drop_entries.py
    poetry run python scripts/migrate_drop_entries.py --apply

Idempotent: a second run is a clean no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.wiki_drops import match_name_for  # noqa: E402

_stats = {"scanned": 0, "converted": 0, "matched": 0, "written": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_drops(data: dict) -> tuple[int, int]:
    """Bring *data*'s drops up to date, in place.

    Converts bare-string drops to entry dicts and fills in ``match_name`` wherever
    the wiki name differs from the in-game one.  Returns ``(converted, matched)``;
    ``(0, 0)`` means the file is already current and must not be rewritten.

    Order is preserved, categories are never touched, and an existing ``match_name``
    is left alone — someone may have corrected one by hand, and re-deriving it would
    silently undo that.
    """
    drops = data.get("drops")
    if not isinstance(drops, list):
        return 0, 0

    converted = matched = 0
    new_drops = []
    for drop in drops:
        if isinstance(drop, str):
            drop = {"name": drop}
            converted += 1
        if isinstance(drop, dict) and "match_name" not in drop and isinstance(drop.get("name"), str):
            match_name = match_name_for(drop["name"], drop.get("category"))
            if match_name is not None:
                drop = {**drop, "match_name": match_name}
                matched += 1
        new_drops.append(drop)

    if converted or matched:
        data["drops"] = new_drops
    return converted, matched


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    """Path relative to the project root for display, falling back to the raw path."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _write_atomic(path: Path, data: dict) -> None:
    """Atomic replace, matching the formatting used elsewhere in the project."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> None:
    # The Windows console defaults to cp1252, which cannot encode the non-ASCII
    # punctuation in this module's docstring (printed by --help).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    parser.add_argument("--dry-run", action="store_true", help="explicit no-op default; report only")
    args = parser.parse_args()
    apply_changes = args.apply and not args.dry_run

    bots_dir = PROJECT_ROOT / "bots"
    if not bots_dir.exists():
        print(f"ERROR: bots/ directory not found at {bots_dir}")
        sys.exit(1)

    bot_files = sorted(bots_dir.rglob("*.json"))
    print(f"Found {len(bot_files)} bot file(s) under {bots_dir}")
    print(f"Mode: {'APPLY (writing changes)' if apply_changes else 'DRY RUN (no files will be modified)'}\n")

    for path in bot_files:
        _stats["scanned"] += 1
        rel = _rel(path)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            converted, matched = migrate_drops(data)
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        if not converted and not matched:
            continue

        _stats["converted"] += converted
        _stats["matched"] += matched
        changes = []
        if converted:
            changes.append(f"{converted} drop(s) -> entry form")
        if matched:
            changes.append(f"{matched} in-game name(s) filled in")
        print(f"  [ok]   {rel}: {', '.join(changes)}")

        if apply_changes:
            _write_atomic(path, data)
            _stats["written"] += 1

    print(
        f"\nScanned {_stats['scanned']} file(s): {_stats['converted']} drop(s) converted, "
        f"{_stats['matched']} in-game name(s) filled in, {_stats['errors']} error(s)."
    )
    if apply_changes:
        print(f"Wrote {_stats['written']} file(s).")
    elif _stats["converted"] or _stats["matched"]:
        print("Dry run — re-run with --apply to write these changes.")
    else:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
