"""Where extracted card art lives, and how a template name finds its image.

The game ships every spell's art as a DDS texture inside its own WADs. Nothing in
this repo may carry one: the repo goes public at 1.0.0 and a committed game texture
is the same exposure a committed wizard name is (see "No real player data" in the
root ``CLAUDE.md``). So the art is *extracted on the user's own machine*, by
:mod:`bartlebot.game.art_extraction` (from setup, Settings › General, or
``tools/extract_card_art.py``), into the machine-local product directory -- the same
place ``settings_manager`` already writes ``autosave/``, and gitignored by
construction because it is not in the tree at all.

This module is the read side, and it is deliberately the *only* thing that knows the
cache's shape. It is Qt-free so the lookup can be tested without a ``QApplication``;
turning a path into a pixmap is ``gui/widgets/cards/card_art_tile.py``'s job.

THE LOOKUP KEY IS THE TEMPLATE NAME
    ``deck_io`` keys a saved deck by ``SpellTemplate.name()`` -- the internal name,
    "Storm Lord - T05 - A" and not "Storm Lord" -- and the extractor reads the art
    filename off that same template's ``m_imageName``. So a deck entry finds its art
    by exact dict lookup, with no normalization and no tier parsing. Do not look art
    up by display name: one display name can cover many templates.

EVERY LOOKUP MAY RETURN NONE, AND THAT IS THE ORDINARY CASE
    Art is optional: a user can turn it off in Custom setup, an extraction can fail,
    and an install from before setup asked has none until the user extracts it. So
    ``art_path`` returning None is not an error and must never be logged as one. Individual cards miss too: a handful of
    templates name an image the game's own files do not contain, and the snapshot can
    be older than the game. Callers render their text form instead.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

from bartlebot.game.wiki_drops import PIN_CATEGORY, pin_kind
from bartlebot.shared.vendor import product_dir

logger = logging.getLogger(__name__)

#: The cache directory's name beneath the product directory.
ART_DIRNAME = "art"

#: Where the extractor writes the per-spell PNGs, relative to :func:`art_dir`.
SPELLS_DIRNAME = "spells"

#: Where the extractor writes the school and archmastery icons.
ICONS_DIRNAME = "icons"

#: Logical names for the two icons that are not a school, so that neither the
#: extractor nor the GUI spells a game texture filename.
ARCHMASTERY_ICON = "archmastery"
UNIVERSAL_ICON = "universal"
#: The pocket watch the bot library's Recent row wears.
RECENT_ICON = "recent"

#: Maps template name -> PNG filename. Written by the extractor, read here.
INDEX_FILENAME = "index.json"


def art_dir() -> Path:
    """``%APPDATA%/Bartleby/BartleBot/art`` -- the extracted art cache.

    May not exist: art is optional, and an extraction can be declined or fail.
    """
    return product_dir() / ART_DIRNAME


def index_path() -> Path:
    return art_dir() / INDEX_FILENAME


def spells_dir() -> Path:
    return art_dir() / SPELLS_DIRNAME


def icons_dir() -> Path:
    return art_dir() / ICONS_DIRNAME


#: Logical name for the generic dungeon badge. Not a world, but it is keyed and stored
#: beside them, because the surfaces that ask for a world's emblem are the ones that need
#: a stand-in when a "world" turns out to be a dungeon.
DUNGEON_ICON = "dungeon"


def world_icon_name(world: str) -> str:
    """The icon key for a world, from the key the nav map uses ("MooShu" -> "world_mooshu").

    Case-folded for the same reason :func:`school_icon_name` is: the caller's key comes
    from ``zone_nav``'s own world table and the extractor's from a hand-written source
    map, and keying on exact casing would make the two agree only by luck -- which is a
    live risk here, since the game spells this world three different ways across its own
    files (``DragonSpire`` / ``Dragonspire`` / ``Dragonspyre``).
    """
    return f"world_{world.casefold()}"


def badge_icon_name(key: str) -> str:
    """The icon key for a badge category, from the game's filter name ("4Dungeons" -> "badge_4dungeons").

    A namespace of its own rather than a reuse of ``world_<key>``: the badge page and the
    nav map spell the same world differently (``Dragonspyre`` against ``DragonSpire``), and
    the two sets are extracted from different tables. Case-folded for the reason
    :func:`world_icon_name` is.
    """
    return f"badge_{key.casefold()}"


def tag_icon_name(tag: str) -> str:
    """The icon key for a bot tag, from its vocabulary name ("Team-Up" -> "tag_team_up").

    Spaces and hyphens become underscores so the key is also a tidy filename; case-folded
    for the reason :func:`school_icon_name` is. The tag's text is the key rather than a
    second table of ids, because ``bot_tags.BOT_TAGS`` is already the one list of them.
    """
    return "tag_" + _slug(tag)


def _slug(text: str) -> str:
    return text.casefold().replace(" ", "_").replace("-", "_")


def drop_icon_name(category: str, item_name: str = "") -> str:
    """The icon key for a drop, from its category and, for a pin, its name.

    ``("Treasure Cards", ...)`` -> ``"drop_treasure_cards"``. A pin is the one category
    whose icon depends on the item: ``("Pins", "Fire Blocking Pin (150)")`` ->
    ``"pin_shield"``, the game's own filter button for that kind. A pin whose kind is not
    recognized keeps ``"drop_pins"``, which the extractor points at the Jewels medallion
    -- the fallback is its table's to state, as the dungeon stand-in for worlds is.
    """
    if category == PIN_CATEGORY and (kind := pin_kind(item_name)):
        return pin_icon_name(kind)
    return "drop_" + _slug(category)


def pin_icon_name(kind: str) -> str:
    """The icon key for a pin kind ("shield" -> "pin_shield")."""
    return f"pin_{kind}"


#: How many players a duel has slots for, and so how many slot gems there are.
DUEL_SLOTS = 4

#: Duel slot -> the symbol the game marks it with, first position to last.
#:
#: The game's own vocabulary, and the only name a user has for these: the "Set Sigil Slot
#: Preference" dialog draws the gem and nothing else, so a tooltip saying "Sun" is what
#: connects this app's slot 1 to the diamond they picked in-game. Here rather than in the
#: extractor because it is read at run time, for tooltips, on machines that never ran one;
#: ``test_duel_slot_icons`` checks it against the extractor's texture table.
DUEL_SLOT_SYMBOLS: dict[int, str] = {1: "Sun", 2: "Eye", 3: "Star", 4: "Moon"}


def duel_slot_symbol(slot: int) -> str:
    """The symbol name for a duel slot ("Sun"), or "" for a slot the game does not have."""
    return DUEL_SLOT_SYMBOLS.get(slot, "")


def duel_slot_icon_name(slot: int) -> str:
    """The icon key for a duel slot's gem (1 -> "duel_slot_1").

    Keyed by the slot *number* rather than by the symbol on the gem, because the number
    is what every caller has: a combat order is a list of positions. Which symbol goes
    with which position is the extractor's table to state (``DUEL_SLOT_TEXTURES``), the
    same way ``WORLD_ICON_SOURCES`` owns the world -> emblem pairing.

    That indirection is load-bearing here and not merely tidy. Three of the four gems are
    named Sun, Star and Moon -- which are also three real schools, with their own
    ``school_`` medallions that look nothing like these. A caller that reached for
    ``school_sun`` to draw slot 1 would get the archmastery picker's ringed medallion
    instead of the gold diamond the duel screen draws, and it would look plausible.
    """
    return f"duel_slot_{slot}"


def duel_slot_icon_path(slot: int) -> Path | None:
    """The PNG for a duel slot's gem, or None if there is not one on this machine."""
    return icon_path(duel_slot_icon_name(slot))


