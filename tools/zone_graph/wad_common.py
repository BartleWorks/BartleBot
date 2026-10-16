"""Shared helpers for the zone-graph tools: where the game data lives, katsuba setup, and output shaping.

Not a script. The sibling tools import it by name, which works because Python puts a
script's own directory on ``sys.path``.
"""

from __future__ import annotations

import json
import re
import struct
import sys
from collections.abc import Iterator
from pathlib import Path

from katsuba.op import STATEFUL_FLAGS, LazyList, LazyObject, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive

GAME_DATA = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData")
TOOLS_DIR = Path(__file__).resolve().parent.parent
# Deimos' hand-built navigation data, in our wizsprinter checkout.
TRAVERSAL_DATA = Path(r"C:\GitHub\wizsprinter\wizwalker\extensions\wizsprinter\traversalData")
# Root.wad ships Locale/<locale>/ for de, el, en-US, es, fr, it and pl; the tools only expose English.
DEFAULT_LOCALE = "en-US"

sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR.parent))

from bartlebot.game.art_extraction import TYPE_DUMP_DIR, newest_type_dump  # noqa: E402

# The newest dump, from game/data/ where the app ships it or tools/ where older ones stay.
DEFAULT_TYPES = newest_type_dump(TYPE_DUMP_DIR, TOOLS_DIR)

from dump_dialog_captions import parse_lang  # noqa: E402, F401  (re-exported for the tools)


def root_wad(game_data: Path = GAME_DATA) -> Archive:
    return Archive.mmap(str(game_data / "Root.wad"))


def zone_wad_path(zone: str, game_data: Path = GAME_DATA) -> Path:
    """``WizardCity/WC_Streets/WC_Unicorn`` -> ``GameData/WizardCity-WC_Streets-WC_Unicorn.wad``."""
    return game_data / (zone.replace("/", "-") + ".wad")


def zone_wad(zone: str, game_data: Path = GAME_DATA) -> Archive:
    path = zone_wad_path(zone, game_data)
    if not path.exists():
        raise FileNotFoundError(f"no wad for zone {zone!r} at {path}")
    return Archive.mmap(str(path))


def _serializer(types: TypeList, flags: int) -> Serializer:
    opts = SerializerOptions()
    opts.flags = flags
    opts.shallow = False
    opts.skip_unknown_types = True
    return Serializer(opts, types)


class Deserializer:
    """Deserialize either kind of serialized object the zone wads hold.

    ``BINd``-prefixed files (``Compass.xml``, ``spawnData.xml``, most of Root.wad) use the
    stateful flags. A zone's ``gamedata.bin`` has no prefix and only reads with flags 0.
    """

    def __init__(self, types_path: Path = DEFAULT_TYPES) -> None:
        types = TypeList.open(str(types_path))
        self._bind = _serializer(types, STATEFUL_FLAGS)
        self._raw = _serializer(types, 0)

    def __call__(self, data: bytes):
        if data.startswith(b"BINd"):
            return self._bind.deserialize(data[4:])
        return self._raw.deserialize(data)


def to_plain(value):
    """Convert katsuba's lazy objects into JSON-serializable Python values."""
    if isinstance(value, LazyObject):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, LazyList | list):
        return [to_plain(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "x") and hasattr(value, "y"):
        return [value.x, value.y] + ([value.z] if hasattr(value, "z") else [])
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value


def text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


def xyz(value) -> tuple[float, float, float]:
    return (value.x, value.y, value.z)


def zone_display_names(root: Archive, locale: str = DEFAULT_LOCALE) -> dict[str, str]:
    """``WizardZone.lang`` for one locale: display-name key (without ``WizardZone_``) -> name."""
    return parse_lang(bytes(root[f"Locale/{locale}/WizardZone.lang"]))


def display_name(record, names: dict[str, str]) -> str | None:
    """A zone record's display name, e.g. ``WizardZone_ColossusBlvd`` -> ``Colossus Boulevard``.

    Side rooms usually carry their street's name, so one name can belong to several zones.
    """
    return names.get(text(record["m_zoneDisplayName"]).removeprefix("WizardZone_"))


# volumes.xml property records: a u32 size in bits, a u32 property hash, then the value. The
# three position floats are 96-bit records with consecutive hashes.
_FLOAT_RECORD = struct.pack("<I", 96)
_POSITION_HASHES = (bytes.fromhex("28f8b37d"), bytes.fromhex("29f8b37d"), bytes.fromhex("2af8b37d"))
_NAME_LOOKBACK = 200


def volumes(data: bytes) -> list[tuple[str, tuple[float, float, float]]]:
    """(name, position) of every volume in a zone's ``volumes.xml``.

    katsuba cannot deserialize the file with our type dump, but the layout is plain enough to
    read directly: each volume's name is a string whose first byte is its length times two,
    and its position follows as three float records. The name is the nearest such string
    ending before the position. Sizes and shapes are not decoded.
    """
    x_key = _FLOAT_RECORD + _POSITION_HASHES[0]
    found = []
    at = data.find(x_key)
    while at >= 0:
        y_at, z_at = at + 12, at + 24
        if data[y_at + 4 : y_at + 8] == _POSITION_HASHES[1] and data[z_at + 4 : z_at + 8] == _POSITION_HASHES[2]:
            x, y, z = (struct.unpack_from("<f", data, off + 8)[0] for off in (at, y_at, z_at))
            found.append((_name_before(data, at), (x, y, z)))
        at = data.find(x_key, at + 1)
    return found


def _name_before(data: bytes, end: int) -> str:
    best: tuple[int, int] | None = None  # (end of string, length)
    for p in range(end - 4, max(end - _NAME_LOOKBACK, 0), -1):
        if data[p] & 1:
            continue
        length = data[p] >> 1
        stop = p + 1 + length
        if length < 3 or stop > end or not all(0x20 <= b <= 0x7E for b in data[p + 1 : stop]):
            continue
        if best is None or (stop, length) > best:
            best = (stop, length)
    return data[best[0] - best[1] : best[0]].decode("ascii") if best else "?"


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
