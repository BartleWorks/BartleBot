"""sync_in_zone node — bring every player to one zone, one realm and one area, from wherever they are.

Was go_to_zone, which took every player to a zone and nothing more. Arriving in one zone is
not arriving together: players on different realms enter separate dungeons and separate
battles. So the node now ends with every player beside a leader, on one realm, confirmed by
sight (#133), and the realm step Player Sync used to run separately is part of it.
"""

import logging

from bartlebot.game import realms, zone_names
from bartlebot.nodes import zone_navigation
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.utility.change_realm import OPTION_FAVORITE_REALM
from bartlebot.options import player_realms
from bartlebot.options.bot_options import FAVORITE_REALM_DESCRIPTION
from bartlebot.options.option_groups import GROUP_GENERAL

PARAM_KEEP_CURRENT_REALM = "keep_current_realm"

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="nav_zone",
        label="Zone",
        help=(
            "The zone to bring every player to. Type part of a zone's name, world or ID to narrow the "
            "list. Players can start anywhere, even in another world."
        ),
        default="",
        required=True,
    ),
    ParamField(
        key=PARAM_KEEP_CURRENT_REALM,
        type="bool",
        label="Keep Current Realm",
        help=(
            "When on, nobody changes realm, and everyone joins the leader on the realm it's on. Use "
            "it partway through a bot that left the Favorite Realm on purpose.\n\nWhen off, the leader "
            "changes to your Favorite Realm on arrival."
        ),
        default=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Brings every player to the chosen zone, together and on one realm, from wherever they "
        "are. One player leads, and the others teleport to it."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is in Zone, on one realm, beside the leader."),
        ReturnSpec(
            "FAILURE",
            (
                "There's no route from where players are, usually because a world or teleporter isn't "
                "unlocked yet. Or a step failed with no other route left, Zone isn't on the navigation "
                "map, a player is in a restricted zone, the leader couldn't change realm, or a player "
                "couldn't reach the leader."
            ),
        ),
    ],
    required_bot_options=[
        player_realms.SPEC,
        SAFETY_DELAY_OPTION,
        BotOptionSpec(
            name=OPTION_FAVORITE_REALM,
            group=GROUP_GENERAL,
            type="realm",
            # Null, always: the author has no opinion about which realm you farm in, and
            # cannot have one. The level below is your app setting.
            default=None,
            description=FAVORITE_REALM_DESCRIPTION,
            required_unless=PARAM_KEEP_CURRENT_REALM,
        ),
    ],
    max_consecutive_retries=3,
    usage_notes=(
        (
            "Follow this node with Teleport to Coordinates when the exact spot matters. Players "
            "arrive wherever the last zone change puts them."
        )
        + "\n\n"
        + ("Players already together in the zone stay where they are, so the node is cheap at the top of a bot.")
        + "\n\n"
        + (
            "On a long route, or one through a teleporter, the leader travels and the others teleport "
            "to it on arrival. A player who can't teleport travels alone, then changes to the "
            "leader's realm."
        )
        + "\n\n"
        + ("With Player Realms set to Separate, every player travels on its own and nobody changes realm.")
    ),
    see_also=["teleport_to_world_hub", "change_realm"],
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
