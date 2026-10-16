"""Extract spell card art and the game's icons from the installed game into the local art cache.

Run from setup's Game art page, from Settings › General › Game art, and from the command
line by ``tools/extract_card_art.py``; all three call :func:`extract_art` (epic #215,
Stage 7). It reads only the user's own game install and writes only under
:func:`~bartlebot.game.card_art.art_dir`, ``%APPDATA%/Bartleby/BartleBot/art``.

WHY THE ART IS EXTRACTED, AND NEVER SHIPPED
    Nothing extracted may ever become a tracked file: the repo goes public at 1.0.0
    and a committed game texture carries the same exposure as committed player data
    (see "No game assets, either" in the root ``CLAUDE.md``). Writing into
    ``%APPDATA%/Bartleby/BartleBot/art`` puts the output somewhere the repo cannot
    reach even by accident. ``bartlebot/game/card_art.py`` is the read side, and treats
    absent art as the ordinary case: a user can decline it, and an extraction can fail.

WHERE THE ART COMES FROM
    Each spell under ``Spells/`` in ``Root.wad`` is a BINd blob whose ``m_imageName``
    names a texture by bare filename ("Spell_Storm_Tempest.dds"). Those textures live
    in ``GUI/SummonedImages/`` -- nearly all in ``_Shared-WorldData.wad``, a dozen in
    ``Root.wad`` -- so this resolves by basename across both rather than by path.
    Reading the *named* field needs a real deserializer and the game's type list, the
    newest ``tools/r<rev>_WizardDev.json`` (:func:`type_dump`): see the byte-scan
    failure described at the top of ``tools/dump_spell_names.py``.

    The textures are DXT3/DXT5 DDS, which Qt cannot read (its build has no dds
    plugin), so they are converted to PNG here, with Pillow. They are written
    **uncropped**, at their full 128x128 with the circular frame and its transparent
    corners intact: the display size and any crop are the UI's business and change more
    often than the cache should be rebuilt.

    One PNG per *image*, not per template: 18,000 templates share about 1,300
    images, and the index maps every template name onto the file it uses.

NOTHING IS LEFT HALF WRITTEN
    A run writes into a sibling folder, ``art.partial``, and only a run that finished
    swaps it in for ``art``. A failed or cancelled run removes its partial folder, so the
    art on disk is always the last whole run, or none. ``cancelled`` is asked between
    textures, which is how closing setup mid-run stops it.

RUN IT AGAIN when a game patch adds or reworks spells. Nothing here is incremental: the
whole run takes seconds, and replaces the art before it.
"""

from __future__ import annotations

import colorsys
import io
import json
import logging
import math
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from katsuba.op import STATEFUL_FLAGS, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive
from PIL import Image

from bartlebot.game import badge_categories, card_art
from bartlebot.game.card_art import (
    ARCHMASTERY_ICON,
    DUNGEON_ICON,
    ICONS_DIRNAME,
    INDEX_FILENAME,
    RECENT_ICON,
    SPELLS_DIRNAME,
    UNIVERSAL_ICON,
    art_dir,
    badge_icon_name,
    drop_icon_name,
    duel_slot_icon_name,
    pin_icon_name,
    school_icon_name,
    tag_icon_name,
    world_icon_name,
)
from bartlebot.game.spell_schools import SCHOOL_ORDER
from bartlebot.game.worlds import DUNGEON_WORLDS
from bartlebot.paths import project_root
from bartlebot.shared.game_install import find_install
from bartlebot.shared.preferences import PreferenceStore

logger = logging.getLogger(__name__)

#: Where the game keeps its archives, beneath the install folder ``find_install`` finds.
GAME_DATA = ("Data", "GameData")
#: The type list's file name in ``tools/``: ``r<revision>_WizardDev.json``.
TYPE_DUMP_PATTERN = re.compile(r"r(\d+)_WizardDev\.json")
#: The sibling folders a run writes into, and the one it moves the art before it aside to.
PARTIAL_SUFFIX = ".partial"
OLD_SUFFIX = ".old"

