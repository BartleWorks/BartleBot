"""Query the Wizard101 type dumps (``r<rev>_WizardDev.json``: the newest in ``bartlebot/game/data/``, older ones in ``tools/``).

The dump lists every game class with the exact byte offset of each property, so
it answers the questions the ``probe_*.py`` scripts otherwise have to answer by
poking at a live client:

    # what lives at offset 304 of ClientObject?
    uv run python tools/typedump.py at ClientObject 304

    # every offset on a class, including inherited properties
    uv run python tools/typedump.py show ClientObject

    # which classes have a property whose name matches this?
    uv run python tools/typedump.py prop globalIdFull

    # annotate wizwalker's hardcoded offsets with property names
    uv run python tools/typedump.py annotate ../wizwalker/wizwalker/memory/memory_objects/client_object.py

    # diff two dumps after a game patch
    uv run python tools/typedump.py diff tools/r800294_WizardDev.json bartlebot/game/data/r810000_WizardDev.json

    # which hard-coded offsets in a library the new dump says have moved
    uv run python tools/typedump.py stale ../wizwalker/wizwalker/memory/memory_objects --old tools/r806919_WizardDev.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR.parent))

from bartlebot.game.art_extraction import TYPE_DUMP_DIR, newest_type_dump  # noqa: E402


@dataclass(frozen=True)
class Prop:
    """One property on a class."""

    name: str
    type: str
    offset: int
    container: str
    pointer: bool
    dynamic: bool
    owner: str

    @property
    def decl(self) -> str:
        """Human-readable type, e.g. ``List<int>`` or ``ClientObject*``."""
        base = self.type + ("*" if self.pointer else "")
        if self.container not in ("Static", ""):
            return f"{self.container}<{base}>"
        return base


class TypeDump:
    """An indexed view over a wiztype JSON dump."""

    def __init__(self, raw: dict) -> None:
        self.raw = raw
        # Strip the "class "/"struct "/"enum " prefix so lookups can use bare names.
        self.by_name: dict[str, dict] = {}
        for key, body in raw.items():
            self.by_name[key] = body
            bare = key.split(" ", 1)[1] if " " in key else key
            self.by_name.setdefault(bare, body)

    @classmethod
    def load(cls, path: Path) -> TypeDump:
        with path.open(encoding="utf-8") as fh:
            return cls(json.load(fh))

    def resolve(self, name: str) -> tuple[str, dict] | None:
        """Find a class by exact name, then case-insensitively."""
        if name in self.by_name:
            return name, self.by_name[name]
        lowered = name.lower()
        for key, body in self.by_name.items():
            if key.lower() == lowered:
                return key, body
        return None

    def search(self, pattern: str, include_pointers: bool = False) -> list[str]:
        """Class names matching a regex.

        Every ``class Foo`` also appears as ``class Foo*`` with byte-identical
        contents, which doubles every result list for no information, so the
        pointer variants are hidden unless asked for.
        """
        rx = re.compile(pattern, re.IGNORECASE)
        hits = (k for k in self.raw if rx.search(k))
        if not include_pointers:
            hits = (k for k in hits if not (k.endswith("*") and k[:-1] in self.raw))
        return sorted(hits)

    @cache  # noqa: B019 - instances are per-process and short-lived
    def props(self, name: str) -> tuple[Prop, ...]:
        """All properties on a class, including those inherited from its bases.

        The dump lists only a class's own properties; base-class properties are
        merged in here. A subclass redeclaring a name wins over its base.
        """
        found = self.resolve(name)
        if not found:
            return ()
        key, body = found
        merged: dict[str, Prop] = {}
        for base in body.get("bases", []):
            for p in self.props(base):
                merged.setdefault(p.name, p)
        for pname, pbody in body.get("properties", {}).items():
            merged[pname] = Prop(
                name=pname,
                type=pbody.get("type", "?"),
                offset=pbody.get("offset", -1),
                container=pbody.get("container", "Static"),
                pointer=bool(pbody.get("pointer")),
                dynamic=bool(pbody.get("dynamic")),
                owner=key,
            )
        return tuple(sorted(merged.values(), key=lambda p: p.offset))

    def at(self, name: str, offset: int) -> list[Prop]:
        """Properties of a class sitting at exactly this offset."""
        return [p for p in self.props(name) if p.offset == offset]

    def near(self, name: str, offset: int, window: int = 24) -> list[Prop]:
        """Properties within ``window`` bytes of an offset, for near-misses."""
        return [p for p in self.props(name) if abs(p.offset - offset) <= window]

    def find_prop(self, pattern: str, include_pointers: bool = False) -> list[Prop]:
        """Every property across every class whose name matches a regex."""
        rx = re.compile(pattern, re.IGNORECASE)
        hits: list[Prop] = []
        for key, body in self.raw.items():
            if not include_pointers and key.endswith("*") and key[:-1] in self.raw:
                continue
            for pname, pbody in body.get("properties", {}).items():
                if rx.search(pname):
                    hits.append(
                        Prop(
                            name=pname,
                            type=pbody.get("type", "?"),
                            offset=pbody.get("offset", -1),
                            container=pbody.get("container", "Static"),
                            pointer=bool(pbody.get("pointer")),
                            dynamic=bool(pbody.get("dynamic")),
                            owner=key,
                        )
                    )
        return sorted(hits, key=lambda p: (p.owner, p.offset))


# Byte sizes for the types that actually appear in class layouts. Only used to
# work out where a property ends, so a wrong guess widens a reported gap rather
# than hiding one -- unknown types are treated as size 0 for that reason.
_SIZES: dict[str, int] = {
    "bool": 1,
    "char": 1,
    "unsigned char": 1,
    "short": 2,
    "unsigned short": 2,
    "int": 4,
    "unsigned int": 4,
    "float": 4,
    "__int64": 8,
    "unsigned __int64": 8,
    "long": 4,
    "unsigned long": 8,
    "double": 8,
    "gid": 8,
    "std::string": 32,
    "std::wstring": 32,
    "class Vector3D": 12,
    "class Point<int>": 8,
    "class Point<float>": 8,
    "class Size<int>": 8,
    "class Rect<int>": 16,
}


def _sizeof(p: Prop) -> int:
    """Best-effort byte size of a property, or 0 when unknown."""
    if p.pointer or p.container in ("Vector", "List"):
        # Vector/List are the usual 24-byte begin/end/cap triple; bare pointers are 8.
        return 8 if p.pointer and p.container == "Static" else 24
    return _SIZES.get(p.type, 0)


def default_dump() -> Path:
    """The newest ``r<rev>_WizardDev.json``, from ``bartlebot/game/data/`` or ``tools/``."""
    dump = newest_type_dump(TYPE_DUMP_DIR, TOOLS_DIR)
    if dump is None:
        sys.exit("no r<rev>_WizardDev.json found in bartlebot/game/data/ or tools/")
    return dump


def _fmt(p: Prop, show_owner: bool = False) -> str:
    owner = f"  [{p.owner}]" if show_owner else ""
    return f"  {p.offset:>6} 0x{p.offset:<5x} {p.name:<40} {p.decl}{owner}"


# ---------------------------------------------------------------- commands


def cmd_show(dump: TypeDump, args) -> int:
    found = dump.resolve(args.klass)
    if not found:
        near = dump.search(re.escape(args.klass))
        print(f"no class named {args.klass!r}")
        if near:
            print("did you mean:")
            for n in near[:15]:
                print(f"  {n}")
        return 1
    key, body = found
    props = dump.props(key)
    print(f"{key}")
    if body.get("bases"):
        print(f"  bases: {' -> '.join(body['bases'])}")
    print(f"  {len(props)} properties (including inherited)\n")
    for p in props:
        if args.own and p.owner != key:
            continue
        print(_fmt(p, show_owner=p.owner != key))
    return 0


def cmd_at(dump: TypeDump, args) -> int:
    hits = dump.at(args.klass, args.offset)
    if hits:
        print(f"{args.klass} @ {args.offset} (0x{args.offset:x}):")
        for p in hits:
            print(_fmt(p, show_owner=True))
        return 0
    print(f"nothing at exactly {args.offset} on {args.klass}; within {args.window} bytes:")
    for p in dump.near(args.klass, args.offset, window=args.window):
        print(_fmt(p, show_owner=True))
    return 1


def cmd_prop(dump: TypeDump, args) -> int:
    hits = dump.find_prop(args.pattern, include_pointers=args.pointers)
    for p in hits[: args.limit]:
        print(f"  {p.owner:<50} {p.offset:>6} 0x{p.offset:<5x} {p.name:<36} {p.decl}")
    if len(hits) > args.limit:
        print(f"  ... {len(hits) - args.limit} more (use --limit)")
    return 0 if hits else 1


def cmd_gaps(dump: TypeDump, args) -> int:
    """Show unreflected holes in a class layout.

    The dump only lists reflected PropertyClass properties. Raw C++ members --
    the entry vectors and manager pointers the probe scripts hunt for -- live in
    the gaps between them. Bracketing a gap between two named neighbors turns
    "poke at a live client until something looks right" into a short list of
    candidate offsets, and a 24-byte gap is very often a std::vector triple.
    """
    props = dump.props(args.klass)
    if not props:
        print(f"no class named {args.klass!r}")
        return 1
    print(f"{args.klass}: unreflected gaps of >= {args.min} bytes\n")
    found = 0
    for prev, nxt in zip(props, props[1:]):
        size = _sizeof(prev)
        end = prev.offset + size
        gap = nxt.offset - end
        if size == 0 or gap < args.min:
            continue
        found += 1
        hint = "  <- std::vector/List triple?" if gap in (24, 32) else ""
        print(f"  0x{end:<5x} .. 0x{nxt.offset:<5x}  {gap:>4} bytes{hint}")
        print(f"      after  {prev.name} ({prev.decl})")
        print(f"      before {nxt.name} ({nxt.decl})")
    if not found:
        print("  none -- every property is followed immediately by the next")
    return 0


def cmd_classes(dump: TypeDump, args) -> int:
    for name in dump.search(args.pattern, include_pointers=args.pointers)[: args.limit]:
        n = len(dump.props(name))
        print(f"  {name:<70} {n:>4} props")
    return 0


# wizwalker writes offsets as bare ints or hex: read_value_from_offset(304, ...)
_OFFSET_CALL = re.compile(r"read_value_from_offset\(\s*(0x[0-9a-fA-F]+|\d+)")
_PY_CLASS = re.compile(r"^class\s+(\w+)")


def cmd_annotate(dump: TypeDump, args) -> int:
    """Print a wizwalker memory-object source file with offsets named.

    Maps each ``class Foo`` in the file to the game class ``Foo`` (dropping a
    leading ``Dynamic``/``Current`` prefix, which is a wizwalker wrapper idiom)
    and resolves every hardcoded offset against the dump.
    """
    path = Path(args.path)
    if not path.exists():
        return int(bool(print(f"no such file: {path}")))

    game_class: str | None = None
    unresolved = 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = _PY_CLASS.match(line)
        if m:
            name = _game_class_name(m.group(1))
            game_class = name if dump.resolve(name) else None
            marker = "" if game_class else "   (no matching game class)"
            print(f"\n{lineno:>5}: {line}{marker}")
            continue
        call = _OFFSET_CALL.search(line)
        if not call:
            continue
        offset = int(call.group(1), 0)
        note = "?"
        if game_class:
            hits = dump.at(game_class, offset)
            if hits:
                note = ", ".join(f"{p.name}: {p.decl}" for p in hits)
            else:
                near = dump.near(game_class, offset, window=8)
                note = ("near " + ", ".join(f"{p.name}@{p.offset}" for p in near)) if near else "UNKNOWN"
                unresolved += 1
        print(f"{lineno:>5}: {line.strip():<60} -> {note}")
    if unresolved:
        print(
            f"\n{unresolved} offsets did not resolve. The dump covers reflected PropertyClass "
            "properties only, so raw C++ member pointers (child objects, managers) never appear "
            "in it -- those are expected misses, not stale offsets."
        )
    return 0


#: wizwalker classes named otherwise than the game class they read. The rest of the
#: unmatched ones (cameras, fish, the scene manager, the deck and spell list entries) are
#: unreflected structures with no game class to check against.
_GAME_CLASS_ALIASES = {"GameStats": "WizGameStats"}


def _game_class_name(py_name: str) -> str:
    """The game class a wizwalker class reads: its own name, Dynamic/Current dropped, or an alias."""
    name = re.sub(r"^(Dynamic|Current)", "", py_name)
    return _GAME_CLASS_ALIASES.get(name, name)


#: Every read_*/write_* helper whose first argument is a literal offset: read_value_from_offset,
#: write_value_to_offset, read_enum, read_dynamic_vector, write_xyz and the rest.
_ANY_OFFSET_CALL = re.compile(r"\b(?:read|write)_\w+\(\s*(0x[0-9a-fA-F]+|\d+)\s*[,)]")
_RAW_WINDOW = 16


@dataclass(frozen=True)
class Stale:
    """One hard-coded offset a new dump says is wrong, or may be."""

    path: str
    line: int
    klass: str
    offset: int
    kind: str  # "moved", "gone", or "raw" (an unreflected member whose neighbors moved)
    note: str
    span: tuple[int, int] = (0, 0)  # the offset literal's columns in its line
    new_offset: int | None = None  # where the property is now, for "moved"


def stale_offsets(source: str, path: str, old: TypeDump, new: TypeDump) -> tuple[list[Stale], set[str]]:
    """The offsets in `source` that name a property `new` has moved or dropped since `old`.

    A wizwalker class maps to the game class of its name, ``Dynamic``/``Current`` prefix
    dropped, as in ``annotate``. An offset naming no property (a raw C++ member) cannot be
    checked directly; when the properties within _RAW_WINDOW bytes of it moved, it is
    reported as ``raw``, since a member between two that shifted has most likely shifted
    with them. Returns the findings, and the source classes with no game class to check.
    """
    found: list[Stale] = []
    unmatched: set[str] = set()
    game_class: str | None = None
    for lineno, line in enumerate(source.splitlines(), 1):
        m = _PY_CLASS.match(line)
        if m:
            name = _game_class_name(m.group(1))
            game_class = name if old.resolve(name) and new.resolve(name) else None
            if game_class is None:
                unmatched.add(m.group(1))
            continue
        if game_class is None:
            continue
        code = line.split("#", 1)[0]  # commented-out code reads nothing
        for call in _ANY_OFFSET_CALL.finditer(code):
            offset = int(call.group(1), 0)
            now = {p.name: p for p in new.props(game_class)}
            named = old.at(game_class, offset)
            span = call.span(1)
            for p in named:
                q = now.get(p.name)
                if q is None:
                    found.append(
                        Stale(path, lineno, game_class, offset, "gone", f"{p.name} is not in the new dump", span)
                    )
                elif q.offset != offset:
                    note = f"{p.name}: {offset} -> {q.offset}"
                    found.append(Stale(path, lineno, game_class, offset, "moved", note, span, q.offset))
            if named:
                continue
            shifts = {
                (p.name, now[p.name].offset - p.offset)
                for p in old.near(game_class, offset, window=_RAW_WINDOW)
                if p.name in now and now[p.name].offset != p.offset
            }
            if shifts:
                detail = ", ".join(f"{n} {d:+d}" for n, d in sorted(shifts))
                found.append(
                    Stale(path, lineno, game_class, offset, "raw", f"unreflected; neighbors moved: {detail}", span)
                )
    return found, unmatched


def fix_moved(source: str, findings: list[Stale]) -> str:
    """`source` with each moved offset rewritten to where its property is now, in the literal's own base.

    Only an offset naming one property is rewritten: two properties at one offset (a
    union, or a zero-size member) moving apart cannot say which the code meant.
    """
    lines = source.splitlines(keepends=True)
    per_site: dict[tuple[int, tuple[int, int]], list[Stale]] = {}
    for f in findings:
        if f.kind in ("moved", "gone"):
            per_site.setdefault((f.line, f.span), []).append(f)
    for (lineno, (start, end)), fs in sorted(per_site.items(), reverse=True):
        if len(fs) != 1 or fs[0].kind != "moved":
            continue
        line = lines[lineno - 1]
        literal = line[start:end]
        new = f"0x{fs[0].new_offset:X}" if literal.lower().startswith("0x") else str(fs[0].new_offset)
        lines[lineno - 1] = line[:start] + new + line[end:]
    return "".join(lines)


def cmd_stale(dump: TypeDump, args) -> int:
    """Report the hard-coded offsets in source files that a new dump says have moved."""
    old = TypeDump.load(Path(args.old))
    new = dump
    paths = [f for p in map(Path, args.paths) for f in (sorted(p.rglob("*.py")) if p.is_dir() else [p])]
    findings: list[Stale] = []
    unmatched: set[str] = set()
    for path in paths:
        # Bytes, not read_text: that would turn a CRLF file's endings into LF on --fix
        source = path.read_bytes().decode("utf-8")
        got, missing = stale_offsets(source, str(path), old, new)
        findings += got
        unmatched |= missing
        if args.fix and any(f.kind == "moved" for f in got):
            path.write_bytes(fix_moved(source, got).encode("utf-8"))
            print(f"rewrote {sum(f.kind == 'moved' for f in got)} offsets in {path}")
    for kind in ("moved", "gone", "raw"):
        rows = [f for f in findings if f.kind == kind]
        print(f"{kind}: {len(rows)}")
        for f in rows:
            print(f"  {f.path}:{f.line}  {f.klass} @{f.offset}  {f.note}")
    if args.verbose and unmatched:
        print(f"\nclasses with no game class of their name, not checked: {', '.join(sorted(unmatched))}")
    return 1 if any(f.kind in ("moved", "gone") for f in findings) else 0


def cmd_diff(dump: TypeDump, args) -> int:
    """Compare two dumps: which class properties moved between revisions."""
    old = TypeDump.load(Path(args.old))
    new = TypeDump.load(Path(args.new))
    old_keys, new_keys = set(old.raw), set(new.raw)

    added, removed = new_keys - old_keys, old_keys - new_keys
    print(f"classes: {len(old_keys)} -> {len(new_keys)}  (+{len(added)} / -{len(removed)})\n")

    moved = 0
    for key in sorted(old_keys & new_keys):
        o = {n: b.get("offset") for n, b in old.raw[key].get("properties", {}).items()}
        n_ = {n: b.get("offset") for n, b in new.raw[key].get("properties", {}).items()}
        deltas = [(k, o[k], n_[k]) for k in o.keys() & n_.keys() if o[k] != n_[k]]
        gone = o.keys() - n_.keys()
        fresh = n_.keys() - o.keys()
        if not (deltas or gone or fresh):
            continue
        moved += 1
        if moved > args.limit:
            continue
        print(key)
        for name, a, b in sorted(deltas, key=lambda t: t[1] or 0):
            print(f"    {name:<40} {a} -> {b}")
        for name in sorted(gone):
            print(f"    {name:<40} removed (was {o[name]})")
        for name in sorted(fresh):
            print(f"    {name:<40} added at {n_[name]}")
    if moved > args.limit:
        print(f"\n... {moved - args.limit} more changed classes (use --limit)")
    print(f"\n{moved} classes changed layout")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", type=Path, default=None, help="type dump JSON (default: newest in tools/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("show", help="all offsets on a class")
    p.add_argument("klass")
    p.add_argument("--own", action="store_true", help="skip inherited properties")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("at", help="what property lives at an offset")
    p.add_argument("klass")
    p.add_argument("offset", type=lambda s: int(s, 0))
    p.add_argument("--window", type=int, default=64, help="near-miss radius in bytes when nothing matches exactly")
    p.set_defaults(fn=cmd_at)

    p = sub.add_parser("prop", help="find a property name across all classes")
    p.add_argument("pattern")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--pointers", action="store_true", help="include the redundant 'class Foo*' variants")
    p.set_defaults(fn=cmd_prop)

    p = sub.add_parser("classes", help="find class names by regex")
    p.add_argument("pattern")
    p.add_argument("--limit", type=int, default=60)
    p.add_argument("--pointers", action="store_true", help="include the redundant 'class Foo*' variants")
    p.set_defaults(fn=cmd_classes)

    p = sub.add_parser("gaps", help="find unreflected holes where raw C++ members hide")
    p.add_argument("klass")
    p.add_argument("--min", type=int, default=8, help="smallest gap worth reporting")
    p.set_defaults(fn=cmd_gaps)

    p = sub.add_parser("annotate", help="name the hardcoded offsets in a wizwalker source file")
    p.add_argument("path")
    p.set_defaults(fn=cmd_annotate)

    p = sub.add_parser("stale", help="the hard-coded offsets in source files that the dump says have moved")
    p.add_argument("paths", nargs="+", help="files or folders of .py files")
    p.add_argument("--old", required=True, help="the dump the offsets were written against")
    p.add_argument("--verbose", action="store_true", help="also list the classes that could not be checked")
    p.add_argument("--fix", action="store_true", help="rewrite each moved offset in place; raw ones stay by hand")
    p.set_defaults(fn=cmd_stale)

    p = sub.add_parser("diff", help="compare two dumps after a game patch")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=cmd_diff)

    args = ap.parse_args(argv)
    if args.fn is cmd_diff:
        return args.fn(None, args)
    return args.fn(TypeDump.load(args.dump or default_dump()), args)


if __name__ == "__main__":
    raise SystemExit(main())
