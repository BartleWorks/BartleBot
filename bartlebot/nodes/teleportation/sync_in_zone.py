"""sync_in_zone node — bring every player to one zone, one realm and one area, from wherever they are.

Was go_to_zone, which took every player to a zone and nothing more. Arriving in one zone is
not arriving together: players on different realms enter separate dungeons and separate
battles. So the node now ends with every player beside a leader, on one realm, confirmed by
sight (#133), and the realm step Player Sync used to run separately is part of it.
"""

import logging

from bartlebot import player_realms, realms, zone_names
from bartlebot.nodes import zone_navigation
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.utility.change_realm import OPTION_FAVORITE_REALM
from bartlebot.option_groups import GROUP_GENERAL

PARAM_KEEP_CURRENT_REALM = "keep_current_realm"

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
    ParamField(
        key=PARAM_KEEP_CURRENT_REALM,
        type="bool",
        label="Keep Current Realm",
        help=(
            "Off: the leader changes to your Favorite Realm on arrival, and everyone joins it there.\n\n"
            "On: nobody opens a spellbook, and everyone joins the leader on the realm it is already on. "
            "For a leg partway through a bot that left the Favorite Realm on purpose.\n\n"
            "Either way every player ends on one realm, unless the bot's Player Realms is Separate: "
            "then nobody changes realm at all."
        ),
        default=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Brings every player to the chosen zone, on one realm and beside one another, from wherever they "
        "are. One player leads: it travels, changes to the Favorite Realm, and everyone it cannot see ports "
        "to it. Routes run through walks, teleporters, zone lists, rides, World Gates, the hub button and HOME."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is in the zone, on one realm, and seen beside the leader."),
        ReturnSpec(
            "FAILURE",
            "The zone is not on the navigation map; there is no route from where the players are "
            "(most often a world or teleporter the character has not unlocked); a step failed and no "
            "other route was left; a player is in a restricted zone; the leader could not change realm; "
            "or a player could not be brought to the leader.",
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
        BotOptionSpec(
            name="Safety Delay",
            group=GROUP_GENERAL,
            type="float",
            default=3.0,
            minimum=0.0,
            suffix=" s",
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
        BotOptionSpec(
            name=OPTION_FAVORITE_REALM,
            group=GROUP_GENERAL,
            type="realm",
            # Null, always: the author has no opinion about which realm you farm in, and
            # cannot have one. The level below is your app setting.
            default=None,
            description=(
                "The realm the leader changes to, and everyone joins it on. This is your value, not the "
                "bot's — it is stored on this machine (Settings → Bot) and never written to a bot file. "
                "Left unset, nobody changes realm, and everyone joins the leader on its own."
            ),
            required_unless=PARAM_KEEP_CURRENT_REALM,
        ),
    ],
    max_consecutive_retries=3,
    usage_notes=(
        "Players arrive wherever the last zone change puts them, which is not a fixed spot — follow "
        "this node with Teleport To Coordinates when the position matters.\n\n"
        "Players already together in the zone are left where they are, so this is cheap at the top of a "
        "bot: one look at who sees whom, and one spellbook read by the leader when it has a realm to check.\n\n"
        "A short route is traveled together. For a longer one, or one through a teleporter, the leader "
        "travels and the others port to it when it arrives, through the friends list or else the group: a "
        "port is one loading screen for everybody, and it puts the porter on the leader's realm. A player "
        "who cannot port travels on its own, then changes to the leader's realm. The leader cannot change "
        "realm in Eerem Palace, so bound there it changes on the way.\n\n"
        "With Player Realms set to Separate, every player travels, and nobody ports or changes realm."
    ),
    see_also=["go_to_world", "teleport_to_world_hub", "change_realm"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: zone_navigation.navigate()

    Flow:
      1. Return FAILURE if no zone is set.
      2. Resolve the realm the leader hops to: the Favorite Realm, unless keep_current_realm is on
         or no favorite is set. FAILURE if the favorite names a realm this build does not know.
      3. navigate() picks a leader, gets it to the zone and the realm, brings everyone it does not
         see to it, and confirms by sight that they are together.

    FAILURE: no zone set; an unknown favorite realm; the zone is not on the map; no route; a step
    failed with no route around it; a player in a restricted zone; the leader could not change
    realm; a player could not be brought to the leader.
    """
    zone: str = params.get("zone") or ""
    if not zone:
        machine.log("FAILURE: no zone chosen", logging.WARNING)
        return "FAILURE"

    realm = None
    if not params.get(PARAM_KEEP_CURRENT_REALM, False):
        realm = str(machine.get_option(OPTION_FAVORITE_REALM, "") or "").strip() or None
        if realm is not None and not realms.is_known(realm):
            machine.log(
                f"FAILURE: favorite realm {realm!r} is not a realm this build knows — pick one from the list "
                "in Settings → Bot (or regenerate the realm table with tools/dump_realm_names.py)",
                logging.WARNING,
            )
            return "FAILURE"

    where = f"{zone_names.label(zone)} ({zone})"
    machine.log(f"sync_in_zone: {where}" + (f", on {realms.label(realm)}" if realm else ""))
    outcome = await zone_navigation.navigate(machine, clients, zone, realm=realm)
    return "SUCCESS" if outcome.success else "FAILURE"
