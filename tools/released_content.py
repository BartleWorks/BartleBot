"""Fail a release that removes content an earlier release shipped (#166).

    uv run python tools/released_content.py v0.3.1
    uv run python tools/released_content.py --since v0.3.0

Official content reaches users only through app updates, so a user's bots can be based
on, or link, anything a release shipped: a template version, a subgraph, a bot, or an
item of the Official strategy set. Removing one breaks those bots, or strands them on a
snapshot, so the rule is "retire it from the pickers, never remove it". This holds the
checkout to it: it compares the checkout with the previous release tag and lists
everything that tag shipped and the checkout no longer does:

- a template version a ``template.json`` listed, whose file is gone or no longer listed;
- a subgraph, bot, duel strategy, player strategy or deck, by its id.

Matching is by id, so a file renamed or moved to another folder passes, and so does an
item kept but hidden from the pickers. The previous release is the newest ``v*`` tag
reachable from ``HEAD``, other than the tag being released; ``--since`` names one
instead. On success it prints what it compared and exits 0. Otherwise it prints every
removal and exits 1 (``docs/RELEASING.md``, "A release").
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent

#: The folders the Official content ships in, which is all a release tag is read for.
CONTENT_DIRS = ("bots", "subgraphs", "strategies", "templates")

#: Each kind kept by id: the folder its files sit in (at any depth), and its name in words.
ID_KINDS = {
    "bots": "bot",
    "subgraphs": "subgraph",
    "strategies/duel": "duel strategy",
    "strategies/player": "player strategy",
    "strategies/decks": "deck",
}

TEMPLATE_INDEX = "template.json"


@dataclass(frozen=True)
class Item:
    """One shipped item: what it is, what it is called, and where it was."""

    kind: str
    name: str
    path: str


@dataclass
class Shipped:
    """What one tree ships: items by ``(kind, id)``, template versions by ``(template id, file)``."""

    items: dict[tuple[str, str], Item] = field(default_factory=dict)
    template_versions: dict[tuple[str, str], Item] = field(default_factory=dict)


def _json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _kind_of(path: PurePosixPath) -> str | None:
    for folder, kind in ID_KINDS.items():
        if path.is_relative_to(folder):
            return kind
    return None


def shipped(files: Mapping[str, bytes]) -> Shipped:
    """What a tree ships, from its content files: posix paths relative to its root, and their bytes."""
    found = Shipped()
    indexes = {}
    for name, data in files.items():
        path = PurePosixPath(name)
        if path.suffix != ".json":
            continue
        if path.parts[0] == "templates":
            if path.name == TEMPLATE_INDEX and len(path.parts) == 3:
                indexes[path.parent] = _json(data)
            continue
        kind = _kind_of(path)
        doc = _json(data)
        if kind is None or not isinstance(doc, dict) or not isinstance(doc.get("id"), str):
            continue
        found.items[(kind, doc["id"])] = Item(kind, str(doc.get("name") or path.stem), name)
    for folder, index in indexes.items():
        if not isinstance(index, dict) or not isinstance(index.get("id"), str):
            continue
        for entry in index.get("versions") or []:
            file = entry.get("file") if isinstance(entry, dict) else None
            if not isinstance(file, str):
                continue
            path = (folder / file).as_posix()
            if path not in files:
                continue
            label = f"{folder.name} v{entry.get('version', '?')}"
            found.template_versions[(index["id"], file)] = Item("template version", label, path)
    return found


def removed(previous: Shipped, current: Shipped) -> list[str]:
    """Everything ``previous`` shipped that ``current`` does not, in words, sorted."""
    gone = [
        f"{item.kind} {item.name!r} ({item_id}), shipped at {item.path}, is gone"
        for (_, item_id), item in previous.items.items()
        if (item.kind, item_id) not in current.items
    ]
    gone += [
        f"template version {item.name} ({template_id}), shipped at {item.path}, is gone or no longer listed"
        for (template_id, file), item in previous.template_versions.items()
        if (template_id, file) not in current.template_versions
    ]
    return sorted(gone)


def working_tree_files(root: Path = ROOT) -> dict[str, bytes]:
    """The checkout's content files, as :func:`shipped` takes them."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for folder in CONTENT_DIRS
        for path in sorted((root / folder).rglob("*.json"))
        if path.is_file()
    }


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True, check=True).stdout


def tag_files(tag: str, root: Path = ROOT) -> dict[str, bytes]:
    """The content files as ``tag`` has them, read in one ``git cat-file --batch``."""
    names = _git(root, "ls-tree", "-r", "-z", "--name-only", tag, "--", *CONTENT_DIRS).decode("utf-8").split("\0")
    names = [name for name in names if name.endswith(".json")]
    if not names:
        return {}
    out = _git(root, "cat-file", "--batch", stdin="".join(f"{tag}:{name}\n" for name in names).encode("utf-8"))
    files, at = {}, 0
    for name in names:
        header_end = out.index(b"\n", at)
        size = int(out[at:header_end].split()[2])
        files[name] = out[header_end + 1 : header_end + 1 + size]
        at = header_end + 1 + size + 1
    return files


def previous_release(current: str | None, root: Path = ROOT) -> str | None:
    """The newest ``v*`` tag reachable from ``HEAD``, leaving out ``current``; None when there is none."""
    args = ["describe", "--tags", "--abbrev=0", "--match", "v*"]
    if current:
        args += ["--exclude", current]
    try:
        return _git(root, *args, "HEAD").decode("utf-8").strip() or None
    except subprocess.CalledProcessError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", nargs="?", help="the tag being released, left out of the search for the previous one")
    parser.add_argument("--since", help="the release to compare with, in place of the previous release tag")
    args = parser.parse_args(argv)

    since = args.since or previous_release(args.tag)
    if since is None:
        print(
            "no earlier v* tag is reachable from HEAD to compare with: fetch the tags (fetch-depth: 0)",
            file=sys.stderr,
        )
        return 1
    gone = removed(shipped(tag_files(since)), shipped(working_tree_files()))
    for line in gone:
        print(line, file=sys.stderr)
    if gone:
        print(f"{len(gone)} removed since {since}: retire content from the pickers, never remove it", file=sys.stderr)
        return 1
    print(f"nothing {since} shipped is gone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
