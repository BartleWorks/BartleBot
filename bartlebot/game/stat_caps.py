"""Challenge Mode's stat caps: what a player's max health and mana are scaled by, and to what level.

Challenge Mode level scales its players by the **stat caps**, a table in the game's own
data (``tools/dump_stat_caps.py`` writes it to ``data/stat_caps.json``). A stat is scaled
by the cap at the dungeon's level over the cap at the player's own, so a player at half
its own level's cap has half the dungeon level's cap inside. A player is held to the caps of
the lowest ten-level step it has not passed: level 90 to the level 90 caps, 91 to 100 to
the level 100 caps (:func:`band`). Caps apply only in advanced combat, which for BartleBot
means only Challenge Mode.

Why BartleBot cares: a player leaves a Challenge Mode dungeon with the health and mana it
had inside, under its own caps again, so the same health reads as a small share of a much
larger max. On entering, current health is clamped to the scaled max, not scaled, so what
the next entry gives is the share of the *scaled* max, capped at one. :func:`factors` is
that scale; ``nodes/combat_and_healing/scaled_ratios.py`` reads a client's ratios through it.

Which level a dungeon scales to (:func:`dungeon_level`) is the level of the Scales of
Knowledge inside that dungeon, or else of its world's Scales; two worlds with Challenge
Mode and no Scales are listed here. A secret boss's Scales nested inside a larger dungeon
(Pit of the Noxii in Mount Olympus) do not count: in Challenge Mode they are gone, and the
boss is fought at the larger dungeon's level.
"""

import json
import logging
import math
from functools import cache
from pathlib import Path

from bartlebot.game import scales, zone_names

logger = logging.getLogger(__name__)

CAPS_PATH = Path(__file__).resolve().parent / "data" / "stat_caps.json"

#: The highest level, and the step between the levels the caps are given for.
MAX_LEVEL = 180
LEVEL_STEP = 10

#: Worlds with Challenge Mode dungeons and no Scales of Knowledge to read their level from.
#: Darkmoor's is the max level, so it scales nobody.
WORLD_LEVELS_WITHOUT_SCALES: dict[str, int] = {"Wallaru": 170, "Darkmoor": 180}


@cache
def load() -> dict[str, dict[int, tuple[int, int]]]:
    """(max health, max mana) caps by school, then level. Empty when the file cannot be read."""
    try:
        raw = json.loads(CAPS_PATH.read_text(encoding="utf-8"))["caps"]
        return {
            school: {int(level): (int(cap["health"]), int(cap["mana"])) for level, cap in levels.items()}
            for school, levels in raw.items()
        }
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Could not read the stat caps from %s; Challenge Mode ratios go unscaled", CAPS_PATH)
        return {}


def band(level: int) -> int | None:
    """The level whose caps hold a player of `level`: the lowest step it has not passed. None for no level."""
    if level <= 0:
        return None
    return min(MAX_LEVEL, max(LEVEL_STEP, math.ceil(level / LEVEL_STEP) * LEVEL_STEP))


def factors(school: str | None, level: int, target: int) -> tuple[float, float]:
    """What a player's max health and mana are multiplied by when scaled to `target`.

    1.0 for a player whose band is at or below `target`, since nobody is scaled up, and for
    a level or table that cannot be read. A school the table lacks (an unread one, or a
    secondary school) takes the mean over every school, which differs from any one school's
    by a few hundredths.
    """
    own, to = band(level), band(target)
    table = load()
    if own is None or to is None or own <= to or not table:
        return 1.0, 1.0
    schools = [table[school]] if school in table else list(table.values())
    pairs = [(caps[to][0] / caps[own][0], caps[to][1] / caps[own][1]) for caps in schools]
    return sum(h for h, _ in pairs) / len(pairs), sum(m for _, m in pairs) / len(pairs)


def dungeon_level(zone: str) -> int | None:
    """The level Challenge Mode scales `zone`'s dungeon to, or None when nothing says.

    `zone` is the dungeon a sigil enters, in either mode. Its own Scales come first: the
    entry whose Scales stand in that zone, which leaves out a secret boss's Scales one
    gateway further in. Then the world: the two worlds that have none, the world's default
    Scales, or the level every Scales in the world shares (Azteca marks no default).
    """
    zone = zone_names.canonical_zone(zone)
    table = scales.load().values()
    own = next((entry.level for entry in table if entry.zone == zone and entry.level is not None), None)
    if own is not None:
        return own
    world = zone.split("/", 1)[0]
    if world in WORLD_LEVELS_WITHOUT_SCALES:
        return WORLD_LEVELS_WITHOUT_SCALES[world]
    default = scales.world_default(world)
    if default is not None and default.level is not None:
        return default.level
    levels = {entry.level for entry in table if entry.world == world and entry.level is not None}
    return levels.pop() if len(levels) == 1 else None
