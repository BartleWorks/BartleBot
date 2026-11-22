"""The icon keys only BartleBot draws, over core's game art: worlds, badges, drops and the Recent row.

The extracted art's read side is core's (``bartleby.runtime.game.card_art``, BartleBuddy #31,
Stage 6), with the keys a shared surface draws: schools, archmastery, duel slots and tags.
These are the zone pickers', the bot library's and the drop tables', each a key
:func:`~bartleby.runtime.game.card_art.icon_path` answers. The extractor
(:mod:`bartlebot.game.art_extraction`) writes them under the same names.
"""

from __future__ import annotations

from pathlib import Path

from bartleby.runtime.game import card_art

from bartlebot.game.wiki_drops import PIN_CATEGORY, pin_kind

#: The pocket watch the bot library's Recent row wears.
RECENT_ICON = "recent"

#: Logical name for the generic dungeon badge. Not a world, but it is keyed and stored
#: beside them, because the surfaces that ask for a world's emblem are the ones that need
#: a stand-in when a "world" turns out to be a dungeon.
DUNGEON_ICON = "dungeon"


def world_icon_name(world: str) -> str:
    """The icon key for a world, from the key the nav map uses ("MooShu" -> "world_mooshu").

    Case-folded for the same reason ``card_art.school_icon_name`` is: the caller's key comes
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
    return "drop_" + card_art.icon_slug(category)


def pin_icon_name(kind: str) -> str:
    """The icon key for a pin kind ("shield" -> "pin_shield")."""
    return f"pin_{kind}"


def world_icon_path(world: str) -> Path | None:
    """The PNG for a world's emblem, or None if there is not one on this machine.

    No fallback to the dungeon badge here, and deliberately: which "worlds" are really
    dungeons is the extractor's table to state (``WORLD_ICON_SOURCES``), which points
    those keys at the dungeon texture so they resolve like any other. A fallback in the
    read side would be a second place that decides the same thing, and the two would
    drift the first time a dungeon group was added.
    """
    return card_art.icon_path(world_icon_name(world))


def badge_icon_path(key: str) -> Path | None:
    """The PNG for a badge category's icon, or None if there is not one on this machine."""
    return card_art.icon_path(badge_icon_name(key))


def drop_icon_path(category: str, item_name: str = "") -> Path | None:
    """The PNG for a drop's category icon, or None if there is not one on this machine."""
    return card_art.icon_path(drop_icon_name(category, item_name))
