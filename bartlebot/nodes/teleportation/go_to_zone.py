"""go_to_zone node — take every player to any zone on the navigation map, from wherever they are."""

import logging

from bartlebot import player_realms, zone_names
from bartlebot.nodes import zone_navigation
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="nav_zone",
        label="Zone",
        help=(
            "The zone to take every player to. The list is grouped by world; type part of a zone's "
            "name, its world or its zone id to narrow it.\n\n"
            "Players can start anywhere, another world included: the route is planned from wherever "
            "they are when the node runs."
        ),
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Takes every player to the chosen zone from wherever they are, planning the route through "
        "walks, teleporters, zone lists, rides, World Gates, the hub button and HOME."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is in the zone."),
        ReturnSpec(
            "FAILURE",
            "The zone is not on the navigation map; there is no route from where the players are "
            "(most often a world or teleporter the character has not unlocked); a step failed and no "
            "other route was left; or a player is in a restricted zone.",
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            minimum=0.0,
            suffix=" s",
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=3,
    usage_notes=(
        "Players arrive wherever the last zone change puts them, which is not a fixed spot — follow "
        "this node with Teleport To Coordinates when the position matters.\n\n"
        "Players already in the zone are left where they are, so this is cheap at the top of a bot.\n\n"
        "A short route is travelled together. For a longer one, or one through a teleporter, one "
        "player travels and the others port to it when it arrives, through the friends list or else "
        "the group: a port is one loading screen for everybody. A player who misses a step waits "
        "where it is and ports at the end in the same way. Players who cannot port to one another "
        "travel together instead."
    ),
    see_also=["go_to_world", "teleport_to_world_hub"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: zone_navigation.navigate()

    Flow:
      1. Return FAILURE if no zone is set.
      2. navigate() reads where the players are, chooses whether they travel together or one
         travels and the rest port to it, and runs the route one planned step at a time.

    FAILURE: no zone set; the zone is not on the map; no route; a step failed with no route
    around it; a player in a restricted zone.
    """
    zone: str = params.get("zone") or ""
    if not zone:
        machine.log("FAILURE: no zone chosen", logging.WARNING)
        return "FAILURE"
    machine.log(f"go_to_zone: {zone_names.label(zone)} ({zone})")
    outcome = await zone_navigation.navigate(machine, clients, zone)
    return "SUCCESS" if outcome.success else "FAILURE"