def school_icon_name(school: str) -> str:
    """The icon key for a school, from the name a deck stores ("Fire" -> "school_fire").

    Case-folded because the deck's value comes from wizwalker's school table and the
    extractor's from :data:`~bartlebot.game.spell_schools.SCHOOL_ORDER`; keying on the exact
    casing would make the two agree only by luck.
    """
    return f"school_{school.casefold()}"


@cache
def _manifest() -> dict:
    """The whole index file, or empty when no art has been extracted.

    Cached because it is read once per card per redraw and the file only changes when
    the extractor runs, which ends with :func:`forget_cache` to make the new file
    visible. A missing file is the common case and says
    nothing; an unreadable one is worth a line in the log, since the user did run the
    extractor and is entitled to know why they see no art.

    One read for both maps: the spell images and the icons are written by the same run
    and live in the same file, so reading it twice would only be two chances to
    disagree about what is in it.
    """
    path = index_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read extracted card art index at %s", path)
        return {}
    return raw if isinstance(raw, dict) else {}


def _section(key: str) -> dict[str, str]:
    """One name -> filename map out of the manifest, or empty if it is not usable."""
    value = _manifest().get(key)
    return value if isinstance(value, dict) else {}


def _index() -> dict[str, str]:
    """Template name -> PNG filename."""
    return _section("images")


