"""Where extracted card art lives, and how a template name finds its image.

The game ships every spell's art as a DDS texture inside its own WADs. Nothing in
this repo may carry one: the repo goes public at 1.0.0 and a committed game texture
is the same exposure a committed wizard name is (see "No real player data" in the
root ``CLAUDE.md``). So the art is *extracted by the user, on their own machine*, by
``tools/extract_card_art.py``, into the machine-local product directory -- the same
place ``settings_manager`` already writes ``autosave/``, and gitignored by
construction because it is not in the tree at all.

This module is the read side, and it is deliberately the *only* thing that knows the
cache's shape. It is Qt-free so the lookup can be tested without a ``QApplication``;
turning a path into a pixmap is ``gui/widgets/card_art_tile.py``'s job.

THE LOOKUP KEY IS THE TEMPLATE NAME
    ``deck_io`` keys a saved deck by ``SpellTemplate.name()`` -- the internal name,
    "Storm Lord - T05 - A" and not "Storm Lord" -- and the extractor reads the art
    filename off that same template's ``m_imageName``. So a deck entry finds its art
    by exact dict lookup, with no normalization and no tier parsing. Do not look art
    up by display name: one display name can cover many templates.

EVERY LOOKUP MAY RETURN NONE, AND THAT IS THE ORDINARY CASE
    Most users will never run the extractor, so ``art_path`` returning None is not an
    error and must never be logged as one. Individual cards miss too: a handful of
    templates name an image the game's own files do not contain, and the snapshot can
    be older than the game. Callers render their text form instead.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

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

#: Maps template name -> PNG filename. Written by the extractor, read here.
INDEX_FILENAME = "index.json"


def art_dir() -> Path:
    """``%APPDATA%/Bartleby/BartleBot/art`` -- the extracted art cache.

    May not exist: the extractor is run by hand, and most installs never run it.
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
    return "tag_" + tag.casefold().replace(" ", "_").replace("-", "_")


def school_icon_name(school: str) -> str:
    """The icon key for a school, from the name a deck stores ("Fire" -> "school_fire").

    Case-folded because the deck's value comes from wizwalker's school table and the
    extractor's from :data:`~bartlebot.spell_schools.SCHOOL_ORDER`; keying on the exact
    casing would make the two agree only by luck.
    """
    return f"school_{school.casefold()}"


@cache
def _manifest() -> dict:
    """The whole index file, or empty when no art has been extracted.

    Cached because it is read once per card per redraw and the file only changes when
    the extractor runs -- which is a manual act, after which :func:`forget_cache`
    is what makes the new file visible. A missing file is the common case and says
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

    None for the Type tags (Subgraph, Template) on every machine: they have no game art,
    and keep their text pill.
    """
    return icon_path(tag_icon_name(tag))
