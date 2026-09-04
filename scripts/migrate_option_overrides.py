"""One-time migration: retire per-node ``option_overrides``.

``StateModel.option_overrides`` let a node pin a bot-wide option for itself. Every
surviving use in the library pinned ``Safety Delay``, and that is the epic's own bug
one level down: the author's number means *"how long this interaction takes"* (a
property of the game) while the user's Safety Delay means *"how much slack my
connection needs"* (a property of the machine). The pin replaced the user's value
outright, so a bot pinning ``0.0`` gave a user on a bad connection no slack at all,
with no recourse.

The field is gone from the schema, and each of the three causes behind the 16 pins
now has a param of its own:

  a. **A pin that was cutting the post-interact quiet period.** ``interact`` waits
     ``Safety Delay`` seconds of dialogue *silence* after every successful
     interaction, and these interactions produce no dialogue at all. ``has_dialogue:
     false`` returns before that wait is even reached — strictly faster than the pin.

  b. **A pin that was buying time for an animation.** Replaced by the additive
     ``animation_delay`` param: effective quiet period = resolved Safety Delay **+**
     this. Additive because a cutscene takes N seconds regardless of connection, and
     *then* the server response needs the user's slack. The stamped value is the
     animation's own length, timed against a live client — deliberately not "the old
     pin minus the default", which would only hold for a user on the default.

  c. **A pin that was raising the sequential interact-prompt ceiling** so retries
     against a slow teleporter eventually landed. Replaced by ``object_use_delay``,
     the gap ``take_turns`` already leaves between one player finishing at a shared
     object and the next starting — which is what that wait was actually for. Its
     value is the object's measured cycle, again from a live client rather than from
     the old pin.

Every state also loses its now-unknown ``option_overrides`` key, empty or not.

Run from the project root. Reports only by default; pass ``--apply`` to write:

    uv run python scripts/migrate_option_overrides.py
    uv run python scripts/migrate_option_overrides.py --apply

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

# (a) Silent interactions whose pin was only cutting the dialogue quiet period.
SILENT_INTERACTS: dict[str, set[str]] = {
    "04_mooshu/crimson_fields.json": {
        "state_5",
        "state_6",
        "state_7",
        "state_8",
        "state_10",
        "state_11",
        "state_12",
        "state_13",
        "state_28",
        "state_29",
    },
    "04_mooshu/jade_oni.json": {"state_4"},
}

# (b) Pins that were buying time for an animation. Value is the animation_delay to
# stamp — the animation's own length, timed against a live client. Not the old pin
# minus 3.0: that arithmetic only reproduces the old behaviour for a user sitting on
# the default Safety Delay, and the users this epic is for are the ones who are not.
ANIMATION_PINS: dict[str, dict[str, float]] = {
    "03_marleybone/spike_the_crusher.json": {"state_11": 5.0, "state_8": 5.0, "state_9": 5.0},
    "aquila/mt_olympus.json": {"state_60": 5.0},
}

# (c) Pins that were really a between-uses delay. Value is the object_use_delay to
# stamp, measured against the live object — the Tartarus teleporter's cycle is ~10s.
OBJECT_USE_DELAY_PINS: dict[str, dict[str, float]] = {
    "11_azteca/pyramid_of_mother_moon_tartarus_workaround.json": {"state_11": 10.0},
}

_stats = {"scanned": 0, "silenced": 0, "animated": 0, "delayed": 0, "keys_dropped": 0, "written": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Pure transform (no disk I/O — this is what the tests exercise)
# ---------------------------------------------------------------------------


def migrate_bot_data(data: dict, rel_key: str = "") -> tuple[dict, list[str]]:
    """Apply the option-override migration to one bot's parsed JSON in place.

    *rel_key* is the bot's path relative to ``bots/`` with forward slashes; it selects
    the per-file replacement tables above. A file not named in any of them still has
    its ``option_overrides`` keys dropped.

    Returns (data, changes). An empty change list means the file is already migrated
    and must not be rewritten.
    """
    changes: list[str] = []
    silent = SILENT_INTERACTS.get(rel_key, set())
    animated = ANIMATION_PINS.get(rel_key, {})
    delayed = OBJECT_USE_DELAY_PINS.get(rel_key, {})

    for state in data.get("states") or []:
        state_id = state.get("id", "?")
        params = state.get("params")
        if not isinstance(params, dict):
            params = None

        if params is not None and state_id in silent and params.get("has_dialogue") is not False:
            params["has_dialogue"] = False
            changes.append(f"state {state_id!r}: has_dialogue -> False (silent interaction)")

        if params is not None and state_id in animated and "animation_delay" not in params:
            params["animation_delay"] = animated[state_id]
            changes.append(f"state {state_id!r}: animation_delay -> {animated[state_id]}")

        if params is not None and state_id in delayed and "object_use_delay" not in params:
            params["object_use_delay"] = delayed[state_id]
            changes.append(f"state {state_id!r}: object_use_delay -> {delayed[state_id]}")

        if "option_overrides" in state:
            pinned = state.pop("option_overrides")
            if pinned:
                changes.append(f"state {state_id!r}: dropped option_overrides {pinned}")
            else:
                changes.append(f"state {state_id!r}: dropped empty option_overrides")

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
        rel_key = path.relative_to(bots_dir).as_posix()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        try:
            migrated, changes = migrate_bot_data(data, rel_key)
        except Exception as exc:
            print(f"  [ERR]  {rel}: {exc}")
            _stats["errors"] += 1
            continue

        if not changes:
            continue

        for note in changes:
            if "has_dialogue" in note:
                _stats["silenced"] += 1
            elif "animation_delay" in note:
                _stats["animated"] += 1
            elif "object_use_delay" in note:
                _stats["delayed"] += 1
            else:
                _stats["keys_dropped"] += 1

        print(f"  [ok]   {rel}")
        for note in changes:
            print(f"           {note}")

        if apply_changes:
            _write_atomic(path, migrated)
            _stats["written"] += 1

    print(
        f"\nScanned {_stats['scanned']} file(s): "
        f"{_stats['silenced']} silenced, "
        f"{_stats['animated']} animation delay(s), "
        f"{_stats['delayed']} object-use delay(s), "
        f"{_stats['keys_dropped']} option_overrides key(s) dropped, "
        f"{_stats['errors']} error(s)."
    )
    if apply_changes:
        print(f"Wrote {_stats['written']} file(s).")
    elif any(_stats[k] for k in ("silenced", "animated", "delayed", "keys_dropped")):
        print("Dry run — re-run with --apply to write these changes.")
    else:
        print("Nothing to do.")


if __name__ == "__main__":
    main()