def _icons() -> dict[str, str]:
    """Icon key -> PNG filename. Empty for a cache written before icons were extracted."""
    return _section("icons")


_generation = 0


def cache_generation() -> int:
    """A number that changes whenever :func:`forget_cache` runs.

    For a cache built on these lookups in a layer this module cannot import: keying on
    the generation makes :func:`forget_cache` reach it too. ``tag_picker.tag_icon`` is
    the case -- the library builds a pill per tag per card, and each lookup here stats
    the file.
    """
    return _generation


def forget_cache() -> None:
    """Drop the cached index, so the next lookup re-reads it from disk.

    For the extractor finishing inside a running app, and for tests pointing the
    product directory somewhere else.
    """
    global _generation
    _generation += 1
    _manifest.cache_clear()


def is_available() -> bool:
    """Whether any card art has been extracted on this machine."""
    return bool(_index())


def art_path(template_name: str) -> Path | None:
    """The PNG for *template_name*, or None if there is not one on this machine.

    None covers every "no art" case on purpose -- nothing extracted, this card not in
    the index, or an index entry whose file has since been deleted -- because every
    caller does the same thing about all three.
    """
    filename = _index().get(template_name)
    if not filename:
        return None
    path = spells_dir() / filename
    return path if path.exists() else None


def icon_path(name: str) -> Path | None:
    """The PNG for an icon key, or None if there is not one on this machine.

    Same three "no icon" cases as :func:`art_path`, and the same answer to all three.
    An art cache extracted before icons were added has no ``icons`` map at all, which
    lands here as None per key rather than as an error -- the surfaces using icons all
    keep the text form they replaced.
    """
    filename = _icons().get(name)
    if not filename:
        return None
    path = icons_dir() / filename
    return path if path.exists() else None


def world_icon_path(world: str) -> Path | None:
    """The PNG for a world's emblem, or None if there is not one on this machine.

    No fallback to the dungeon badge here, and deliberately: which "worlds" are really
    dungeons is the extractor's table to state (``WORLD_ICON_SOURCES``), which points
    those keys at the dungeon texture so they resolve like any other. A fallback in the
    read side would be a second place that decides the same thing, and the two would
    drift the first time a dungeon group was added.
    """
    return icon_path(world_icon_name(world))


def badge_icon_path(key: str) -> Path | None:
    """The PNG for a badge category's icon, or None if there is not one on this machine."""
    return icon_path(badge_icon_name(key))


def tag_icon_path(tag: str) -> Path | None:
    """The PNG for a bot tag's medallion, or None if there is not one on this machine.

    Every tag in the vocabulary has a source, so None here means the extractor has not
    been run — the ordinary case. The caller keeps its text pill for that.
    """
    return icon_path(tag_icon_name(tag))


def drop_icon_path(category: str, item_name: str = "") -> Path | None:
    """The PNG for a drop's category icon, or None if there is not one on this machine."""
    return icon_path(drop_icon_name(category, item_name))
