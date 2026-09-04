"""One-time migration: stamp a wisp-collection timeout, and undo the 0.5 workaround.

A recent game bug can make a client's displayed max HP read far above actual,
so ``calc_health_ratio()`` never clears a collection threshold and
``collect_wisps``'s per-client loop spins indefinitely. Some bots worked
around this by lowering ``if_health_below`` to ``0.5`` instead of the
intended ``1.0`` (top off to full health). Now that ``collect_wisps`` has a
``timeout`` param bounding the loop, that workaround is unnecessary.

This script does two things to each ``collect_wisps`` state under ``bots/``:

  a. Resets ``if_health_below`` from ``0.5`` back to ``1.0``.
  b. Stamps an explicit ``timeout: 10.0`` on any state missing the key, so
     behavior is consistent even for bots nobody hand-edits again.

``fail_if_health_below`` is left untouched — it defaults to ``0.5`` in the
node's own schema and is a distinct field (the post-collection failure
threshold), not part of the workaround.

Run from the project root. Reports only by default; pass ``--apply`` to write:

    uv run python scripts/migrate_wisp_timeout.py
    uv run python scripts/migrate_wisp_timeout.py --apply

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

DEFAULT_TIMEOUT = 10.0

_stats = {"scanned": 0, "hp_reset": 0, "timeout_stamped": 0, "written": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Pure transform (no disk I/O — this is what the tests exercise)
# ---------------------------------------------------------------------------


def migrate_bot_data(data: dict) -> tuple[dict, list[str]]:
    """Apply the wisp-timeout migration to one bot's parsed JSON in place.

    Returns (data, changes). An empty change list means the file is already
    migrated and must not be rewritten.
    """
    changes: list[str] = []
    for state in data.get("states") or []:
        if state.get("node_type") != "collect_wisps":
            continue
        params = state.get("params")
        if not isinstance(params, dict):
            continue

        if params.get("if_health_below") == 0.5:
            params["if_health_below"] = 1.0
            changes.append(f"state {state.get('id', '?')!r}: if_health_below 0.5 -> 1.0")

        if "timeout" not in params:
            params["timeout"] = DEFAULT_TIMEOUT
            changes.append(f"state {state.get('id', '?')!r}: stamped timeout -> {DEFAULT_TIMEOUT}")

    return data, changes


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
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        try:
            migrated, changes = migrate_bot_data(data)
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        if not changes:
            continue

        for note in changes:
            if "if_health_below" in note:
                _stats["hp_reset"] += 1
            else:
                _stats["timeout_stamped"] += 1

        print(f"  [ok]   {rel}")
        for note in changes:
            print(f"           {note}")

        if apply_changes:
            _write_atomic(path, migrated)
            _stats["written"] += 1

    print(
        f"\nScanned {_stats['scanned']} file(s): "
        f"{_stats['hp_reset']} if_health_below reset, "
        f"{_stats['timeout_stamped']} timeout(s) stamped, "
        f"{_stats['errors']} error(s)."
    )
    if apply_changes:
        print(f"Wrote {_stats['written']} file(s).")
    elif _stats["hp_reset"] or _stats["timeout_stamped"]:
        print("Dry run — re-run with --apply to write these changes.")
    else:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
