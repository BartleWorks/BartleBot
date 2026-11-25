"""Which zones a bot names that have no walking map.

Every move inside a zone walks (``nodes/walking.py``), on the zone maps core ships. A zone
with none can't be walked in, so a bot naming one is warned about when it loads and refused
at Run. The check reads only the maps' index (``ZoneMaps.has``), never a map: it runs on
every validation pass, and its budget is 10 ms on the largest shipped bot.

The zones are the ones the restricted-zone check reads (``restricted_zones._zone_param_keys``),
from every state of the bot, a subgraph's members included. A glob pattern has a map when
any mapped zone matches it.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

from bartleby.runtime.movement import zone_maps

from bartlebot.bot.schema import StateModel
from bartlebot.game.restricted_zones import _zone_param_keys

#: The maps the check asks: core's shipped ones, so a test can hand it its own index.
MAPS = zone_maps.SHIPPED

#: Said after the zones wherever a bot is refused for one.
NO_MAP_TEXT = (
    "Every move in a zone walks, along that zone's walking map, and these zones have none. "
    "Choose another zone, or report the zone so a map can be added."
)


def _has_map(zone: str) -> bool:
    if any(c in zone for c in "*?["):
        return any(fnmatch.fnmatchcase(mapped, zone) for mapped in MAPS.index)
    return MAPS.has(zone)


def unmapped_zones(states: Iterable[StateModel]) -> list[tuple[str, str]]:
    """(state label, zone) for every zone a state names that has no walking map."""
    found: list[tuple[str, str]] = []
    for state in states:
        params = state.params or {}
        for key in _zone_param_keys(params):
            zone = params.get(key)
            if isinstance(zone, str) and zone and not _has_map(zone):
                found.append((state.label or state.id, zone))
    return found


def warning(zone: str) -> str:
    """The validation warning on a node naming *zone*."""
    return f"{zone} has no walking map, so the bot can't be run"


def describe_found(found: Iterable[tuple[str, str]]) -> str:
    """One bullet per zone :func:`unmapped_zones` found: the node and the zone."""
    return "\n".join(f"• {label}: {zone}" for label, zone in found)
