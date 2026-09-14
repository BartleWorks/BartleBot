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
        "Gear",  # hats, robes, boots, wands, athames, amulets, rings, decks
        "Jewels",  # jewels and pins both
        "Reagents",
        "Spellements",  # the bot almost always needs a level-scaling node to get these
        "Animus",  # what a monstrology farm is actually after
        "Treasure Cards",
        "Snacks",
        "Seeds",
        "Mounts",
        "Pets",
        "Housing",
        "Gold",  # only where the gold alone is worth the run
    ),
    "Activity": (
        "Boss",  # one named boss, repeated
        "Mobs",  # a non-boss mob fight; replaces Boss rather than joining it
        "Dungeon",  # a full dungeon (more than just a single fight)
        "Challenge Mode",  # dungeons with Challenge Mode available
        "Gauntlet",  # housing gauntlets
        "Team-Up",  # joins the fight through Team-Up instead of entering alone
        "Collecting",  # a reagent-node route with no combat, unlike a boss that drops reagents
        "Training Points",  # a bot that completes training point quests
    ),
    "Type": (
        "Subgraph",  # a reusable building block meant to be called, not run on its own
        "Template",  # a starting point meant to be copied and filled in, not run as-is
    ),
}

#: Every valid tag, in the order they are written to a bot file and shown in the UI.
BOT_TAGS: tuple[str, ...] = tuple(tag for group in TAG_GROUPS.values() for tag in group)

_TAG_ORDER: dict[str, int] = {tag: i for i, tag in enumerate(BOT_TAGS)}


def tag_group(tag: str) -> str:
    """The group *tag* belongs to, or an empty string if it is not in the vocabulary.

    Lives here rather than beside the picker because the grouping is vocabulary
    knowledge, and the library's filtering needs it as much as the widget does: a
    selection is ORed within a group and ANDed across groups, which cannot be
    decided without knowing which group each tag came from.
    """
    for group, tags in TAG_GROUPS.items():
        if tag in tags:
            return group
    return ""


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
