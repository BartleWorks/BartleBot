"""Add a Scales of Knowledge to the shipped table from a bot recorded in the app.

``bartlebot/game/data/scales_of_knowledge.json`` is never edited by hand. This tool builds an entry
from points Record already captured, checks it, and writes the whole file back in one fixed
format (see ``docs/LEVEL_SCALING.md``).

To add a Scales:

1. In the app, make a scratch bot and record, standing in game:
   - an ``enter_dungeon`` on the dungeon's sigil,
   - an ``enter_zone`` for each zone change between the sigil and the Scales, or between a
     zone the zone picker offers and the sigil, if there are any,
   - an ``interact`` on the Scales: its zone, the point where the prompt shows, and a reset
     point where it does not.
   The nodes do not need edges between them. Save the bot anywhere.
2. Run::

       uv run python tools/scales_table.py add path/to/scratch.json --level 100

   It prints the entry it found and writes nothing. Add ``--write`` to save it.

The steps are found by following zones back from the Scales to the first zone the navigation
map can reach, so the order the nodes were recorded in does not matter.

Other commands::

    uv run python tools/scales_table.py list                 # every entry, one line each
    uv run python tools/scales_table.py check                # the checks the test suite makes
    uv run python tools/scales_table.py set KEY --level 90 --world-default   # change a label field
    uv run python tools/scales_table.py remove KEY --write
    uv run python tools/scales_table.py format --write       # rewrite the file in the fixed format

A key is what bot files store, so ``set`` cannot change one and ``remove`` breaks every bot
that names it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.game import scales, zone_names, zone_nav  # noqa: E402
from bartlebot.nodes.node_helpers import has_glob, zone_matches  # noqa: E402

COMMENT = [
    "Every Scales of Knowledge handle_level_scaling can use. Read by bartlebot/game/scales.py.",
    "Written by tools/scales_table.py: add and change entries with it, not by hand.",
    "A bot file stores an entry's key, so a key is never renamed once shipped.",
    "See docs/LEVEL_SCALING.md.",
]

MAX_STEPS = 6
"""The longest chain of zone changes an entry is given before the walk back gives up."""

_FIELD_ORDER = (
    "key",
    "name",
    "world",
    "level",
    "world_default",
    "entrance_zone",
    "steps",
    "zone",
    "interact_xyz",
    "reset_xyz",
)


class TableError(Exception):
    """Something the user can fix: a bot missing a node, a key already taken, a broken entry."""


# ---------------------------------------------------------------------------
# Reading and writing the file
# ---------------------------------------------------------------------------


def read_entries(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["scales"]
    except FileNotFoundError:
        return []
    except (ValueError, KeyError, TypeError) as error:
        raise TableError(f"{path} cannot be read: {error}") from error


def _ordered(entry: dict) -> dict:
    """*entry* with its fields in the table's order, and ``world_default`` kept only when true."""
    ordered = {name: entry[name] for name in _FIELD_ORDER if name in entry}
    if not ordered.get("world_default"):
        ordered.pop("world_default", None)
    return ordered


