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

# Ordered groups, used to lay out the chip row and the editor's menu. Validation is
# against the flat BOT_TAGS below — the grouping carries no meaning beyond ordering.
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
