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
    for it. ``bartlebot/game/card_art.py`` is the read side, and treats absent art as the
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
import colorsys
import io
import json
import logging
import math
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath

from katsuba.op import STATEFUL_FLAGS, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartlebot.game import badge_categories  # noqa: E402
from bartlebot.game.card_art import (  # noqa: E402
    ARCHMASTERY_ICON,
    DUNGEON_ICON,
    ICONS_DIRNAME,
    INDEX_FILENAME,
    RECENT_ICON,
    UNIVERSAL_ICON,
    art_dir,
    badge_icon_name,
    drop_icon_name,
    duel_slot_icon_name,
    pin_icon_name,
    school_icon_name,
    spells_dir,
    tag_icon_name,
    world_icon_name,
)
from bartlebot.game.spell_schools import SCHOOL_ORDER  # noqa: E402
from bartlebot.game.worlds import DUNGEON_WORLDS  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_GAME_DIR = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData")
DEFAULT_TYPES = Path(__file__).resolve().parent / "r800294_WizardDev.json"

SPELLS_GLOB = "Spells/**/*.xml"
#: Searched in order; the first WAD holding a texture of that name wins. _Shared first
#: because that is where all but a dozen of them are.
TEXTURE_WADS = ("_Shared-WorldData.wad", "Root.wad")
#: Where the spell textures sit inside those WADs.
TEXTURE_GLOB = "GUI/**"

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

#: Icon key -> the game path of its texture, for every badge category the game lists.
#:
#: Read from the checked-in snapshot rather than written out, because the game's own table
#: already pairs each category with its icon and several pairings cannot be guessed: Five
#: B.O.X.E.S. draws ``Badge_Category_Maestro_Events`` and Special Event draws
#: ``Badge_Category_Deckathalon``. Every category is extracted, not only the ones the bot
#: library has a folder for, so a folder added later needs no change here.
#:
#: A full path, not a basename: ``index_textures`` holds both, and the path is exact where
#: a basename is first-archive-wins.
BADGE_ICON_SOURCES: dict[str, str] = {
    badge_icon_name(key): texture for key in badge_categories.keys() if (texture := badge_categories.icon_texture(key))
}

#: Badge categories whose texture is one 128px image rather than a 2x2 state sheet.
#: Monstrology's is a button drawn once, and Unfiltered's is the help button; cropping
#: either keeps a quarter of the picture.
SINGLE_IMAGE_BADGES: frozenset[str] = frozenset({"MonsterMagic", "Unfiltered"})

#: Bot tag -> the full game path of its medallion, for every tag drawn from one texture.
#:
#: Hand-picked, one by one, from the game's gold-ringed medallions so every tag icon is
#: the same shape: nothing in the game pairs these with our vocabulary, so there is no
#: table to read them from. Full paths, because several basenames recur across folders.
#: ``Button_Backpack`` and ``Art_Quest_Max_Gold`` exist only at 64px; the rest are 128px.
#: Every tag in the vocabulary has a source here or in ``TAG_COMPOSITES``.
TAG_TEXTURES: dict[str, str] = {
    # Rewards
    "Gear": "GUI/Buttons/Button_Backpack.dds",
    "Jewels": "GUI/QuestPage/Art_Quest_Jewel.dds",
    "Reagents": "GUI/QuestPage/Art_Quest_Reagent.dds",
    "Spellements": "GUI/Buttons/Button_BG_Reward_Spellement02.dds",
    "Animus": "GUI/Buttons/Button_Monster_Magic.dds",  # the Monstrology tab on the deck page
    "Treasure Cards": "GUI/QuestPage/Art_Quest_Treasure_Card.dds",
    "Snacks": "GUI/QuestPage/Art_Quest_PetSnack.dds",
    "Seeds": "GUI/QuestPage/Art_Quest_Seed.dds",
    "Mounts": "GUI/QuestPage/Art_Quest_Mount.dds",
    "Pets": "GUI/QuestPage/Art_Quest_Pet.dds",
    "Housing": "GUI/QuestPage/Art_Quest_Housing.dds",
    "Gold": "GUI/QuestPage/Art_Quest_Max_Gold.dds",
    # Activities
    "Boss": "GUI/NpcPortraits/Art_Portrait_Wraith.dds",
    "Mobs": "GUI/NpcPortraits/Art_Portrait_Ghost_Blue.dds",
    "Dungeon": "GUI/QuestButtons/Use_Sigil_4_Player.dds",
    "Team-Up": "GUI/QuestButtons/Quest_WC_TeamUpKiosk.dds",
    "Collecting": "GUI/Icons/Icon_Harvest.dds",
    "Training Points": "GUI/QuestButtons/Quest_Training_Point.dds",
}