#: The steps a run reports, in order. Said to the user, so in their words.
STEP_READING = "Reading the spell list"
STEP_INDEXING = "Finding the game's pictures"
STEP_SPELLS = "Extracting spell card art"
STEP_ICONS = "Extracting icons"
STEP_SAVING = "Saving"
#: How many spells or textures pass between progress reports.
REPORT_EVERY = 50


@dataclass(frozen=True)
class Progress:
    """Where a run is: its step, and how far through it when the step has a count (*total* > 0)."""

    step: str
    done: int = 0
    total: int = 0


@dataclass(frozen=True)
class ExtractionResult:
    """What a run wrote, for the line that reports it."""

    out_dir: Path
    templates: int
    files: int
    icons: int
    missing: int
    failed: int
    size_bytes: int
    index: dict


class ExtractionError(Exception):
    """A run could not start or finish, with a reason the user can read."""


class ExtractionCancelled(Exception):
    """``cancelled`` said stop. Nothing the run wrote is left on disk."""


ProgressCallback = Callable[[Progress], None]


def game_data_dir(install: Path) -> Path:
    """The ``Data/GameData`` folder of a game install, where ``Root.wad`` is."""
    return Path(install).joinpath(*GAME_DATA)


def installed_game_data_dir(prefs: PreferenceStore | None = None) -> Path | None:
    """The game's ``Data/GameData``, from the folder ``find_install`` finds, or None when none is found.

    The same folder the app launches from (``docs/FIRST_RUN.md``, "The game folder"), so a
    Steam install is read as readily as the launcher's.
    """
    found = find_install(prefs if prefs is not None else PreferenceStore())
    return game_data_dir(found.path) if found is not None else None


def type_dump(root: Path | None = None) -> Path | None:
    """The newest ``tools/r<rev>_WizardDev.json`` in BartleBot's folder, or None when there is none.

    Type dumps are committed and never deleted (``tools/CLAUDE.md``), so the newest is
    the one nearest the game's own; a field the game later moved is skipped rather than
    misread (``skip_unknown_types``).
    """
    tools = (root if root is not None else project_root()) / "tools"
    dumps = [
        (int(match.group(1)), path)
        for path in tools.glob("r*_WizardDev.json")
        if (match := TYPE_DUMP_PATTERN.fullmatch(path.name))
    ]
    return max(dumps)[1] if dumps else None


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


def _never() -> bool:
    return False


def _quiet(_progress: Progress) -> None:
    pass


