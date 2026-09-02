"""One-time migration: strip the deprecated combat-order fields from bot files.

Combat order used to be a bot-wide option (``"Default Combat Order"``, an
``int_list``) that ``handle_combat`` read per node, with an optional
``combat_order`` param overriding it for a single fight. The three-level
strategy system moved that setting onto ``DuelConfig.combat_order``, where it
belongs with the rest of a duel's configuration — see ``resolve_combat()``,
which is now the only source of a combat order at runtime.

Nothing outside ``bots/`` has read these fields since that migration, but they
survive in the JSON: the option still renders in the bot options panel, where
editing it does nothing at all.

Two removals per bot:

  a. The ``"Default Combat Order"`` entry in the bot's ``options`` list.
  b. Any ``combat_order`` key in a state's ``params``.

A third removal — the option's key in a state's ``option_overrides`` — was here
until per-node option overrides were retired outright; see
``scripts/migrate_option_overrides.py``, which drops the whole field.

Only the combat *order* fields are touched. The other v2 combat leftovers
(``combat_config``, ``use_bot_default``, ``default_combat_config``,
``use_bartlebot_default_combat_config``) are deliberately left alone — they
carry authored DSL content and are a separate cleanup.

Run from the project root. Reports only by default; pass ``--apply`` to write:

    poetry run python scripts/migrate_combat_order_removal.py
    poetry run python scripts/migrate_combat_order_removal.py --apply

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

OPTION_NAME = "Default Combat Order"
PARAM_NAME = "combat_order"

_stats = {"scanned": 0, "options": 0, "overrides": 0, "params": 0, "written": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Pure transform (no disk I/O — this is what the tests exercise)
# ---------------------------------------------------------------------------


def migrate_bot_data(data: dict) -> tuple[dict, list[str]]:
    """Remove the deprecated combat-order fields from one bot's parsed JSON in place.

    Returns (data, changes). An empty change list means the file is already
    migrated and must not be rewritten.
    """
    changes: list[str] = []

    options = data.get("options")
    if isinstance(options, list):
        kept = [o for o in options if not (isinstance(o, dict) and o.get("name") == OPTION_NAME)]
        if len(kept) != len(options):
            data["options"] = kept
            changes.append(f"removed bot option {OPTION_NAME!r}")

    for state in data.get("states") or []:
        if not isinstance(state, dict):
            continue
        state_id = state.get("id", "?")

        params = state.get("params")
        if isinstance(params, dict) and PARAM_NAME in params:
            del params[PARAM_NAME]
            changes.append(f"state {state_id!r}: removed {PARAM_NAME!r} param")

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
            if "bot option" in note:
                _stats["options"] += 1
            elif "override" in note:
                _stats["overrides"] += 1
            else:
                _stats["params"] += 1

        print(f"  [ok]   {rel}")
        for note in changes:
            print(f"           {note}")

        if apply_changes:
            _write_atomic(path, migrated)
            _stats["written"] += 1

    print(
        f"\nScanned {_stats['scanned']} file(s): "
        f"{_stats['options']} option(s), "
        f"{_stats['overrides']} override(s), "
        f"{_stats['params']} param(s) removed, "
        f"{_stats['errors']} error(s)."
    )
    if apply_changes:
        print(f"Wrote {_stats['written']} file(s).")
    elif _stats["options"] or _stats["overrides"] or _stats["params"]:
        print("Dry run — re-run with --apply to write these changes.")
    else:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
