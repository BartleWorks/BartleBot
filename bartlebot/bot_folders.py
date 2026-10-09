"""The bot library's folders: which badge category each one is, and what it is called.

The folders under ``bots/`` are the game's own badge categories (see
:mod:`bartlebot.game.badge_categories`), because that is a grouping players already know:
the worlds in the order the badge page lists them, then activities such as Crafting and
Four Dungeons. A folder's icon and caption both come from its category.

WHY THE FOLDER NAMES STAY READABLE
    The folder is where a user saves a new bot, so it has to say what it is on disk:
    ``25_crafting``, not ``Crafting``, and certainly not ``4Dungeons``. The number keeps
    Explorer's order the same as the library's, and is stripped before the lookup, so
    renumbering a folder never touches this table.

WHY THE TABLE IS WRITTEN OUT
    The two names are ours and the game's, and no rule joins them: ``four_dungeons`` is
    ``4Dungeons``, ``monstrology`` is ``MonsterMagic``, ``dragonspyre`` is
    ``Dragonspyre``. ``test_bot_folders.py`` checks every folder under ``bots/`` is here,
    every key exists in the snapshot, and the numbers follow the game's order.

A folder that is not here -- one a user made, or one under a custom library path -- is
listed like any other, with a caption made from its name and no icon.
"""

from __future__ import annotations

import re

from bartlebot.game import badge_categories

#: Folder name, without its number -> badge category key.
FOLDER_BADGES: dict[str, str] = {
    "wizard_city": "WizardCity",
    "krokotopia": "Krokotopia",
    "marleybone": "Marleybone",
    "mooshu": "MooShu",
    "dragonspyre": "Dragonspyre",
    "grizzleheim": "Grizzleheim",
    "celestia": "Celestia",
    "zafaria": "Zafaria",
    "avalon": "Avalon",
    "azteca": "Azteca",
    "khrysalis": "Khrysalis",
    "wysteria": "Wysteria",
    "aquila": "Aquila",
    "polaris": "Polaris",
    "mirage": "Mirage",
    "empyrea": "Empyrea",
    "arcanum": "Arcanum",
    "catacombs": "Catacombs",
    "karamelle": "Karamelle",
    "lemuria": "Lemuria",
    "novus": "Novus",
    "wallaru": "Wallaru",
    "selenopolis": "Selenopolis",
    "darkmoor": "Darkmoor",
    "crafting": "Crafting",
    "gardening": "Gardening",
    "housing_gauntlets": "HousingGauntlet",
    "boss_rematch": "BossRematch",
    "lost_pages": "LostPages",
    "five_boxes": "FiveBOXES",
    "four_dungeons": "4Dungeons",
    "skeleton_key": "SkeletonKey",
    "monstrology": "MonsterMagic",
    "special_events": "SpecialEvents",
    "raids": "Raid",
}

#: Folders that hold bot files but are no badge category, and so carry no icon.
UNBADGED_FOLDERS: frozenset[str] = frozenset({"subgraphs"})

#: The category the library's "All" row wears: the badge page's own "all badges" spiral.
ALL_BADGE = "All"

_NUMBER = re.compile(r"^\d+_")


def folder_slug(dir_name: str) -> str:
    """'25_crafting' -> 'crafting'."""
    return _NUMBER.sub("", dir_name)


def folder_number(dir_name: str) -> int | None:
    """'25_crafting' -> 25, or None for an unnumbered folder."""
    match = _NUMBER.match(dir_name)
    return int(match.group()[:-1]) if match else None


def folder_badge(dir_name: str) -> str | None:
    """The badge category key a folder stands for, or None."""
    return FOLDER_BADGES.get(folder_slug(dir_name))


def folder_caption(dir_name: str) -> str:
    """What the library calls a folder: its category's name, else a tidied folder name.

    '31_four_dungeons' -> 'Four Dungeons'; 'my_bots' -> 'My Bots'.
    """
    badge = folder_badge(dir_name)
    if badge is not None:
        return badge_categories.label(badge)
    return folder_slug(dir_name).replace("_", " ").title()