def _check(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise ExtractionCancelled


def read_image_names(
    root_wad: Path,
    types_path: Path,
    *,
    progress: ProgressCallback = _quiet,
    cancelled: Callable[[], bool] = _never,
) -> dict[str, str]:
    """Template name -> the texture filename it names, for every spell in Root.wad.

    The WAD path's stem *is* the template's internal name (see
    ``tools/dump_spell_names.py``), so the key needs no deserializing -- only
    ``m_imageName`` does.
    """
    archive = Archive.mmap(str(root_wad))
    ser = _build_serializer(types_path)
    paths = list(archive.iter_glob(SPELLS_GLOB))

    images: dict[str, str] = {}
    for done, path in enumerate(paths):
        if done % REPORT_EVERY == 0:
            _check(cancelled)
            progress(Progress(STEP_READING, done, len(paths)))
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


def extract(
    game_dir: Path,
    types_path: Path,
    out_dir: Path,
    *,
    dry_run: bool = False,
    progress: ProgressCallback = _quiet,
    cancelled: Callable[[], bool] = _never,
) -> dict:
    """Convert every spell texture to PNG under *out_dir*, and return the index.

    Writes straight into *out_dir*: :func:`extract_art` points it at the partial folder.
    """
    wanted = read_image_names(game_dir / "Root.wad", types_path, progress=progress, cancelled=cancelled)
    _check(cancelled)
    progress(Progress(STEP_INDEXING))
    textures = index_textures(game_dir)

    png_dir = out_dir / SPELLS_DIRNAME
    if not dry_run:
        png_dir.mkdir(parents=True, exist_ok=True)

    images: dict[str, str] = {}
    written: set[str] = set()
    missing: dict[str, str] = {}
    failed: dict[str, str] = {}
    ordered = sorted(wanted.items())
    for done, (template, texture) in enumerate(ordered):
        if done % REPORT_EVERY == 0:
            _check(cancelled)
            progress(Progress(STEP_SPELLS, done, len(ordered)))
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

    _check(cancelled)
    progress(Progress(STEP_ICONS))
    icons = _extract_icons(textures, out_dir, dry_run=dry_run, missing=missing, failed=failed, cancelled=cancelled)

    return {
        "_comment": "Extracted by BartleBot from the local game install. Not for redistribution.",
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
    cancelled: Callable[[], bool] = _never,
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
        _check(cancelled)
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


def _remove(folder: Path) -> None:
    if folder.exists():
        shutil.rmtree(folder)


def _swap_in(new: Path, target: Path) -> None:
    """Make *new* the folder at *target*, moving the one there aside first and dropping it after.

    Should the last move fail, the old folder goes back, so the art before the run stays.
    """
    old = target.with_name(target.name + OLD_SUFFIX)
    _remove(old)
    if target.exists():
        target.rename(old)
    try:
        new.rename(target)
    except OSError:
        if old.exists() and not target.exists():
            old.rename(target)
        raise
    _remove(old)


def _size(folder: Path) -> int:
    return sum(f.stat().st_size for sub in (SPELLS_DIRNAME, ICONS_DIRNAME) for f in (folder / sub).glob("*.png"))


def extract_art(
    game_dir: Path,
    progress: ProgressCallback | None = None,
    *,
    types_path: Path | None = None,
    out_dir: Path | None = None,
    dry_run: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> ExtractionResult:
    """Extract the art from the game's ``Data/GameData`` folder *game_dir* into *out_dir* (the art cache).

    *progress* hears each step as it starts, and how far a counted step has got; it is
    called on the thread running this, so a GUI caller hands it to its own thread.
    *cancelled* is asked between textures, and a true answer raises
    :class:`ExtractionCancelled`. Either that or a failure removes everything the run
    wrote, and leaves the art before it as it was. A run that cannot start says why with
    :class:`ExtractionError`.

    With *dry_run*, nothing is written and the result counts what would be.
    """
    game_dir = Path(game_dir)
    out_dir = Path(out_dir) if out_dir is not None else art_dir()
    report = progress if progress is not None else _quiet
    stop = cancelled if cancelled is not None else _never
    if not (game_dir / "Root.wad").is_file():
        raise ExtractionError(f"There is no Root.wad in {game_dir}, so the game's files could not be read.")
    types_path = Path(types_path) if types_path is not None else type_dump()
    if types_path is None or not types_path.is_file():
        raise ExtractionError(
            "The game's type list, tools/r<revision>_WizardDev.json, is missing from BartleBot's folder."
        )

    if dry_run:
        index = extract(game_dir, types_path, out_dir, dry_run=True, progress=report, cancelled=stop)
        return _result(out_dir, index, 0)

    staging = out_dir.with_name(out_dir.name + PARTIAL_SUFFIX)
    _remove(staging)
    try:
        index = extract(game_dir, types_path, staging, progress=report, cancelled=stop)
        report(Progress(STEP_SAVING))
        (staging / INDEX_FILENAME).write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
        _check(stop)
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        _swap_in(staging, out_dir)
    except BaseException:
        _remove(staging)
        raise
    card_art.forget_cache()
    return _result(out_dir, index, _size(out_dir))


def _result(out_dir: Path, index: dict, size: int) -> ExtractionResult:
    return ExtractionResult(
        out_dir=out_dir,
        templates=len(index["images"]),
        files=index["files"],
        icons=len(index["icons"]),
        missing=len(index["missing"]),
        failed=len(index["failed"]),
        size_bytes=size,
        index=index,
    )
