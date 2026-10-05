"""Health and mana ratios as the next Challenge Mode entry will see them.

A player leaves a Challenge Mode dungeon holding the health and mana it had under the
scaled caps, and outside they read against its full max, so a player who left at full
health reads well under half. Entering again clamps health to the scaled max, so what
matters before going back in is the share of the *scaled* max (``bartlebot/stat_caps.py``).
``handle_potions`` and ``collect_wisps`` read their ratios here, so one threshold means the
same thing in either mode, and a bot needs no Challenge Mode branch of its own.
"""

import asyncio

from wizwalker.memory.memory_objects.conditionals import school_to_str

from bartlebot import stat_caps, zone_names
from bartlebot.nodes.zone_entry.enter_dungeon import OPTION_CHALLENGE_MODE, PARAM_OFFERS_CHALLENGE_MODE


def challenge_level(machine) -> int | None:
    """The level this bot's Challenge Mode scales its players to, or None when it is not in use.

    None when the Challenge Mode option is off, when no ``enter_dungeon`` offers it, and when
    no level is known for the dungeon. Read from the bot rather than stored on it, so it
    follows the bot as it is edited. Two Challenge Mode dungeons at different levels take
    the higher, which scales least and so errs toward using a potion.
    """
    if not machine.get_option(OPTION_CHALLENGE_MODE, False):
        return None
    config = machine.bot_config
    levels = {
        level
        for state in getattr(config, "states", None) or ()
        if state.node_type == "enter_dungeon" and (state.params or {}).get(PARAM_OFFERS_CHALLENGE_MODE)
        if (zone := (state.params or {}).get("dungeon_zone"))
        if (level := stat_caps.dungeon_level(zone)) is not None
    }
    return max(levels) if levels else None


async def ratios(client, target: int | None) -> tuple[float, float]:
    """(health, mana) ratios against the max a dungeon scaled to `target` will give, capped at 1.

    The plain ratios when `target` is None, and when the client is standing in a Challenge
    Mode zone, where the game has scaled its max already.
    """
    health, mana = await asyncio.gather(client.calc_health_ratio(), client.calc_mana_ratio())
    if target is None or zone_names.challenge_variant_of(await client.zone_name()):
        return health, mana
    level, school_id = await asyncio.gather(client.stats.reference_level(), client.stats.school_id())
    health_factor, mana_factor = stat_caps.factors(school_to_str.get(school_id), level, target)
    return min(1.0, health / health_factor), min(1.0, mana / mana_factor)
