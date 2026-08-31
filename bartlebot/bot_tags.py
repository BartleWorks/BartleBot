"""The curated vocabulary of bot type tags, and the one rule for normalizing them.

A tag says what a bot is *for*; the world folder it lives in says where it runs.
The two are independent, which is why tags filter across the library rather than
subdividing it.

The vocabulary is fixed on purpose. A shipped library that drifts between
``reagent`` / ``Reagents`` / ``reagents`` cannot be filtered, and free-form tags in
files that everyone shares drift within a release. Adding a tag is a code change,
which is the point: it forces the question of whether an existing tag already covers
it.

Tags are deliberately *not* derived from ``drops[].category``. Nearly every boss
drops jewels and treasure cards, so a derived list would tag almost every bot with
almost every tag — the opposite of a filter. They record stated intent instead.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Ordered groups. "Rewards" is what a run yields, "Activity" is what it does, and a
# bot normally carries at least one of each. Validation is against the flat BOT_TAGS
# below — the grouping decides presentation order and nothing else.
TAG_GROUPS: dict[str, tuple[str, ...]] = {
    "Rewards": (
        "Gear",
        "Jewels",
        "Reagents",
        "Spellements",
        "Animus",
        "Treasure Cards",
        "Snacks",
        "Seeds",
        "Mounts",
        "Pets",
        "Housing",
        "Gold",
    ),
    "Activity": (
        "Boss",
        "Mobs",
        "Dungeon",
        "Gauntlet",
        "Team-Up",
        "Gathering",
    ),
}

#: Every valid tag, in the order they are written to a bot file and shown in the UI.
BOT_TAGS: tuple[str, ...] = tuple(tag for group in TAG_GROUPS.values() for tag in group)

_TAG_ORDER: dict[str, int] = {tag: i for i, tag in enumerate(BOT_TAGS)}

# One line per tag, shown beneath the name in the tag picker. These are the rule the
# library was tagged by, written down where someone tagging a new bot will actually
# read it — the distinctions that matter (a reagent *route* vs a boss that drops
# reagents; a spellement farm needing level scaling) are invisible from the name alone.
TAG_DESCRIPTIONS: dict[str, str] = {
    "Gear": "Hats, robes, boots, wands, athames, amulets, rings and decks",
    "Jewels": "Jewels and pins for socketed gear",
    "Reagents": "Crafting reagents",
    "Spellements": "Spell upgrades — the bot usually needs level scaling to get them",
    "Animus": "Monstrology animus, for levelling the Monstrology skill",
    "Treasure Cards": "Treasure cards",
    "Snacks": "Pet snacks",
    "Seeds": "Garden seeds",
    "Mounts": "Permanent and timed mounts",
    "Pets": "Pets and pet eggs",
    "Housing": "Housing items and furniture",
    "Gold": "Gold worth the run on its own",
    "Boss": "Repeats one named boss fight",
    "Mobs": "Repeats a non-boss mob fight",
    "Dungeon": "Runs a dungeon through to its end",
    "Gauntlet": "Runs a gauntlet from its entrance sigil",
    "Team-Up": "Joins the fight through Team-Up rather than entering alone",
    "Gathering": "Walks a reagent-node route — no combat",
}


def describe(tag: str) -> str:
    """The one-line description for *tag*, or an empty string if it is unknown."""
    return TAG_DESCRIPTIONS.get(tag, "")


def normalize_tags(raw: object) -> list[str]:
    """Coerce a bot file's raw ``tags`` value into a valid, ordered, de-duplicated list.

    Tolerant rather than strict, and deliberately so: this runs on every load, and a
    ``ValueError`` here would make a hand-edited bot fail to open at all rather than
    open with one tag missing. Same reasoning as ``accept_bare_drop_names`` and
    ``migrate_named_color`` in :mod:`bartlebot.bot.schema`.

    Used by both :class:`~bartlebot.bot.schema.BotConfig` and the library panel, which
    reads bot files outside Pydantic and needs the identical rule.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        if entry not in _TAG_ORDER:
            logger.warning("ignoring unknown bot tag %r (not in BOT_TAGS)", entry)
            continue
        seen.add(entry)
    return sorted(seen, key=_TAG_ORDER.__getitem__)
