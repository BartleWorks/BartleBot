"""The game's badge categories: their order, their names, and the icon each one draws.

The badge page groups every badge under a category -- a world, a school, or an activity
such as Crafting or Four Dungeons -- and draws an icon beside each. The bot library's
folders are those same categories, so a player can find a bot where the game has already
taught them to look (see :mod:`bartlebot.content.bot_folders`).

WHERE IT COMES FROM
    ``BadgeFilterDescriptions.xml`` and ``BadgeFilterNames.lang`` in ``Root.wad``, dumped
    by ``tools/dump_badge_categories.py`` into ``data/badge_categories.json``. The key is
    the game's own filter name -- ``WizardCity``, ``4Dungeons``, ``MonsterMagic`` -- and is
    stable across locales and patches; the caption is not, and is looked up per locale.

    The keys are **not** the nav map's world keys: the badge page says ``Dragonspyre``
    where ``zone_name()`` says ``DragonSpire``. Nothing here routes a client anywhere, so
    the two are never compared.

WHY THE CAPTION DROPS "BADGES"
    Every en-US name is "<category> Badges", which is right on the badge page and noise in
    a folder list. Only en-US is trimmed, because only en-US is shown today: other locales
    word it differently ("Abzeichen: ..."), and a suffix rule that is right for one
    language is wrong for the next.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

BADGE_CATEGORIES_PATH = Path(__file__).resolve().parent / "data" / "badge_categories.json"

_SUFFIX = " Badges"


@cache
def _categories() -> dict[str, dict]:
    """{key: entry}, in the game's order."""
    try:
        raw = json.loads(BADGE_CATEGORIES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read badge categories from %s", BADGE_CATEGORIES_PATH)
        return {}
    return {entry["key"]: entry for entry in raw.get("categories", []) if entry.get("key")}


def keys() -> tuple[str, ...]:
    """Every category key, in the order the game lists them."""
    return tuple(_categories())


def label(key: str) -> str:
    """The en-US caption without its trailing " Badges", or the key when there is none."""
    name = _categories().get(key, {}).get("names", {}).get("en-US")
    if not name:
        return key
    return name.removesuffix(_SUFFIX) or name


def icon_texture(key: str) -> str | None:
    """The game path of the texture the badge page draws for *key*, or None."""
    return _categories().get(key, {}).get("icon") or None