def render(entries: list[dict]) -> str:
    """The file's text for *entries*: the one format the table is ever written in."""
    document = {"_comment": COMMENT, "scales": [_ordered(entry) for entry in entries]}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write_entries(path: Path, entries: list[dict]) -> None:
    path.write_text(render(entries), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Building an entry from a bot
# ---------------------------------------------------------------------------


def slug(name: str) -> str:
    """ "Emperor's Throne Room" -> "emperors_throne_room": a key made from a display name."""
    return re.sub(r"[^a-z0-9]+", "_", name.replace("'", "").replace("’", "").casefold()).strip("_")


#: The node a scratch bot records the Scales themselves with.
STATION_NODE_TYPE = "interact"


def _candidates(states: list[dict]) -> list[dict]:
    """Every recorded zone change in the bot, as a table step plus the sigil's prompt title."""
    found = []
    for state in states:
        params = state.get("params") or {}
        if state.get("node_type") == "enter_dungeon":
            kind, to_zone, xyz = scales.SIGIL, params.get("dungeon_zone"), params.get("sigil_xyz")
        elif state.get("node_type") == "enter_zone":
            kind, to_zone, xyz = scales.GATEWAY, params.get("to_zone"), params.get("xyz")
        else:
            continue
        if not (params.get("from_zone") and to_zone and xyz and params.get("reset_xyz")):
            continue
        found.append(
            {
                "step": {
                    "kind": kind,
                    "from_zone": params["from_zone"],
                    "to_zone": to_zone,
                    "xyz": xyz,
                    "reset_xyz": params["reset_xyz"],
                },
                "dungeon_name": (params.get("dungeon_name") or "").strip(),
            }
        )
    return found


def _steps_to(zone: str, candidates: list[dict], nav: zone_nav.NavMap) -> list[dict]:
    """The recorded zone changes that lead to *zone*, walked back to a zone navigation can reach."""
    chain: list[dict] = []
    target = zone
    while not (any(c["step"]["kind"] == scales.SIGIL for c in chain) and zone_nav.is_destination(nav, target)):
        if len(chain) == MAX_STEPS:
            raise TableError(f"more than {MAX_STEPS} zone changes lead to {zone}; the recorded zones may loop")
        used = [(c["step"]["from_zone"], c["step"]["kind"]) for c in chain]
        into = [
            c
            for c in candidates
            if zone_matches(target, c["step"]["to_zone"]) and (c["step"]["from_zone"], c["step"]["kind"]) not in used
        ]
        if not into and chain and zone_nav.is_destination(nav, target):
            raise TableError(
                f"the recorded way from {target} to {zone} goes through no dungeon sigil. A Scales is inside a "
                f"dungeon: record the sigil with an enter_dungeon."
            )
        if not into:
            needs = "an enter_dungeon" if not chain else "an enter_dungeon or enter_zone"
            raise TableError(
                f"nothing in the bot leads into {target}. Record {needs} into it, starting from a zone the "
                f"zone picker offers."
            )
        # A sigil before a walk back out of a side room; then whichever starts somewhere navigable.
        into.sort(
            key=lambda c: (
                c["step"]["kind"] != scales.SIGIL,
                not zone_nav.is_destination(nav, c["step"]["from_zone"]),
            )
        )
        # A bot's dungeon zone may be a glob that also takes in the Challenge Mode copy. The table
        # holds the ordinary zone only: the node enters in normal mode, and the copy has no Scales.
        step = {**into[0]["step"], "to_zone": target}
        chain.insert(0, {**into[0], "step": step})
        target = step["from_zone"]
    return chain


def entry_from_bot(
    bot: dict,
    *,
    state_id: str | None = None,
    key: str | None = None,
    name: str | None = None,
    level: int | None = None,
    world_default: bool = False,
    nav: zone_nav.NavMap | None = None,
) -> dict:
    """The table entry for the Scales a bot's ``interact`` node was recorded on.

    An ``interact`` because it records exactly the station's three fields and is staying in the
    library; the ``apply_level_scaling`` that also recorded the same three has been removed.
    """
    nav = zone_nav.load() if nav is None else nav
    states = bot.get("states") or []
    stations = [s for s in states if s.get("node_type") == STATION_NODE_TYPE]
    if state_id is not None:
        stations = [s for s in stations if s.get("id") == state_id]
    if not stations:
        raise TableError(f"the bot has no {STATION_NODE_TYPE} node" + (f" with id {state_id!r}" if state_id else ""))
    if len(stations) > 1:
        ids = ", ".join(s["id"] for s in stations)
        raise TableError(f"the bot has several {STATION_NODE_TYPE} nodes ({ids}); pick one with --state")
    params = stations[0].get("params") or {}
    zone = params.get("zone")
    if not (zone and params.get("interact_xyz") and params.get("reset_xyz")):
        raise TableError(f"the {STATION_NODE_TYPE} node is not recorded: it needs a zone and both points")

    chain = _steps_to(zone, _candidates(states), nav)
    if name is None:
        # The sigil's prompt names the dungeon, unless the Scales are a zone further in.
        name = chain[-1]["dungeon_name"] or zone_names.label(zone)
    entry = {
        "key": key or slug(name),
        "name": name,
        "world": zone.split("/", 1)[0],
        "level": level,
        "world_default": world_default,
        "entrance_zone": chain[0]["step"]["from_zone"],
        "steps": [c["step"] for c in chain],
        "zone": zone,
        "interact_xyz": params["interact_xyz"],
        "reset_xyz": params["reset_xyz"],
    }
    return _ordered(entry)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def problems(entries: list[dict], nav: zone_nav.NavMap | None = None) -> list[str]:
    """Everything wrong with *entries*, one line each. Empty when the table is sound."""
    nav = zone_nav.load() if nav is None else nav
    found: list[str] = []
    seen: set[str] = set()
    defaults: dict[str, str] = {}
    for raw in entries:
        label = raw.get("key", "?") if isinstance(raw, dict) else "?"
        try:
            entry = scales._parse(raw)  # noqa: SLF001 - the loader's own rule for a readable entry
        except (KeyError, TypeError, ValueError) as error:
            found.append(f"{label}: cannot be read ({error!r})")
            continue
        if not re.fullmatch(r"[a-z0-9_]+", entry.key):
            found.append(f"{label}: the key must be lower-case letters, digits and underscores")
        if entry.key in seen:
            found.append(f"{label}: the key is used twice")
        seen.add(entry.key)
        named = [entry.entrance_zone, entry.zone, *(z for s in entry.steps for z in (s.from_zone, s.to_zone))]
        if globbed := sorted({z for z in named if has_glob(z)}):
            found.append(f"{label}: {globbed[0]} is a pattern; the table names one zone, never a Challenge Mode copy")
        if copies := sorted({z for z in named if zone_names.challenge_variant_of(z)}):
            found.append(f"{label}: {copies[0]} is a Challenge Mode copy; the table names the ordinary zone")
        if not zone_nav.is_destination(nav, entry.entrance_zone):
            found.append(f"{label}: entrance zone {entry.entrance_zone} is not a destination on the zone map")
        if entry.steps[0].from_zone != entry.entrance_zone:
            found.append(f"{label}: the first step does not start in the entrance zone")
        for before, after in zip(entry.steps, entry.steps[1:]):
            if not zone_matches(after.from_zone, before.to_zone):
                found.append(f"{label}: step into {after.to_zone} does not start where the one before it ends")
        if not zone_matches(entry.zone, entry.steps[-1].to_zone):
            found.append(f"{label}: the last step does not end in the Scales' zone")
        if entry.world_default:
            if entry.world in defaults:
                found.append(f"{label}: {entry.world} already has a default, {defaults[entry.world]}")
            defaults[entry.world] = entry.key
    return found


def add_entry(entries: list[dict], entry: dict, *, replace: bool = False) -> list[dict]:
    """*entries* with *entry* added, or put in place of the one with its key when *replace*."""
    index = next((i for i, existing in enumerate(entries) if existing.get("key") == entry["key"]), None)
    if index is None:
        return [*entries, entry]
    if not replace:
        raise TableError(f"the table already has {entry['key']!r}; pass --replace to overwrite it, or --key to rename")
    return [*entries[:index], entry, *entries[index + 1 :]]


def set_fields(entries: list[dict], key: str, **fields) -> list[dict]:
    """*entries* with the given label fields changed on the entry named *key*."""
    if not any(entry.get("key") == key for entry in entries):
        raise TableError(f"the table has no {key!r}")
    return [{**entry, **fields} if entry.get("key") == key else entry for entry in entries]


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _summary(entry: dict) -> str:
    level = "level ?" if entry.get("level") is None else f"level {entry['level']}"
    default = ", world default" if entry.get("world_default") else ""
    route = " > ".join([entry["entrance_zone"], *(f"[{s['kind']}] {s['to_zone']}" for s in entry["steps"])])
    return f"{entry['key']}: {entry['name']} ({entry['world']}, {level}{default})\n    {route}"


def _save(path: Path, entries: list[dict], write: bool) -> int:
    if found := problems(entries):
        print("Not written. The table would have these problems:")
        print("\n".join(f"  {line}" for line in found))
        return 1
    if not write:
        print("Nothing written. Pass --write to save.")
        return 0
    write_entries(path, entries)
    print(f"Wrote {len(entries)} entries to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--table", type=Path, default=scales.SCALES_PATH, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add the Scales a bot's interact node was recorded on")
    add.add_argument("bot", type=Path, help="a bot file holding the recorded nodes")
    add.add_argument("--state", help="the interact node's id, when the bot has several")
    add.add_argument("--key", help="the key bot files store (default: made from the name)")
    add.add_argument("--name", help="the name the picker shows (default: the sigil's prompt title)")
    add.add_argument("--level", type=int, help="the level this Scales scales a player to")
    add.add_argument("--world-default", action="store_true", help="the Scales of this world's final dungeon")
    add.add_argument("--replace", action="store_true", help="overwrite the entry that already has this key")
    add.add_argument("--write", action="store_true")

    change = commands.add_parser("set", help="change an entry's name, level or world default")
    change.add_argument("key")
    change.add_argument("--name")
    change.add_argument("--level", type=int)
    default = change.add_mutually_exclusive_group()
    default.add_argument("--world-default", dest="world_default", action="store_true", default=None)
    default.add_argument("--no-world-default", dest="world_default", action="store_false")
    change.add_argument("--write", action="store_true")

    remove = commands.add_parser("remove", help="remove an entry (breaks every bot that names it)")
    remove.add_argument("key")
    remove.add_argument("--write", action="store_true")

    commands.add_parser("list", help="print every entry")
    commands.add_parser("check", help="report anything wrong with the table")
    commands.add_parser("format", help="rewrite the file in the fixed format").add_argument(
        "--write", action="store_true"
    )

    args = parser.parse_args(argv)
    path: Path = args.table
    try:
        entries = read_entries(path)
        if args.command == "list":
            print("\n".join(_summary(entry) for entry in entries) or "The table is empty.")
            return 0
        if args.command == "check":
            found = problems(entries)
            if path.exists() and path.read_text(encoding="utf-8") != render(entries):
                found.append("the file is not in the fixed format; run: tools/scales_table.py format --write")
            print("\n".join(found) or f"{len(entries)} entries, no problems.")
            return 1 if found else 0
        if args.command == "format":
            return _save(path, entries, args.write)
        if args.command == "add":
            bot = json.loads(args.bot.read_text(encoding="utf-8"))
            entry = entry_from_bot(
                bot,
                state_id=args.state,
                key=args.key,
                name=args.name,
                level=args.level,
                world_default=args.world_default,
            )
            print(_summary(entry))
            if args.level is None:
                print("    No --level given: the picker will show this Scales without one.")
            return _save(path, add_entry(entries, entry, replace=args.replace), args.write)
        if args.command == "set":
            fields = {
                name: value
                for name, value in (("name", args.name), ("level", args.level), ("world_default", args.world_default))
                if value is not None
            }
            if not fields:
                raise TableError("nothing to change: pass --name, --level, --world-default or --no-world-default")
            entries = set_fields(entries, args.key, **fields)
            print(_summary(next(entry for entry in entries if entry["key"] == args.key)))
            return _save(path, entries, args.write)
        if args.command == "remove":
            if not any(entry.get("key") == args.key for entry in entries):
                raise TableError(f"the table has no {args.key!r}")
            return _save(path, [entry for entry in entries if entry.get("key") != args.key], args.write)
    except (TableError, OSError, ValueError) as error:
        print(f"Error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
