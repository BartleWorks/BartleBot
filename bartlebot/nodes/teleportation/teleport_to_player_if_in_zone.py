"""teleport_to_player_if_in_zone node — port every other hooked client to whichever hooked
client is already in a given zone."""

import asyncio
import logging
from dataclasses import dataclass

from bartlebot import wizard_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL

logger = logging.getLogger(__name__)

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="target_zone",
        type="str",
        label="Target Zone",
        help="Zone name to check hooked players against (e.g. 'WizardCity/WC_Ravenwood').",
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Checks whether any hooked player is currently in Target Zone and, if so, teleports every "
        "other hooked player to them using the in-game friends list. The first player found in Target "
        "Zone, in player order, is the target. Every other player is sent, including ones already in "
        "Target Zone or standing beside the target: neither can tell which realm a player is on, and a "
        "port within the same zone and realm is quick."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is standing with the target, in Target Zone."),
        ReturnSpec(
            "FAILURE",
            "No hooked player is currently in Target Zone; the target's wizard name has not been "
            "detected yet; the target is not on every player's online friends list; the friend-busy "
            "dialog appeared; a player showed no sign of porting; or not every player ended up with "
            "the target.",
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "All players must be in-game friends with each other: this drives the friends list, it does "
        "not port through the party. The target is picked by player order among players already in "
        "Target Zone — it is never asked to port to itself. A port within the same zone and realm "
        "shows no loading screen; it is confirmed by the player landing on the target instead, and "
        "a player already beside the target is expected to show nothing at all."
    ),
)


@dataclass(frozen=True)
class ZoneTarget:
    """Which hooked client to port everyone else to, or why there isn't one.

    Mirrors teleport_to_friend's TargetResolution — same shape, different resolution rule
    (the first client already in target_zone, by player order, rather than a configured name
    or slot). Shared by this node and node_helpers' zone-arrival recovery.
    """

    index: int | None = None
    name: str = ""
    error: str = ""


async def find_target_in_zone(clients: list, target_zone: str, zones: list[str] | None = None) -> ZoneTarget:
    """Pick the first hooked client (in player order) already in target_zone as the target.

    `zones` lets a caller that already read every client's zone_name() pass it in rather than
    have this read it again — node_helpers' recovery path always has a fresh one on hand.

    .error is set when no client is in target_zone, or the one found there has no cached
    wizard name yet (ClientManager fills this; a client just connected may not have it yet).
    """
    if zones is None:
        zones = list(await asyncio.gather(*[c.zone_name() for c in clients]))

    in_zone_indices = [i for i, z in enumerate(zones) if node_helpers.zone_matches(z, target_zone)]
    if not in_zone_indices:
        return ZoneTarget(error=f"no hooked client is currently in {target_zone!r}")

    target_index = in_zone_indices[0]
    target_name = wizard_names.cached_name(clients[target_index])
    if not target_name:
        return ZoneTarget(error=f"P{target_index + 1}'s wizard name has not been detected yet")

    return ZoneTarget(index=target_index, name=target_name)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.is_loading(), client.body.position(),
          node_helpers.gather_at_player()

    Flow:
      1. find_target_in_zone(): the first client in target_zone, in player order, is the
         target. No client in target_zone, or the target's wizard name not yet cached ->
         FAILURE.
      2. node_helpers.gather_at_player(): port every other client onto the target, accepting
         a loading screen or a jump onto it as the port, and nothing at all from a client that
         was already beside it; then confirm every client is in the target's zone and within
         POSITION_MARGIN of it.

    FAILURE: no hooked client is in target_zone; the target's wizard name has not been
    detected; anything gather_at_player fails on (see its docstring).
    """
    target_zone: str = params.get("target_zone", "")

    if not target_zone:
        machine.log("FAILURE: target_zone parameter is empty", logging.WARNING)
        return "FAILURE"

    # 1. Find every client currently in target_zone, in player order, and pick the target.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current zones: {list(zones)}", logging.DEBUG)
    target = await find_target_in_zone(clients, target_zone, zones=zones)
    if target.error:
        machine.log(f"FAILURE: {target.error}", logging.WARNING)
        return "FAILURE"
    machine.log(f"teleport_to_player_if_in_zone: target is P{target.index + 1}, in {target_zone!r}")
    machine.log(f"Resolved target name: {target.name!r}", logging.DEBUG)

    # 2. Port everyone else onto the target and confirm they are standing with it.
    if not await node_helpers.gather_at_player(
        machine, clients, target.index, target.name, poll_interval=POLL_INTERVAL
    ):
        return "FAILURE"

    machine.log(f"SUCCESS: every player is with P{target.index + 1} in {target_zone!r}")
    return "SUCCESS"
