"""Shared helpers for the zone-graph tools: where the game data lives, katsuba setup, and output shaping.

Not a script. The sibling tools import it by name, which works because Python puts a
script's own directory on ``sys.path``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

from bartleby.runtime.movement.zone_links import parse_volumes
from katsuba.wad import Archive

TOOLS_DIR = Path(__file__).resolve().parent.parent
#: Core's tools, in the checkout beside this one (core's docs/UPSTREAM.md, the repos' layout). The
#: wad reader is core's (``zone_files.py``), for the zone maps it builds; these tools use it too.
CORE_TOOLS = TOOLS_DIR.parent.parent / "bartleby-core" / "tools"
# Deimos' hand-built navigation data, in our wizsprinter checkout.
TRAVERSAL_DATA = Path(r"C:\GitHub\wizsprinter\wizwalker\extensions\wizsprinter\traversalData")
# Root.wad ships Locale/<locale>/ for de, el, en-US, es, fr, it and pl; the tools only expose English.
DEFAULT_LOCALE = "en-US"

if not (CORE_TOOLS / "zone_files.py").exists():
    raise SystemExit(f"the wad reader is core's: check out bartleby-core beside this repo ({CORE_TOOLS} is missing)")
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR.parent))
sys.path.append(str(CORE_TOOLS))  # after ours, so a tool of ours is never shadowed by core's

import zone_files  # noqa: E402
from zone_files import (  # noqa: E402, F401  (re-exported for the tools)
    GAME_DATA,
    locations,
    root_wad,
    text,
    to_plain,
    wad_entry,
    xyz,
    zone_record,
    zone_wad,
    zone_wad_path,
)

from bartlebot.game.art_extraction import TYPE_DUMP_DIR, newest_type_dump  # noqa: E402

# The newest dump, from game/data/ where the app ships it or tools/ where older ones stay.
DEFAULT_TYPES = newest_type_dump(TYPE_DUMP_DIR, TOOLS_DIR)

from dump_world_names import parse_lang  # noqa: E402, F401  (re-exported for the tools)


class Deserializer(zone_files.Deserializer):
    """Core's, reading against the newest type dump unless told otherwise."""

    def __init__(self, types_path: Path = DEFAULT_TYPES) -> None:
        super().__init__(types_path)


def reader(game_data: Path = GAME_DATA, types_path: Path = DEFAULT_TYPES) -> zone_files.ZoneReader:
    """Core's reader of zones into the planner's ``ZoneFiles``, on this install and dump."""
    return zone_files.ZoneReader(types_path, game_data)


def zone_display_names(root: Archive, locale: str = DEFAULT_LOCALE) -> dict[str, str]:
    """``WizardZone.lang`` for one locale: display-name key (without ``WizardZone_``) -> name."""
    return parse_lang(bytes(root[f"Locale/{locale}/WizardZone.lang"]))


def display_name(record, names: dict[str, str]) -> str | None:
    """A zone record's display name, e.g. ``WizardZone_ColossusBlvd`` -> ``Colossus Boulevard``.

    Side rooms usually carry their street's name, so one name can belong to several zones.
    """
    return names.get(text(record["m_zoneDisplayName"]).removeprefix("WizardZone_"))


def volumes(data: bytes) -> list[tuple[str, tuple[float, float, float]]]:
    """(name, position) of every volume in a zone's ``volumes.xml``. The parser is core's
    (``bartleby.runtime.movement.zone_links.parse_volumes``), which reads each one's shape and
    size too."""
    return [(v.name, v.center) for v in parse_volumes(data)]


def printable_strings(data: bytes, min_len: int = 5) -> Iterator[str]:
    """Runs of printable ASCII, for files katsuba cannot deserialize (e.g. ``triggers.xml``)."""
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        yield m.group().decode("ascii")


def read_traversal_section(filename: str, world: str, root: Path = TRAVERSAL_DATA) -> list[list[str]]:
    """The ``;``-split rows of one ``WORLD - <world>`` ... ``END`` block of a Deimos traversal file."""
    rows: list[list[str]] = []
    inside = False
    for line in (root / filename).read_text(encoding="utf-8").splitlines():
        if line.startswith("WORLD - "):
            inside = line == f"WORLD - {world}"
        elif line.strip() == "END":
            inside = False
        elif inside and line.strip():
            rows.append([part.strip() for part in line.strip().split(";")])
    return rows
