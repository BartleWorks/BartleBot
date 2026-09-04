"""One-time migration: give every bot file a stable, unique ``id``.

``BotConfig.id`` has ``default_factory=uuid4``, so a bot JSON with no ``id`` field
mints a *fresh* UUID on every load.  Both ``SettingsManager.get_active_strategy()``
and ``strategy_store.list_bot_strategies()`` key off that id, so such bots cannot
bind a combat strategy that survives a restart — the picker comes up empty and every
selection writes a garbage entry into ``settings.json``.

This script does three things to each file under ``bots/``:

  a. Stamps a UUID into any bot lacking ``id``, inserted as the *first* key to match
     the layout the app itself writes.
  b. Re-stamps duplicate ids so each bot is distinct.  See ``ID_OWNERS`` below.
  c. Renames the legacy ``use_default_duel_profile`` param to ``use_default_strategy``
     in ``handle_combat`` states, and drops the unused ``duel_profile_name``.

(c) overlaps ``migrate_combat_profiles.py``, but that script's legacy-field guard
returns early for bots whose old combat fields were already stripped — which is
exactly why a few files still carry the old key.  The rename is repeated here
without that guard.  The Combat tab tolerates the legacy key via a fallback, but
``handle_combat`` does not, so an unmigrated bot silently ignores its per-state
strategy assignments at runtime.

Run from the project root.  Reports only by default; pass ``--apply`` to write:

    uv run python scripts/migrate_bot_ids.py
    uv run python scripts/migrate_bot_ids.py --apply

Idempotent: a second run is a clean no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).parent.parent

# Bots that share an id, and which file should keep it.  Without this the keeper is
# whichever path sorts first, which would hand id 602704e3 (referenced by the bot
# strategy named "4-player Hall of Heroes") to scion_of_the_old_one.json.
ID_OWNERS: dict[str, str] = {
    "602704e3-45d1-4232-a64a-9c012f870cf3": "bots/18_lemuria/hall_of_heroes.json",
}

_stats = {"scanned": 0, "ids_stamped": 0, "ids_restamped": 0, "params_renamed": 0, "written": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Pure transform (no disk I/O — this is what the tests exercise)
# ---------------------------------------------------------------------------


def rename_legacy_params(data: dict) -> list[str]:
    """Rename legacy handle_combat params in place. Returns a list of change notes."""
    changes: list[str] = []
    for state in data.get("states") or []:
        if state.get("node_type") != "handle_combat":
            continue
        params = state.get("params")
        if not isinstance(params, dict):
            continue
        if "use_default_duel_profile" in params:
            # Preserve the value — these are meaningfully False in at least one bot.
            params["use_default_strategy"] = params.pop("use_default_duel_profile")
            changes.append(f"state {state.get('id', '?')!r}: use_default_duel_profile -> use_default_strategy")
        if "duel_profile_name" in params:
            params.pop("duel_profile_name")
            changes.append(f"state {state.get('id', '?')!r}: dropped duel_profile_name")
    return changes


def ensure_id(data: dict, taken: set[str], *, force_new: bool = False) -> tuple[dict, list[str]]:
    """Return (data, changes), guaranteeing a unique ``id`` as the first key.

    ``taken`` is the set of ids already claimed by other files; it is updated in
    place.  ``force_new`` re-stamps even when an id is present (duplicate
    resolution).  A file that already has a unique id is returned untouched, with
    its existing key order preserved.
    """
    current = data.get("id")
    if current and not force_new:
        taken.add(current)
        return data, []

    new_id = str(uuid4())
    while new_id in taken:  # practically unreachable; cheap insurance
        new_id = str(uuid4())
    taken.add(new_id)

    note = f"re-stamped duplicate id {current} -> {new_id}" if current else f"stamped missing id -> {new_id}"
    # Rebuild so ``id`` leads, matching files the app writes via model_dump().
    rebuilt = {"id": new_id, **{k: v for k, v in data.items() if k != "id"}}
    return rebuilt, [note]


def migrate_bot_data(data: dict, taken: set[str], *, force_new_id: bool = False) -> tuple[dict, list[str]]:
    """Apply every migration step to one bot's parsed JSON.

    Returns the (possibly rebuilt) dict and a list of human-readable changes.  An
    empty change list means the file is already migrated and must not be rewritten.
    """
    changes = rename_legacy_params(data)
    data, id_changes = ensure_id(data, taken, force_new=force_new_id)
    return data, id_changes + changes


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


def _plan_duplicate_restamps(loaded: dict[Path, dict]) -> set[Path]:
    """Return the paths whose id collides with another file's and must be re-stamped.

    The keeper is the owner named in ``ID_OWNERS`` when one applies, otherwise the
    first path in sorted order.
    """
    by_id: dict[str, list[Path]] = {}
    for path, data in loaded.items():
        bot_id = data.get("id")
        if bot_id:
            by_id.setdefault(bot_id, []).append(path)

    restamp: set[Path] = set()
    for bot_id, paths in by_id.items():
        if len(paths) < 2:
            continue
        owner_rel = ID_OWNERS.get(bot_id)
        keeper = None
        if owner_rel:
            keeper = next((p for p in paths if p.as_posix().endswith(owner_rel)), None)
        if keeper is None:
            keeper = sorted(paths)[0]
        for p in paths:
            if p != keeper:
                restamp.add(p)
        print(f"  [dup]  id {bot_id} held by {len(paths)} files - keeping it on {_rel(keeper)}")
    return restamp


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

    loaded: dict[Path, dict] = {}
    for path in bot_files:
        _stats["scanned"] += 1
        try:
            with open(path, encoding="utf-8") as f:
                loaded[path] = json.load(f)
        except Exception as exc:
            print(f"  [ERR]  {_rel(path)}: {exc}")
            _stats["errors"] += 1

    restamp = _plan_duplicate_restamps(loaded)
    taken = {d["id"] for p, d in loaded.items() if d.get("id") and p not in restamp}

    for path, data in loaded.items():
        rel = _rel(path)
        try:
            migrated, changes = migrate_bot_data(data, taken, force_new_id=path in restamp)
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        if not changes:
            continue

        for note in changes:
            if note.startswith("stamped"):
                _stats["ids_stamped"] += 1
            elif note.startswith("re-stamped"):
                _stats["ids_restamped"] += 1
            else:
                _stats["params_renamed"] += 1

        print(f"  [ok]   {rel}")
        for note in changes:
            print(f"           {note}")

        if apply_changes:
            _write_atomic(path, migrated)
            _stats["written"] += 1

    print(
        f"\nScanned {_stats['scanned']} file(s): "
        f"{_stats['ids_stamped']} id(s) stamped, "
        f"{_stats['ids_restamped']} re-stamped, "
        f"{_stats['params_renamed']} param change(s), "
        f"{_stats['errors']} error(s)."
    )
    if apply_changes:
        print(f"Wrote {_stats['written']} file(s).")
    elif _stats["ids_stamped"] or _stats["ids_restamped"] or _stats["params_renamed"]:
        print("Dry run — re-run with --apply to write these changes.")
    else:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
