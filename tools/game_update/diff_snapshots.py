"""Regenerate the game data snapshots from another install into a folder, and diff them with the shipped ones.

Each ``tools/dump_*.py`` writes one file of ``bartlebot/game/data/`` (``tools/README.md``).
Run against a Test realm install into a scratch folder, they say what a patch adds, renames
and retires without touching the files the app ships, which stay at the live build until
the patch is.

WHICH TOOLS
    Most read only ``Root.wad`` and run against any install. Three read every zone's own wad
    (``ZONE_WAD_TOOLS``), which a Test realm install fetches on demand: run there, they would
    report every zone not downloaded yet as removed, so they run only with ``--zone-wads``,
    once the install is complete.

A list of objects with a ``"key"`` (the badge categories) is compared key by key, with a
note when the order of the shared keys changed, since that order is what the bot library's
folders follow.

USAGE
    uv run python tools/game_update/diff_snapshots.py --out <folder>                 # regenerate from the Test realm, then diff
    uv run python tools/game_update/diff_snapshots.py --out <folder> --no-regenerate # diff what is already there
    uv run python tools/game_update/diff_snapshots.py --out <folder> --game-data <dir> --zone-wads
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "bartlebot" / "game" / "data"
TEST_GAME_DATA = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101 Test\Data\GameData")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT_WAD_TOOLS = (
    "badge_categories",
    "dialog_captions",
    "name_tables",
    "realm_names",
    "spell_names",
    "spell_schools",
    "stat_caps",
    "world_names",
)
ZONE_WAD_TOOLS = ("entity_names", "reagent_nodes", "zone_names")

#: Top-level fields that change on every run and say nothing about the game.
STAMPS = {"_comment", "generated", "source", "skipped", "errors"}


def regenerate(game_data: Path, out: Path, zone_wads: bool) -> list[str]:
    """Run each dump tool into `out`. Returns the names of those that failed."""
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in ROOT_WAD_TOOLS + (ZONE_WAD_TOOLS if zone_wads else ()):
        source = ["--wad", str(game_data / "Root.wad")] if name in ROOT_WAD_TOOLS else ["--game-data", str(game_data)]
        target = out / f"{name}.json"
        # A tool can exit 1 for a warning after writing, so success is the file existing, and
        # a file left by an earlier run must not pass for one.
        target.unlink(missing_ok=True)
        cmd = [sys.executable, str(REPO / "tools" / f"dump_{name}.py"), *source, "--out", str(target)]
        print(f"== dump_{name}")
        done = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not target.exists():
            failed.append(name)
            print((done.stdout + done.stderr).strip()[-800:])
    return failed


def _keyed(value) -> dict | None:
    if isinstance(value, list) and value and all(isinstance(v, dict) and "key" in v for v in value):
        return {v["key"]: v for v in value}
    return None


def differences(old, new, path: str = "") -> list[str]:
    """One line per added (+), removed (-) or changed (~) value between two parsed snapshots."""
    if (a := _keyed(old)) is not None and (b := _keyed(new)) is not None:
        lines = differences(a, b, path)
        shared_old, shared_new = [k for k in a if k in b], [k for k in b if k in a]
        if shared_old != shared_new:
            lines.append(f"~ {path}: the order of the shared keys changed")
        return lines
    if isinstance(old, dict) and isinstance(new, dict):
        lines = []
        for key in sorted(set(old) | set(new), key=str):
            if not path and key in STAMPS:
                continue
            where = f"{path}/{key}"
            if key not in old:
                lines.append(f"+ {where} = {json.dumps(new[key], ensure_ascii=False)[:160]}")
            elif key not in new:
                lines.append(f"- {where} (was {json.dumps(old[key], ensure_ascii=False)[:120]})")
            else:
                lines.extend(differences(old[key], new[key], where))
        return lines
    if old != new:
        return [f"~ {path}: {json.dumps(old, ensure_ascii=False)[:100]} -> {json.dumps(new, ensure_ascii=False)[:100]}"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="the folder to regenerate into and diff; never game/data")
    ap.add_argument("--game-data", type=Path, default=TEST_GAME_DATA, help="the install's Data/GameData")
    ap.add_argument("--zone-wads", action="store_true", help="also run the tools that read every zone wad")
    ap.add_argument("--no-regenerate", action="store_true", help="diff what is already in --out")
    ap.add_argument("--limit", type=int, default=60, help="lines shown per snapshot (the rest are counted)")
    args = ap.parse_args()

    if args.out.resolve() == DATA.resolve():
        print("--out is the shipped data folder; pick a scratch folder")
        return 2
    failed = [] if args.no_regenerate else regenerate(args.game_data, args.out, args.zone_wads)

    for path in sorted(args.out.glob("*.json")):
        shipped = DATA / path.name
        if not shipped.exists():
            continue
        lines = differences(
            json.loads(shipped.read_text(encoding="utf-8")), json.loads(path.read_text(encoding="utf-8"))
        )
        signs = [line[0] for line in lines]
        print(f"\n== {path.name}: +{signs.count('+')} -{signs.count('-')} ~{signs.count('~')}")
        for line in lines[: args.limit]:
            print(f"  {line}")
        if len(lines) > args.limit:
            print(f"  ... {len(lines) - args.limit} more")
    if failed:
        print(f"\nfailed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
