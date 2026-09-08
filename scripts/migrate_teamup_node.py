"""One-time migration: fold ``enter_dungeon_teamup`` back into ``enter_dungeon``.

Team-Up was a second copy of ``enter_dungeon`` that differed only in its tail, so the
two drifted: ``sister_sanderson_teamup.json`` lost both ``check_zone`` states its
sibling has and gained a Quicksell Keep List its sibling does not. One node with a
switch cannot drift from itself.

The switch is the shape ``offers_challenge_mode`` established -- an author **param**
for the fact only the author knows, gating a user-owned **bot option**:

  * ``offers_team_up``      param, ticked per bot: this sigil offers Team-Up, and this
                            is a run a stranger can reasonably be matched into.
  * ``Team-Up``             bot option, ``required_when="offers_team_up"``: whether the
                            user wants to queue rather than enter alone.
  * ``Minimum Team Size``   bot option, replacing the old ``minimum_team_size`` param.
                            How the user wants to run, not a fact about this sigil, so
                            per ``docs/BOT_OPTIONS.md`` it is an option.

So each ``enter_dungeon_teamup`` state becomes an ``enter_dungeon`` state with
``offers_team_up: true``. A ``minimum_team_size`` other than the option's default is
carried across as an authorial declaration of ``Minimum Team Size``; the default is
dropped, since a declaration equal to the node's own says nothing.

Note what this migration cannot do: it makes a converted bot *able* to team up, but the
user still has to turn the ``Team-Up`` option on. That is the point of the split, not an
oversight -- a bot file no longer decides that a run queues.

Run from the project root. Reports only by default; pass ``--apply`` to write:

    uv run python scripts/migrate_teamup_node.py
    uv run python scripts/migrate_teamup_node.py --apply

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

LEGACY_NODE_TYPE = "enter_dungeon_teamup"
NODE_TYPE = "enter_dungeon"
PARAM_OFFERS_TEAM_UP = "offers_team_up"
LEGACY_PARAM_TEAM_SIZE = "minimum_team_size"
OPTION_MINIMUM_TEAM_SIZE = "Minimum Team Size"
DEFAULT_TEAM_SIZE = 2

_stats = {"scanned": 0, "states": 0, "options": 0, "written": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Pure transform (no disk I/O — this is what the tests exercise)
# ---------------------------------------------------------------------------


def migrate_bot_data(data: dict) -> tuple[dict, list[str]]:
    """Apply the Team-Up merge to one bot's parsed JSON in place.

    Returns (data, changes). An empty change list means the file has no team-up
    states and must not be rewritten.
    """
    changes: list[str] = []
    team_size: int | None = None

    for state in data.get("states") or []:
        if state.get("node_type") != LEGACY_NODE_TYPE:
            continue
        state["node_type"] = NODE_TYPE
        params = state.get("params")
        if not isinstance(params, dict):
            params = {}
            state["params"] = params
        params[PARAM_OFFERS_TEAM_UP] = True

        # The param is the user's now. Keep the largest one any state asked for, so a
        # bot with two differently-sized sigils does not silently shrink its team.
        stored = params.pop(LEGACY_PARAM_TEAM_SIZE, None)
        if isinstance(stored, int) and not isinstance(stored, bool):
            team_size = stored if team_size is None else max(team_size, stored)

        # ASCII only: these notes are printed, and a Windows console is cp1252.
        changes.append(f"{state.get('id', '?')}: {LEGACY_NODE_TYPE} -> {NODE_TYPE} with {PARAM_OFFERS_TEAM_UP}")

    if not changes:
        return data, changes

    # A declaration equal to the node's own default says nothing, and the save-time
    # prune would drop it again on the next save anyway.
    if team_size is not None and team_size != DEFAULT_TEAM_SIZE:
        options = data.setdefault("options", [])
        if not any(opt.get("name") == OPTION_MINIMUM_TEAM_SIZE for opt in options):
            options.append({"name": OPTION_MINIMUM_TEAM_SIZE, "type": "int", "default": team_size})
            changes.append(f"declared {OPTION_MINIMUM_TEAM_SIZE} = {team_size}")

    return data, changes


# ---------------------------------------------------------------------------
# Disk walk
# ---------------------------------------------------------------------------


def _write_atomic(path: Path, data: dict) -> None:
    """Write *data* to *path* through a temp file, so a crash cannot truncate a bot."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    parser.add_argument("--bots-dir", default=str(PROJECT_ROOT / "bots"), help="directory of bot files to scan")
    args = parser.parse_args()

    bots_dir = Path(args.bots_dir)
    if not bots_dir.is_dir():
        print(f"No such directory: {bots_dir}")
        sys.exit(1)

    print(f"Scanning {bots_dir}...")
    for path in sorted(bots_dir.rglob("*.json")):
        rel = path.relative_to(bots_dir).as_posix()
        _stats["scanned"] += 1
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            migrated, changes = migrate_bot_data(data)
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        if not changes:
            continue

        for note in changes:
            if note.startswith("declared "):
                _stats["options"] += 1
            else:
                _stats["states"] += 1

        print(f"  [ok]   {rel}")
        for note in changes:
            print(f"           {note}")

        if args.apply:
            _write_atomic(path, migrated)
            _stats["written"] += 1

    print(
        f"\nScanned {_stats['scanned']} file(s): "
        f"{_stats['states']} state(s) converted, "
        f"{_stats['options']} option(s) declared, "
        f"{_stats['errors']} error(s)."
    )
    if args.apply:
        print(f"Wrote {_stats['written']} file(s).")
    elif _stats["states"]:
        print("Dry run — re-run with --apply to write these changes.")
    else:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