#: Duel slot -> the full game path of the gem the game marks that position with.
#:
#: A duel puts four wizards around a circle and turns go clockwise, which puts a team's
#: own players right to left: slot 1 is the rightmost. The game's "Set Sigil Slot
#: Preference" dialog lists them that way, and marks each with one of these gold diamonds
#: -- Sun, Eye, Star, Moon, first to last. ``Art_Target_`` because the same four mark a
#: target in the duel itself.
#:
#: Full paths, and worth keeping full: ``Art_Target_Sun`` is the one of the four that
#: lives in ``Root.wad`` rather than ``_Shared-WorldData.wad``, so a probe listing only
#: one archive concludes there are three gems. They are single 64px images, not state
#: sheets.
#:
#: Sun, Star and Moon are also real schools. These are *not* those medallions -- see
#: :func:`~bartlebot.game.card_art.duel_slot_icon_name` for why the slot number is the key.
DUEL_SLOT_TEXTURES: dict[int, str] = {
    1: "GUI/Art/Art_Target_Sun.dds",
    2: "GUI/Art/Art_Target_Eye.dds",
    3: "GUI/Art/Art_Target_Star.dds",
    4: "GUI/Art/Art_Target_Moon.dds",
}

DUEL_SLOT_ICON_SOURCES: dict[str, str] = {
    duel_slot_icon_name(slot): texture for slot, texture in DUEL_SLOT_TEXTURES.items()
}

#: The four-player sigil the Dungeon tag draws, which Challenge Mode builds on.
SIGIL_TEXTURE = "GUI/QuestButtons/Use_Sigil_4_Player.dds"
#: The red skull the game's own Challenge Mode button stamps on that sigil (32px).
CHALLENGE_SKULL_TEXTURE = "GUI/Icons/Icon_Advanced_Mode.dds"
#: A bare gold ring, transparent inside, that the Gauntlet medallion is framed in. Root.wad.
RING_FRAME_TEXTURE = "GUI/Art/Art_Character_Cover.dds"
#: The Housing Gauntlet badge (a 2x2 state sheet) the Gauntlet medallion puts in that ring.
GAUNTLET_BADGE_TEXTURE = "GUI/BadgeCategory/Badge_Category_Housing_Gauntlets.dds"

#: Where the skull sits on the sigil, as fractions of the medallion's width: its size, and
#: its center's offset from the medallion's center (negative is left and up). No ``.gui``
#: file references either texture, so these were matched by eye against the game's button,
#: keeping the skull clear of the gold ring.
SKULL_SIZE = 0.378
SKULL_OFFSET = (-0.05, -0.13)

#: The Gauntlet medallion's disc, which is green because Dungeon, Challenge Mode, Team-Up
#: and Gauntlet are all entered through a four-player sigil. It is an orange radial
#: gradient recolored to the sigil's hue rather than a green one painted directly: the
#: game's medallions are shaded that way, and a gradient chosen in green read pale.
DISC_CENTER = (255, 190, 70)
DISC_EDGE = (170, 70, 10)
DISC_CURVE = 1.4
DISC_HUE = colorsys.rgb_to_hls(41 / 255, 243 / 255, 33 / 255)[0]  # the sigil's green
DISC_LIGHTNESS = (1.15, 1.15 * 0.75)  # center, and where the band starts
#: A solid near-black band between the disc and the gold. Solid, not a fade to black: a
#: fade reads as a second, dark-green ring inside the gold one.
BAND_PX = 2
BAND_BLEND_PX = 1.5
BAND_COLOR = (6, 20, 6)
#: The disc overlaps the ring's inner edge by this much, so no gap shows under the gold.
DISC_OVERLAP_PX = 1.5
#: How much of the disc's diameter the gauntlet badge fills, and how far down it sits.
GAUNTLET_FILL = 0.74
GAUNTLET_DROP = 0.01  # of the frame's width


