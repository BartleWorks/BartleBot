"""A made-up Wizard101 install for the game art extractor: real KIWAD archives, synthetic contents.

Nothing here is the game's: the textures are tiny PNGs drawn here (Pillow opens an image
by its content, so a ``.dds`` name holding PNG bytes decodes the same), the spells are
templates serialized against a type list of one class, and every name is invented. So
the extractor runs end to end, archives, deserializer and all, without a game install and
without committing an asset (root ``CLAUDE.md``, "No game assets, either").

``game_install(root)`` writes ``Data/GameData`` under *root* with ``Root.wad`` and
``_Shared-WorldData.wad``, a client so ``is_install`` accepts the folder, and the type list
beside them, and returns the paths.
"""

from __future__ import annotations

import io
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from bartlebot.game.art_extraction import ICON_SOURCES, game_data_dir

#: The type list's one class, and its one property, as the real dump spells them.
SPELL_CLASS = "class SpellTemplate"
SPELL_HASH = 1864220976
IMAGE_NAME_HASH = 2391520543

#: Template name -> the texture it names. Two share one image; one names a texture no archive holds.
SPELLS = {
    "Fixture Bolt": "Spell_Fixture_Bolt.dds",
    "Fixture Bolt - T02": "Spell_Fixture_Bolt.dds",
    "Fixture Ward": "Spell_Fixture_Ward.dds",
    "Fixture Ghost": "Spell_Fixture_Missing.dds",
}
#: Which spell texture sits in which archive: the extractor looks in both.
SHARED_TEXTURES = ("Spell_Fixture_Bolt.dds",)
ROOT_TEXTURES = ("Spell_Fixture_Ward.dds",)
#: A few of the icons the tables name, so the icon pass writes something.
ICON_KEYS = ("archmastery", "universal", "school_fire")


@dataclass(frozen=True)
class FixtureInstall:
    install: Path
    game_data: Path
    types: Path


def _png(color: tuple[int, int, int, int], size: int = 8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _ki_crc(data: bytes) -> int:
    """KingsIsle's archive checksum: CRC-32 with no initial or final inversion."""
    return zlib.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF


def write_wad(path: Path, files: dict[str, bytes]) -> None:
    """A KIWAD version 2 archive of *files*, stored uncompressed."""
    names = list(files)
    header = b"KIWAD" + struct.pack("<II", 2, len(names)) + b"\x01"
    table_size = sum(4 * 3 + 1 + 4 + 4 + len(name.encode()) + 1 for name in names)
    offset = len(header) + table_size
    table = b""
    blobs = b""
    for name in names:
        data = files[name]
        encoded = name.encode() + b"\x00"
        table += struct.pack("<IIIBII", offset + len(blobs), len(data), len(data), 0, _ki_crc(data), len(encoded))
        table += encoded
        blobs += data
    path.write_bytes(header + table + blobs)


def spell_blob(image_name: str) -> bytes:
    """A spell template as the game stores it: BINd, then one object holding ``m_imageName``."""
    name = image_name.encode()
    value = struct.pack("<H", len(name)) + name
    prop = struct.pack("<II", (8 + len(value)) * 8, IMAGE_NAME_HASH) + value
    obj = struct.pack("<II", SPELL_HASH, (4 + len(prop)) * 8) + prop
    # STATEFUL_FLAGS, as the game writes it. With 0 here, a reused serializer misreads every blob after the first.
    return b"BINd" + struct.pack("<I", 1) + obj


def type_list() -> dict:
    return {
        SPELL_CLASS: {
            "bases": ["PropertyClass"],
            "hash": SPELL_HASH,
            "properties": {
                "m_imageName": {
                    "type": "std::string",
                    "id": 0,
                    "offset": 0,
                    "flags": 131079,
                    "container": "Static",
                    "dynamic": False,
                    "singleton": False,
                    "pointer": False,
                    "hash": IMAGE_NAME_HASH,
                }
            },
        }
    }


def game_install(root: Path) -> FixtureInstall:
    install = root / "Wizard101"
    (install / "Bin").mkdir(parents=True)
    (install / "Bin" / "WizardGraphicalClient.exe").write_bytes(b"")
    game_data = game_data_dir(install)
    game_data.mkdir(parents=True)

    shared = {f"GUI/SummonedImages/{name}": _png((200, 40, 40, 255)) for name in SHARED_TEXTURES}
    for key in ICON_KEYS:
        texture = ICON_SOURCES[key]
        path = texture if "/" in texture else f"GUI/Icons/{texture}"
        shared[path] = _png((40, 40, 200, 255))
    write_wad(game_data / "_Shared-WorldData.wad", shared)

    root_files = {f"Spells/Fixture/{template}.xml": spell_blob(image) for template, image in SPELLS.items()}
    root_files.update({f"GUI/SummonedImages/{name}": _png((40, 200, 40, 255)) for name in ROOT_TEXTURES})
    write_wad(game_data / "Root.wad", root_files)

    types = root / "types.json"
    types.write_text(json.dumps(type_list()), encoding="utf-8")
    return FixtureInstall(install, game_data, types)
