"""One-time migration: convert bare drop names into structured drop entries.

``BotConfig.drops`` used to be a plain ``list[str]``.  It is now a list of
``DropModel`` objects carrying the item's wiki category, so the quicksell keep-list
picker can filter and sort by category:

    "drops": ["Amber"]              ->  "drops": [{"name": "Amber"}]

The schema still *accepts* bare names on load, so an unmigrated file is not broken —
it just rewrites itself into the structured form the next time the app saves it.  This
script does that rewrite up front, so the whole library changes shape in one reviewable
commit rather than a file at a time as bots happen to get edited.

Categories cannot be backfilled: they only exist on the Wizard101 Central page the
drops were imported from, and nothing on disk records which page that was.  Migrated
drops stay uncategorized until the bot is re-imported from the wiki.

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

_stats = {"scanned": 0, "converted": 0, "written": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_drops(data: dict) -> int:
    """Convert bare-string drops in *data* to entry dicts, in place.

    Returns the number of drops converted.  Zero means the file is already migrated
    and must not be rewritten.  Order is preserved, and entries that are already
    dicts are left exactly as they are — including any category they carry.
    """
    drops = data.get("drops")
    if not isinstance(drops, list):
        return 0

    converted = 0
    new_drops = []
    for drop in drops:
        if isinstance(drop, str):
            new_drops.append({"name": drop})
            converted += 1
        else:
            new_drops.append(drop)

    if converted:
        data["drops"] = new_drops
    return converted


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
            converted = migrate_drops(data)
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        if not converted:
            continue

        _stats["converted"] += converted
        print(f"  [ok]   {rel}: {converted} drop(s) -> entry form")

        if apply_changes:
            _write_atomic(path, data)
            _stats["written"] += 1

    print(
        f"\nScanned {_stats['scanned']} file(s): {_stats['converted']} drop(s) converted, {_stats['errors']} error(s)."
    )
    if apply_changes:
        print(f"Wrote {_stats['written']} file(s).")
    elif _stats["converted"]:
        print("Dry run — re-run with --apply to write these changes.")
    else:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
