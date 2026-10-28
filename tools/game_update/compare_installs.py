"""Compare two Wizard101 installs: client revision, Bin files, GameData wads, and Root.wad entry by entry.

The first thing to run when a patch reaches the Test realm. It says which game tables
changed, so which snapshots, readers and hooks a patch can have broken
(``bartleby-core/docs/GAME_UPDATES.md`` maps each one to what depends on it).

WHAT IT READS
    ``Bin/revision.dat``, every file in ``Bin/`` by size, the file list of
    ``Data/GameData``, and every entry of each install's ``Root.wad``, hashed by content
    with katsuba so a compressed entry is compared by what it holds.

    A Test realm install downloads zone wads on demand, so it lists far fewer wads than
    live. "Only in old" is therefore mostly noise until the update is complete; it is
    counted, not listed.

USAGE
    uv run python tools/game_update/compare_installs.py                       # live vs Test realm
    uv run python tools/game_update/compare_installs.py --report root_wad.txt  # also every changed entry
    uv run python tools/game_update/compare_installs.py --old <install> --new <install>
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from katsuba.wad import Archive

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LIVE = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101")
TEST = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101 Test")

#: Root.wad folders of per-object entries, thousands of them: counted in the summary, listed only in --report.
BULK_FOLDERS = ("ObjectData", "Spells", "Cinematics", "Decks", "Badges", "StateData", "ClassProjects")


@dataclass
class WadDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)


def revision(install: Path) -> str:
    path = install / "Bin" / "revision.dat"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "?"


def digests(wad: Path) -> dict[str, str]:
    archive = Archive.mmap(str(wad))
    return {
        name: hashlib.blake2b(bytes(archive[name]), digest_size=12).hexdigest() for name in archive.iter_glob("**/*")
    }


def diff_wads(old: Path, new: Path) -> WadDiff:
    a, b = digests(old), digests(new)
    return WadDiff(
        added=sorted(set(b) - set(a)),
        removed=sorted(set(a) - set(b)),
        changed=sorted(name for name in set(a) & set(b) if a[name] != b[name]),
    )


def top_folder(name: str) -> str:
    return name.split("/")[0] if "/" in name else "(root)"


def is_bulk(name: str) -> bool:
    """A per-object entry (a template, a quest's text), or a locale other than en-US."""
    if name.startswith("Locale/"):
        return not name.startswith("Locale/en-US/") or name.startswith("Locale/en-US/WizQst")
    return top_folder(name) in BULK_FOLDERS or name.endswith("Template.xml")


def sizes(folder: Path) -> dict[str, int]:
    return {p.name: p.stat().st_size for p in folder.iterdir() if p.is_file()} if folder.is_dir() else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--old", type=Path, default=LIVE, help="the install the code works with")
    ap.add_argument("--new", type=Path, default=TEST, help="the install to compare against it")
    ap.add_argument(
        "--report", type=Path, default=None, help="write every added, removed and changed Root.wad entry here"
    )
    args = ap.parse_args()

    print(f"revision  old {revision(args.old)}   new {revision(args.new)}")

    old_bin, new_bin = sizes(args.old / "Bin"), sizes(args.new / "Bin")
    print("\nBin files that differ in size, or exist in one install only:")
    for name in sorted(set(old_bin) | set(new_bin)):
        if old_bin.get(name) != new_bin.get(name) and not name.endswith((".log", ".dmp")):
            print(f"  {name:<40} old={old_bin.get(name, '-')!s:>10}  new={new_bin.get(name, '-')!s:>10}")

    old_gd, new_gd = sizes(args.old / "Data" / "GameData"), sizes(args.new / "Data" / "GameData")
    only_new = sorted(set(new_gd) - set(old_gd))
    print(f"\nGameData: old {len(old_gd)} files, new {len(new_gd)}; {len(set(old_gd) - set(new_gd))} only in old")
    print(f"only in new ({len(only_new)}):")
    for name in only_new:
        print(f"  {name}")

    diff = diff_wads(args.old / "Data" / "GameData" / "Root.wad", args.new / "Data" / "GameData" / "Root.wad")
    print(f"\nRoot.wad: {len(diff.added)} added, {len(diff.removed)} removed, {len(diff.changed)} changed")
    for label, names in (("added", diff.added), ("removed", diff.removed), ("changed", diff.changed)):
        print(f"  {label} by folder: {Counter(top_folder(n) for n in names).most_common()}")
    for label, names in (("added", diff.added), ("removed", diff.removed), ("changed", diff.changed)):
        named = [n for n in names if not is_bulk(n)]
        if named:
            print(f"\n{label}, leaving out per-object entries and the non-English locales:")
            for name in named:
                print(f"  {name}")

    if args.report:
        with args.report.open("w", encoding="utf-8") as fh:
            for label, names in (("ADDED", diff.added), ("REMOVED", diff.removed), ("CHANGED", diff.changed)):
                fh.write(f"##### {label} {len(names)}\n")
                fh.writelines(f"{n}\n" for n in names)
        print(f"\nwrote {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