def ring_inner_radius(frame: Image.Image) -> float:
    """The radius of *frame*'s transparent center, read along its middle row.

    The first fully transparent pixel after the opaque gold, walking in from the left.
    """
    half = frame.width // 2
    seen_ring = False
    for x in range(half):
        alpha = frame.getpixel((x, frame.height // 2))[3]
        if alpha > 200:
            seen_ring = True
        elif seen_ring and alpha == 0:
            return frame.width / 2 - x
    raise ValueError("the ring frame has no transparent center")


def challenge_icon(sigil: Image.Image, skull: Image.Image) -> Image.Image:
    """The Dungeon sigil with the Challenge Mode skull on it, as the game's own button draws it."""
    icon = sigil.copy()
    left, top, right, bottom = icon.getbbox()
    width = right - left
    cx, cy = (left + right) / 2, (top + bottom) / 2
    side = round(width * SKULL_SIZE)
    stamp = skull.resize((side, side), Image.LANCZOS)
    x = round(cx + SKULL_OFFSET[0] * width - stamp.width / 2)
    y = round(cy + SKULL_OFFSET[1] * width - stamp.height / 2)
    icon.alpha_composite(stamp, (x, y))
    return icon


def gauntlet_disc(size: int, inner: float) -> Image.Image:
    """The Gauntlet medallion's green disc, *size* px square, reaching just past *inner*."""
    c = (size - 1) / 2
    outer = inner + DISC_OVERLAP_PX
    end = outer - BAND_PX  # the gradient and the lightness ramp both finish where the band starts
    start_l, end_l = DISC_LIGHTNESS
    band_start = end - BAND_BLEND_PX
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = disc.load()
    for y in range(size):
        for x in range(size):
            r = math.hypot(x - c, y - c)
            if r > outer:
                continue
            reach = min(1.0, r / end)
            t = reach**DISC_CURVE
            red, green, blue = (a + (b - a) * t for a, b in zip(DISC_CENTER, DISC_EDGE))
            red, green, blue = (round(v) for v in (red, green, blue))
            _, light, sat = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
            light = min(1.0, light * (start_l + (end_l - start_l) * reach))
            rgb = [round(v * 255) for v in colorsys.hls_to_rgb(DISC_HUE, light, sat)]
            if r > band_start:
                k = min(1.0, (r - band_start) / BAND_BLEND_PX)
                rgb = [round(v + (b - v) * k) for v, b in zip(rgb, BAND_COLOR)]
            px[x, y] = (*rgb, 255)
    return disc


def gauntlet_icon(frame: Image.Image, badge_sheet: Image.Image) -> Image.Image:
    """The Housing Gauntlet badge on a green disc, inside the game's plain gold ring."""
    size = frame.width
    inner = ring_inner_radius(frame)
    icon = gauntlet_disc(size, inner)
    badge = top_left_quadrant(badge_sheet)
    badge = badge.crop(badge.getbbox())
    box = round(2 * inner * GAUNTLET_FILL)
    badge.thumbnail((box, box), Image.LANCZOS)
    icon.alpha_composite(badge, ((size - badge.width) // 2, (size - badge.height) // 2 + round(size * GAUNTLET_DROP)))
    icon.alpha_composite(frame)
    return icon


#: Tag icons assembled from several textures: tag -> (the textures, in the order the
#: builder takes them, and the builder).
TAG_COMPOSITES: dict[str, tuple[tuple[str, ...], Callable[..., Image.Image]]] = {
    "Challenge Mode": ((SIGIL_TEXTURE, CHALLENGE_SKULL_TEXTURE), challenge_icon),
    "Gauntlet": ((RING_FRAME_TEXTURE, GAUNTLET_BADGE_TEXTURE), gauntlet_icon),
}

TAG_ICON_SOURCES: dict[str, str] = {tag_icon_name(tag): texture for tag, texture in TAG_TEXTURES.items()}

#: Drop category -> the full game path of its icon, for every category the wiki import
#: produces (``quicksell``'s two category sets are that vocabulary).
#:
#: Nearly all are the quest page's reward medallions, 128px single images, and which one
#: goes with which category is the game's own pairing rather than a guess: the outfit slots
#: draw ``Hat``/``Robe``/``Shoes``/``Weapon``, housing items draw ``Housing`` (pixel-for-pixel
#: ``Furniture``), battleground reward tables draw ``Seed`` and ``Treasure_Card``.
#: ``Weapon`` rather than ``Wand``, which is the same wand at 64px. The quest page has no
#: spellement, so Spellements takes the reward-category one the Spellements tag uses.
#:
#: Pins has an icon per kind (``PIN_TEXTURES``); its entry here is only for a pin whose
#: kind is not recognized, and is the Jewels medallion because quicksell sells pins as
#: jewels.
DROP_TEXTURES: dict[str, str] = {
    "Hats": "GUI/QuestPage/Art_Quest_Hat.dds",
    "Robes": "GUI/QuestPage/Art_Quest_Robe.dds",
    "Boots": "GUI/QuestPage/Art_Quest_Shoes.dds",
    "Wands": "GUI/QuestPage/Art_Quest_Weapon.dds",
    "Athames": "GUI/QuestPage/Art_Quest_Athame.dds",
    "Amulets": "GUI/QuestPage/Art_Quest_Amulet.dds",
    "Rings": "GUI/QuestPage/Art_Quest_Ring.dds",
    "Decks": "GUI/QuestPage/Art_Quest_Deck.dds",
    "Pets": "GUI/QuestPage/Art_Quest_Pet.dds",
    "Mounts": "GUI/QuestPage/Art_Quest_Mount.dds",
    "Seeds": "GUI/QuestPage/Art_Quest_Seed.dds",
    "Snacks": "GUI/QuestPage/Art_Quest_PetSnack.dds",
    "Reagents": "GUI/QuestPage/Art_Quest_Reagent.dds",
    "Jewels": "GUI/QuestPage/Art_Quest_Jewel.dds",
    "Pins": "GUI/QuestPage/Art_Quest_Jewel.dds",
    "Housing Items": "GUI/QuestPage/Art_Quest_Housing.dds",
    "Treasure Cards": "GUI/QuestPage/Art_Quest_Treasure_Card.dds",
    "Spellements": "GUI/Buttons/Button_BG_Reward_Spellement02.dds",
}

#: Pin kind -> the bank and quicksell filter button for it. 2x2 state sheets of 64px
#: buttons, the one drop icon set that is neither 128px nor a single image.
PIN_TEXTURES: dict[str, str] = {
    "power": "GUI/Buttons/Button_Socketing_Pin_Square_Pip.dds",
    "shield": "GUI/Buttons/Button_Socketing_Pin_Square_Shield.dds",
    "sword": "GUI/Buttons/Button_Socketing_Pin_Square_Sword.dds",
}

PIN_ICON_SOURCES: dict[str, str] = {pin_icon_name(kind): texture for kind, texture in PIN_TEXTURES.items()}

DROP_ICON_SOURCES: dict[str, str] = {
    **{drop_icon_name(category): texture for category, texture in DROP_TEXTURES.items()},
    **PIN_ICON_SOURCES,
}

#: Icon key -> the texture basename it comes from, resolved through the same
#: basename index the spell art uses.
#:
#: ``Art_School_`` is the ringed medallion the game's own archmastery picker draws, and
#: it covers all eleven schools -- seven in ``Root.wad``, and Moon, Star, Sun and Shadow
#: in ``_Shared-WorldData.wad``. ``Type_Global`` is the game's universal mark, stamped
#: on card art that belongs to no school; it exists at 32x32 and nowhere larger, which
#: is the ceiling on how big the GUI may draw these. ``Art_Selfie_Timer`` is the camera's
#: self-timer button, a pocket watch in the same gold ring as the badges, and a single
#: 128px image rather than a state sheet.
ICON_SOURCES: dict[str, str] = {
    ARCHMASTERY_ICON: "Icon_Archmastery.dds",
    UNIVERSAL_ICON: "Type_Global.dds",
    RECENT_ICON: "GUI/Buttons/Art_Selfie_Timer.dds",
    **{school_icon_name(school): f"Art_School_{school}.dds" for school in SCHOOL_ORDER},
    **WORLD_ICON_SOURCES,
    **BADGE_ICON_SOURCES,
    **TAG_ICON_SOURCES,
    **DROP_ICON_SOURCES,
    **DUEL_SLOT_ICON_SOURCES,
}

#: Icon keys whose texture is a 2x2 sheet of button states rather than a single image.
#:
#: The world buttons and nearly all the badge categories are 256x256 sheets holding four
#: 128px quadrants: normal, two hover/pressed variants, and a desaturated one for a world
#: the player cannot reach. Only the top-left is wanted. Drawn whole, these would render at
#: quarter size with three ghosts around them -- which is exactly what a "why is the icon
#: tiny" bug looks like from the outside. ``SINGLE_IMAGE_BADGES`` are the exceptions.
STATE_SHEET_ICONS: frozenset[str] = (
    frozenset(WORLD_ICON_SOURCES)
    | frozenset(key for key in BADGE_ICON_SOURCES if key not in {badge_icon_name(b) for b in SINGLE_IMAGE_BADGES})
    | frozenset(PIN_ICON_SOURCES)
)


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
    """Lowercased texture basename *and* full path -> the archive holding it and its path inside.

    By basename because ``m_imageName`` carries no directory, and across both WADs
    because the spell textures are split between them. By full path as well, for the
    badge icons, whose table names the exact file; a basename never contains a ``/`` and a
    path always does, so the two kinds of key cannot collide.
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
            found.setdefault(str(entry).lower(), (archive, entry))
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
    """Convert the UI icons, build the composite tag icons, and return the key -> filename map.

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

    for tag, (textures_needed, build) in TAG_COMPOSITES.items():
        key = tag_icon_name(tag)
        hits = [textures.get(texture.lower()) for texture in textures_needed]
        absent = [texture for texture, hit in zip(textures_needed, hits) if hit is None]
        if absent:
            missing[key] = ", ".join(absent)
            continue
        filename = f"{key}.png"
        if not dry_run:
            try:
                parts = [Image.open(io.BytesIO(archive[entry])).convert("RGBA") for archive, entry in hits]
                build(*parts).save(icon_dir / filename)
            except Exception as exc:  # noqa: BLE001 - one bad texture must not stop the run
                failed[key] = f"{tag}: {exc}"
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
    print(f"  {len(index['icons'])} school, archmastery, world, badge, tag, drop and duel-slot icons")
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
