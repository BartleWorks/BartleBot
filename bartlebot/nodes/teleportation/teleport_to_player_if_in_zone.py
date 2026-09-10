"""teleport_to_player_if_in_zone node — port every other hooked client to whichever hooked
client is already in a given zone."""

import asyncio
import logging
from dataclasses import dataclass

from bartlebot import dialog_captions, wizard_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT

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
    ParamField(
        key="skip_extra_clients_in_zone",
        type="bool",
        label="Skip Extra Players Already in Zone",
        help=(
            "Off: every player but the target teleports, including ones already in Target Zone — "
            "being in the same zone does not mean standing next to the target. On: a player already "
            "in Target Zone is left alone rather than re-teleported."
        ),
        default=False,
        required=False,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Checks whether any hooked player is currently in Target Zone and, if so, teleports every "
        "other hooked player to them using the in-game friends list. The first player found in Target "
        "Zone, in player order, is the target — other players already there are also re-teleported to "
        "them by default, unless Skip Extra Players Already in Zone is on. Only ever targets this "
        "bot's own hooked players, never a friend on the friends list."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now with the target in Target Zone."),
        ReturnSpec(
            "FAILURE",
            "No hooked player is currently in Target Zone; the target's wizard name has not been "
            "detected yet; the friend-busy dialog appeared; players did not enter a loading screen "
            "within the trigger timeout; or not all players arrived in Target Zone after loading.",
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "All players must be in-game friends with each other: this drives the friends list, it does "
        "not port through the party. The target is picked by player order among players already in "
        "Target Zone — it is never asked to port to itself."
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
    APIs: client.zone_name(), client.is_loading(), node_helpers.port_to_friend()

    Flow:
      1. find_target_in_zone(): the first client in target_zone, in player order, is the
         target. No client in target_zone, or the target's wizard name not yet cached ->
         FAILURE.
      2. Build clients_to_move: every client but the target. When skip_extra_clients_in_zone
         is set, a client already in target_zone (other than the target) is excluded too.
         Empty -> SUCCESS immediately, nothing to do.
      3. node_helpers.port_to_friend(): initiate the teleport for all clients_to_move,
         answer modals, retry stragglers, and wait for each to enter a loading screen.
         ValueError (friend not found/offline), a refusal, or not every client loading
         within the timeout -> FAILURE.
      4. Wait for all loading screens to finish.
      5. Wait for every client (including the target and any skipped extras) to be in
         target_zone. Return FAILURE if any are not.

    FAILURE: no hooked client is in target_zone; the target's wizard name has not been
    detected; friend-busy dialog appeared; loading not detected within ZONE_CHANGE_TIMEOUT;
    not all clients arrived in target_zone.
    """
    target_zone: str = params.get("target_zone", "")
    skip_extra_clients_in_zone: bool = params.get("skip_extra_clients_in_zone", False)

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
    friend_name = target.name.lower()
    machine.log(f"teleport_to_player_if_in_zone: target is P{target.index + 1}, in {target_zone!r}")
    machine.log(f"Resolved target name: {target.name!r}", logging.DEBUG)

    # 2. Build the set of clients to move.
    in_zone_indices = [i for i, z in enumerate(zones) if node_helpers.zone_matches(z, target_zone)]
    extra_in_zone = set(in_zone_indices[1:])
    clients_to_move = [
        c
        for i, c in enumerate(clients)
        if i != target.index and not (skip_extra_clients_in_zone and i in extra_in_zone)
    ]
    if not clients_to_move:
        machine.log(f"SUCCESS: no other clients need to teleport to {target_zone!r}")
        return "SUCCESS"
    machine.log(f"{len(clients_to_move)} of {len(clients)} client(s) will teleport", logging.DEBUG)

    # 3. Initiate the teleport and wait for it to trigger a loading screen.
    machine.log("Calling teleport_to_friend_from_list…", logging.DEBUG)
    try:
        port_result = await node_helpers.port_to_friend(
            clients_to_move,
            friend_name,
            trigger_timeout=TRIGGER_TIMEOUT,
            ceiling=ZONE_CHANGE_TIMEOUT,
            poll_interval=POLL_INTERVAL,
        )
    except ValueError as exc:
        machine.log(f"FAILURE: {exc}", logging.WARNING)
        return "FAILURE"

    if port_result.refusal:
        machine.log(
            f"FAILURE: client {port_result.refused_index} cannot reach the target (P{target.index + 1}) — "
            f"{dialog_captions.describe(port_result.refusal)}",
            logging.WARNING,
        )
        return "FAILURE"
    if len(port_result.seen_loading) < len(clients_to_move):
        not_triggered = [i for i in range(len(clients_to_move)) if i not in port_result.seen_loading]
        machine.log(f"Clients not loading at timeout: {not_triggered}", logging.DEBUG)
        machine.log(
            f"FAILURE: {len(not_triggered)} client(s) did not enter a loading screen within {ZONE_CHANGE_TIMEOUT:.0f}s",
            logging.WARNING,
        )
        return "FAILURE"

    # 4. Wait for all loading screens to finish.
    machine.log("Loading detected; waiting for screens to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients_to_move), None, POLL_INTERVAL)

    # 5. Verify all clients (including the target and any skipped extras) arrived.
    actual_zones = await node_helpers.wait_for_zone(clients, target_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)
    if actual_zones is not None:
        machine.log(f"Zones after teleport: {actual_zones}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients in expected zone (expected={target_zone!r}, got={list(set(actual_zones))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {target_zone!r}")
    return "SUCCESS"
