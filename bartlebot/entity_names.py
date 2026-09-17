"""Resolve a world entity's internal object name to the name a player reads.

``ClientObject.object_name()`` is an internal template name -- ``AQ-ATL-NPC02``,
``KT-TeleporterEntranceHall`` -- and it is the *identity* nodes are written against, so it
is what a bot file stores and what the Entity Browser's Copy Name hands over. It is also
unreadable, which is what makes the browser's list hard to search: nothing in
``AQ-Centaur-Artemis`` says "Crescent Moon Centaur".

The display text lives beside it in the game's own files, and
``bartlebot/data/entity_names.json`` is the checked-in join, extracted by
``tools/dump_entity_names.py``. That file explains why it is a snapshot rather than the
run-time lookup WizWalker's ``ClientObject.display_name()`` does, and why it is English only.

Resolution only ever feeds the display layer. An entity missing from the snapshot -- no
player-facing name, or added to the game since it was taken -- resolves to ``None``, and a
caller shows the object name it already has. That is the ordinary case, not an error: plenty
of world objects are scenery the game never names.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

ENTITY_NAMES_PATH = Path(__file__).resolve().parent / "data" / "entity_names.json"


@cache
def _names(path: Path | None = None) -> dict[str, str]:
    """Load the checked-in snapshot. Cached: it never changes at run time."""
    names_path = path or ENTITY_NAMES_PATH
    try:
        raw = json.loads(names_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read entity names from %s", names_path)
        return {}
    return raw.get("names", {})


def display_name(object_name: str, path: Path | None = None) -> str | None:
    """The text a player sees for `object_name`, or None if the game never names it."""
    return _names(path).get(object_name) if object_name else None


def label(object_name: str, path: Path | None = None) -> str:
    """One readable name: the player-facing one if there is one, else the object name."""
    return display_name(object_name, path) or object_name
