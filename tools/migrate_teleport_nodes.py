"""Rewrite every Teleport to Coordinates node as a Walk to XYZ (Stage 7 of #322).

    uv run python tools/migrate_teleport_nodes.py [--check] [PATH ...]

Teleport to Coordinates left the registry when every move began to walk, and a file naming it
refuses to load (``schema.REMOVED_NODES``). Walk to XYZ takes the same two params, Zone and XYZ,
so only the node type changes, and the rewrite is a text replacement of that one value: the
file's layout, order and everything else stay byte for byte as they were. A file that walks
past a closed gate where it used to teleport gets a Teleport to XYZ by hand, where a run finds
the gate closed (Stage 7.2).

With no paths it rewrites the shipped content: every bot under ``bots/`` and every template
under ``templates/``, the drafts and the released versions both. A released version's sha256 in
its ``template.json`` is rewritten to match (the user's call: a released version is rewritten in
place before 1.0.0). Given paths, files or folders, it rewrites the bots, templates and
subgraphs there instead, which is how a user's own bots sent in by a tester are converted; a
``template.json`` beside a rewritten version is kept in step the same way.

Each rewritten file is loaded afterwards, and must save byte-identical to what was written,
or the tool says so and exits 1. ``--check`` lists the files it would change and writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OLD = '"node_type": "teleport_to_coordinates"'
NEW = '"node_type": "walk_to_xyz"'

#: What the tool rewrites with no paths given.
SHIPPED = ("bots", "templates")

TEMPLATE_INDEX = "template.json"


def files_under(paths: list[Path]) -> list[Path]:
    """Every ``.json`` file at or under *paths*, but a template's own index."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(p for p in sorted(path.rglob("*.json")) if p.name != TEMPLATE_INDEX)
        elif path.suffix == ".json" and path.name != TEMPLATE_INDEX:
            found.append(path)
    return found


def rewrite(text: str) -> str:
    """*text* with every Teleport to Coordinates node made a Walk to XYZ."""
    return text.replace(OLD, NEW)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reindex(version: Path, old_sha: str) -> Path | None:
    """Rewrite the sha256 of *version* in its folder's ``template.json``, if it lists it. Returns the
    index rewritten, or None. A text replacement too, so the index keeps its layout."""
    index = version.parent / TEMPLATE_INDEX
    if not index.is_file():
        return None
    text = index.read_text(encoding="utf-8")
    listed = any(v.get("file") == version.name for v in json.loads(text).get("versions", []))
    if not listed or old_sha not in text:
        return None
    index.write_text(text.replace(old_sha, _sha256(version)), encoding="utf-8", newline="")
    return index


def saves_unchanged(path: Path) -> str | None:
    """Why *path*, once rewritten, does not load or would not save byte-identical, or None."""
    from bartlebot.bot.schema import BotConfig, SubgraphDefinition  # noqa: PLC0415 - only once a file changed

    data = json.loads(path.read_text(encoding="utf-8"))
    model = SubgraphDefinition if "inputs" in data and "initial" not in data else BotConfig
    try:
        loaded = model.load(path)
    except Exception as e:  # noqa: BLE001 - reported, with the file
        return f"does not load: {e}"
    if loaded.file_dump() != data:
        return "would not save as written"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="files or folders; the shipped content if none")
    parser.add_argument("--check", action="store_true", help="list what would change, and write nothing")
    args = parser.parse_args(argv)

    targets = files_under(args.paths or [ROOT / d for d in SHIPPED])
    changed: list[Path] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if OLD not in text:
            continue
        changed.append(path)
        if args.check:
            continue
        old_sha = _sha256(path)
        path.write_text(rewrite(text), encoding="utf-8", newline="")
        if (index := reindex(path, old_sha)) is not None:
            print(f"  {index}: the sha256 of {path.name} follows it")

    for path in changed:
        print(path)
    if args.check:
        print(f"{len(changed)} file(s) would change")
        return 0
    problems = [(p, why) for p in changed if (why := saves_unchanged(p)) is not None]
    for path, why in problems:
        print(f"PROBLEM: {path} {why}", file=sys.stderr)
    print(f"{len(changed)} file(s) rewritten")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
