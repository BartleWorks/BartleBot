"""Check every byte pattern wizwalker scans for against two client exes, before either has run.

wizwalker finds its hooks by pattern scans over the loaded client, and a patch that moves
or recompiles the code they match leaves the hook with nowhere to go. The exe on disk holds
the same code bytes (``pe_image``), so comparing the build that works with the one that is
coming says which hooks will fail, from the Test realm, before the patch is live.

WHAT IT READS
    Every ``bytes`` literal of four bytes or more in the wizwalker source, found by ``ast``,
    so a new pattern needs no list kept here. Most literals are not patterns but the code
    wizwalker writes into the client (its hooks' bodies); those match neither exe, or are
    not valid regexes at all, and are counted rather than listed.

WHAT IT SAYS
    broken   matches the old exe and not the new: that hook fails on the new build
    new only matches the new exe and not the old: a replacement written for the new
             build, as on an update's library branch
    changed  matches both, a different number of times: a scan that takes the first
             match, or expects one, may now land somewhere else
    holds    matches both the same number of times

USAGE
    uv run python tools/game_update/check_hook_patterns.py
    uv run python tools/game_update/check_hook_patterns.py --old <exe> --new <exe> --source ../wizwalker/wizwalker

    Exits 1 when a pattern is broken. ``bartleby-core/docs/GAME_UPDATES.md`` has the procedure around it.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.game_update.pe_image import LIVE_EXE, TEST_EXE, Image  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Folders of the library that hold no scans: its tests, and the file readers' magic numbers.
SKIP_PARTS = {"tests", "file_readers", "__pycache__"}


@dataclass(frozen=True)
class Literal:
    source: str
    line: int
    value: bytes


@dataclass(frozen=True)
class Result:
    literal: Literal
    old: int | None  # None: not a valid regex, so not a pattern
    new: int | None

    @property
    def verdict(self) -> str:
        if self.old is None or (not self.old and not self.new):
            return "neither"
        if self.old and not self.new:
            return "broken"
        if self.new and not self.old:
            return "new only"
        return "holds" if self.old == self.new else "changed"


def default_source() -> Path:
    import wizwalker

    return Path(wizwalker.__file__).resolve().parent


def literals(root: Path) -> list[Literal]:
    """Every bytes literal of four or more bytes under `root`, outside SKIP_PARTS."""
    found = []
    for path in sorted(root.rglob("*.py")):
        if SKIP_PARTS & set(path.relative_to(root).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, bytes) and len(node.value) >= 4:
                found.append(Literal(path.relative_to(root).as_posix(), node.lineno, node.value))
    return sorted(found, key=lambda lit: (lit.source, lit.line))


def count(pattern: bytes, image: Image) -> int | None:
    try:
        return len(image.find(pattern))
    except re.error:
        return None


def check(found: list[Literal], old: Image, new: Image) -> list[Result]:
    return [Result(lit, count(lit.value, old), count(lit.value, new)) for lit in found]


def report(results: list[Result]) -> int:
    by_verdict: dict[str, list[Result]] = {}
    for r in results:
        by_verdict.setdefault(r.verdict, []).append(r)
    for verdict in ("broken", "new only", "changed", "holds"):
        rows = by_verdict.get(verdict, [])
        print(f"{verdict}: {len(rows)}")
        for r in rows:
            print(f"  {r.literal.source}:{r.literal.line:<5} old={r.old:<4} new={r.new:<4} {r.literal.value[:48]!r}")
    print(f"neither (code wizwalker writes, or a pattern already dead): {len(by_verdict.get('neither', []))}")
    return 1 if by_verdict.get("broken") else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--old", type=Path, default=LIVE_EXE, help="the client exe the patterns work on")
    ap.add_argument("--new", type=Path, default=TEST_EXE, help="the client exe to check them against")
    ap.add_argument("--source", type=Path, default=None, help="the wizwalker package (default: the installed one)")
    args = ap.parse_args()

    source = args.source or default_source()
    found = literals(source)
    print(f"{len(found)} byte literals in {source}")
    print(f"old: {args.old}\nnew: {args.new}\n")
    return report(check(found, Image.open(args.old), Image.open(args.new)))


if __name__ == "__main__":
    sys.exit(main())
