"""Extract spell card art from the installed game into the local art cache.

Run by the user, on their own machine, never by the app on a schedule::

    uv run python tools/extract_card_art.py
    uv run python tools/extract_card_art.py --dry-run
    uv run python tools/extract_card_art.py --game-dir "D:/Games/Wizard101/Data/GameData"

WHY THIS IS A TOOL AND NOT A FEATURE
    Nothing extracted may ever become a tracked file: the repo goes public at 1.0.0
    and a committed game texture carries the same exposure as committed player data
    (see "No real player data" in the root ``CLAUDE.md``). Writing into
    ``%APPDATA%/Bartleby/BartleBot/art`` puts the output somewhere the repo cannot
    reach even by accident, and leaves every install without art until its user asks
    for it. ``bartlebot/card_art.py`` is the read side, and treats absent art as the
    ordinary case.

WHERE THE ART COMES FROM
    Each spell under ``Spells/`` in ``Root.wad`` is a BINd blob whose ``m_imageName``
    names a texture by bare filename ("Spell_Storm_Tempest.dds"). Those textures live
    in ``GUI/SummonedImages/`` -- nearly all in ``_Shared-WorldData.wad``, a dozen in
    ``Root.wad`` -- so this resolves by basename across both rather than by path.
    Reading the *named* field needs a real deserializer: see the byte-scan failure
    described at the top of ``tools/dump_spell_names.py``.

    The textures are DXT3/DXT5 DDS, which Qt cannot read (its build has no dds
    plugin), so they are converted to PNG here. They are written **uncropped**, at
    their full 128x128 with the circular frame and its transparent corners intact:
    the display size and any crop are the UI's business and change more often than
    the cache should be rebuilt.

    One PNG per *image*, not per template: 18,000 templates share about 1,300
    images, and the index maps every template name onto the file it uses.

REGENERATE IT when a game patch adds or reworks spells. Art already extracted is
overwritten; nothing here is incremental, because the whole run costs a few seconds.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from datetime import date
from pathlib import Path, PurePosixPath

from katsuba.op import STATEFUL_FLAGS, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartlebot.card_art import (  # noqa: E402
    ARCHMASTERY_ICON,
    DUNGEON_ICON,
    ICONS_DIRNAME,
    INDEX_FILENAME,
    UNIVERSAL_ICON,
    art_dir,
    school_icon_name,
    spells_dir,
    world_icon_name,
)
from bartlebot.spell_schools import SCHOOL_ORDER  # noqa: E402
from bartlebot.worlds import DUNGEON_WORLDS  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_GAME_DIR = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData")
DEFAULT_TYPES = Path(__file__).resolve().parent / "r800294_WizardDev.json"

SPELLS_GLOB = "Spells/**/*.xml"
#: Searched in order; the first WAD holding a texture of that name wins. _Shared first
#: because that is where all but a dozen of them are.
TEXTURE_WADS = ("_Shared-WorldData.wad", "Root.wad")
#: Where the spell textures sit inside those WADs.
TEXTURE_GLOB = "GUI/**"

#: Icon key -> the texture basename it comes from, resolved through the same
#: basename index the spell art uses.
#:
#: ``Art_School_`` is the ringed medallion the game's own archmastery picker draws, and
#: it covers all eleven schools -- seven in ``Root.wad``, and Moon, Star, Sun and Shadow
#: in ``_Shared-WorldData.wad``. ``Type_Global`` is the game's universal mark, stamped
#: on card art that belongs to no school; it exists at 32x32 and nowhere larger, which
#: is the ceiling on how big the GUI may draw these.
#: Nav-map world key -> the ``Button_World_`` texture stem carrying its emblem.
#:
#: Written out rather than derived, and every entry is load-bearing even where it looks
#: like a case fold. The game spells these three different ways across its own files --
#: the world key is ``DragonSpire``, the world button ``Dragonspire``, the guild museum
#: door ``Dragonspyre`` -- so a normalizing rule is a rule that is right until it is
#: silently wrong. ``Wysteria`` is the one that cannot be derived at all: its texture is
#: named for its school, Pigswick Academy.
#:
#: Aquila is here despite not being on the spiral door: it has an emblem of its own, and
#: the zone picker lists it as a world like any other.
WORLD_TEXTURES: dict[str, str] = {
    "Aquila": "Aquila",
    "Arcanum": "Arcanum",
    "Avalon": "Avalon",
    "Azteca": "Azteca",
    "Celestia": "Celestia",
    "Darkmoor": "Darkmoor",
    "DragonSpire": "Dragonspire",
    "Empyrea": "Empyrea",
    "Grizzleheim": "Grizzleheim",
    "Karamelle": "Karamelle",
    "Khrysalis": "Khrysalis",
    "Krokotopia": "Krokotopia",
    "Lemuria": "Lemuria",
    "Marleybone": "Marleybone",
    "Mirage": "Mirage",
    "MooShu": "Mooshu",
    "Novus": "Novus",
    "Polaris": "Polaris",
    "Wallaru": "Wallaru",
    "WizardCity": "Wizard_City",
    "Wysteria": "Pigswick_Academy",
    "Zafaria": "Zafaria",
}

#: The generic dungeon badge, for the nav worlds that are dungeon groups. All three point
#: at one texture, the way many spell templates share one image.
DUNGEON_TEXTURE = "Badge_Category_Dungeons.dds"

#: Icon key -> texture basename, for the worlds and the dungeon stand-in.
WORLD_ICON_SOURCES: dict[str, str] = {
    **{world_icon_name(key): f"Button_World_{stem}.dds" for key, stem in WORLD_TEXTURES.items()},
    **{world_icon_name(key): DUNGEON_TEXTURE for key in DUNGEON_WORLDS},
    DUNGEON_ICON: DUNGEON_TEXTURE,
}

#: Icon key -> the texture basename it comes from, resolved through the same
#: basename index the spell art uses.
#:
#: ``Art_School_`` is the ringed medallion the game's own archmastery picker draws, and
#: it covers all eleven schools -- seven in ``Root.wad``, and Moon, Star, Sun and Shadow
#: in ``_Shared-WorldData.wad``. ``Type_Global`` is the game's universal mark, stamped
#: on card art that belongs to no school; it exists at 32x32 and nowhere larger, which
#: is the ceiling on how big the GUI may draw these.
ICON_SOURCES: dict[str, str] = {
    ARCHMASTERY_ICON: "Icon_Archmastery.dds",
    UNIVERSAL_ICON: "Type_Global.dds",
    **{school_icon_name(school): f"Art_School_{school}.dds" for school in SCHOOL_ORDER},
    **WORLD_ICON_SOURCES,
}

#: Icon keys whose texture is a 2x2 sheet of button states rather than a single image.
#:
#: The world buttons and the badge categories are all 256x256 sheets holding four 128px
#: quadrants: normal, two hover/pressed variants, and a desaturated one for a world the
#: player cannot reach. Only the top-left is wanted. Drawn whole, these would render at
#: quarter size with three ghosts around them -- which is exactly what a "why is the icon
#: tiny" bug looks like from the outside.
STATE_SHEET_ICONS: frozenset[str] = frozenset(WORLD_ICON_SOURCES)


def _decode(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _build_serializer(types_path: Path) -> Serializer:
    types = TypeList.open(str(types_path))
    opts = SerializerOptions()
    opts.flags = STATEFUL_FLAGS
    opts.shallow = False
    opts.skip_unknown_types = True
    return Serializer(opts, types)


def read_image_names(root_wad: Path, types_path: Path) -> dict[str, str]:
    """Template name -> the texture filename it names, for every spell in Root.wad.

    The WAD path's stem *is* the template's internal name (see
    ``tools/dump_spell_names.py``), so the key needs no deserializing -- only
    ``m_imageName`` does.
    """
    archive = Archive.mmap(str(root_wad))
    ser = _build_serializer(types_path)

    images: dict[str, str] = {}
    for path in archive.iter_glob(SPELLS_GLOB):
        try:
            data = archive[path]
            if data.startswith(b"BINd"):
                data = data[4:]
            image = _decode(ser.deserialize(data).get("m_imageName"))
        except Exception:
            logger.debug("could not read a spell template at %s", path, exc_info=True)
            continue
        if image:
            images[PurePosixPath(path).stem] = image
    return images


def index_textures(game_dir: Path) -> dict[str, tuple[Archive, str]]:
    """Lowercased texture basename -> the archive holding it and its path inside.

    By basename because ``m_imageName`` carries no directory, and across both WADs
    because the spell textures are split between them.
    """
    found: dict[str, tuple[Archive, str]] = {}
    for wad_name in TEXTURE_WADS:
        wad_path = game_dir / wad_name
        if not wad_path.exists():
            logger.warning("no %s in %s -- some art may be missing", wad_name, game_dir)
            continue
        archive = Archive.mmap(str(wad_path))
        for entry in archive.iter_glob(TEXTURE_GLOB):
            found.setdefault(PurePosixPath(entry).name.lower(), (archive, entry))
    return found


def extract(game_dir: Path, types_path: Path, out_dir: Path, *, dry_run: bool = False) -> dict:
    """Convert every spell texture to PNG under *out_dir*, and return the index."""
    wanted = read_image_names(game_dir / "Root.wad", types_path)
    textures = index_textures(game_dir)

    png_dir = out_dir / spells_dir().name
    if not dry_run:
        png_dir.mkdir(parents=True, exist_ok=True)

    images: dict[str, str] = {}
    written: set[str] = set()
    missing: dict[str, str] = {}
    failed: dict[str, str] = {}
    for template, texture in sorted(wanted.items()):
        key = PurePosixPath(texture.replace("\\", "/")).name
        hit = textures.get(key.lower())
        if hit is None:
            # The game names a texture its own files do not contain. A handful of
            # spells are like this; it costs those cards their art and nothing else.
            missing[template] = texture
            continue
        filename = f"{PurePosixPath(key).stem}.png"
        if filename not in written:
            if not dry_run:
                archive, entry = hit
                try:
                    Image.open(io.BytesIO(archive[entry])).convert("RGBA").save(png_dir / filename)
                except Exception as exc:  # noqa: BLE001 - one bad texture must not stop the run
                    failed[template] = f"{texture}: {exc}"
                    continue
            written.add(filename)
        images[template] = filename

    icons = _extract_icons(textures, out_dir, dry_run=dry_run, missing=missing, failed=failed)

    return {
        "_comment": "Generated by tools/extract_card_art.py from the local game install. Not for redistribution.",
        "generated": date.today().isoformat(),
        "source": str(game_dir),
        "images": images,
        "icons": icons,
        "files": len(written),
        "missing": missing,
        "failed": failed,
    }


def top_left_quadrant(image: Image.Image) -> Image.Image:
    """The top-left quarter of a 2x2 button-state sheet: the normal, undimmed state.

    The game packs four button states into one texture -- normal, two hover/pressed
    variants, and a desaturated one for an unavailable world -- so the whole image is four
    icons, not one. Cropping rather than resizing: the quadrant is already the 128px the
    GUI wants, so there is no resample and no softening.

    Public so a test can exercise the crop without a WAD.
    """
    return image.crop((0, 0, image.width // 2, image.height // 2))


def _extract_icons(
    textures: dict[str, tuple[Archive, str]],
    out_dir: Path,
    *,
    dry_run: bool,
    missing: dict[str, str],
    failed: dict[str, str],
) -> dict[str, str]:
    """Convert the school and archmastery icons, and return the key -> filename map.

    Written beside the spell art rather than into it: there are 13 of them against
    1,300 spell images, they are keyed by a logical name rather than a template, and
    the GUI asks for them one at a time by that name.
    """
    icon_dir = out_dir / ICONS_DIRNAME
    if not dry_run:
        icon_dir.mkdir(parents=True, exist_ok=True)

    icons: dict[str, str] = {}
    for key, texture in ICON_SOURCES.items():
        hit = textures.get(texture.lower())
        if hit is None:
            missing[key] = texture
            continue
        filename = f"{key}.png"
        if not dry_run:
            archive, entry = hit
            try:
                image = Image.open(io.BytesIO(archive[entry])).convert("RGBA")
                if key in STATE_SHEET_ICONS:
                    image = top_left_quadrant(image)
                image.save(icon_dir / filename)
            except Exception as exc:  # noqa: BLE001 - one bad texture must not stop the run
                failed[key] = f"{texture}: {exc}"
                continue
        icons[key] = filename
    return icons


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR, help="the game's Data/GameData directory")
    ap.add_argument("--types", type=Path, default=DEFAULT_TYPES, help="path to a wiztype types JSON dump")
    ap.add_argument("--out", type=Path, default=None, help="where to write the cache (default: the art cache)")
    ap.add_argument("--dry-run", action="store_true", help="report what would be written, write nothing")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out_dir = args.out or art_dir()
    if not (args.game_dir / "Root.wad").exists():
        print(f"No Root.wad under {args.game_dir}; pass --game-dir")
        return 1
    if not args.types.exists():
        print(f"types JSON not found at {args.types}; pass --types")
        return 1

    index = extract(args.game_dir, args.types, out_dir, dry_run=args.dry_run)

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / INDEX_FILENAME).write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")

    size = 0
    if not args.dry_run:
        for sub in (spells_dir().name, ICONS_DIRNAME):
            size += sum(f.stat().st_size for f in (out_dir / sub).glob("*.png"))
    print(f"{'would write' if args.dry_run else 'wrote'} {index['files']} images for {len(index['images'])} templates")
    print(f"  {len(index['icons'])} school and archmastery icons")
    if size:
        print(f"  {size / 1e6:.1f} MB in {out_dir}")
    if index["missing"]:
        print(f"  {len(index['missing'])} templates name a texture the game files do not contain")
    if index["failed"]:
        print(f"  {len(index['failed'])} textures could not be converted")
    if not args.dry_run:
        print(f"  index: {out_dir / INDEX_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
