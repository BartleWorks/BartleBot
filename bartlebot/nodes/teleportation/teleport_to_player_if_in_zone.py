"""teleport_to_player_if_in_zone node — port every other hooked client to whichever hooked
client is already in a given zone."""

import asyncio
import logging

from wizwalker.extensions.scripting import teleport_to_friend_from_list

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


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.is_loading(),
          wizwalker.extensions.scripting.teleport_to_friend_from_list()

    Flow:
      1. Check every client's zone. No client in target_zone -> FAILURE.
      2. The first client in target_zone, in player order, is the target. Its wizard name
         must already be cached (ClientManager keeps this filled); if not -> FAILURE.
      3. Build clients_to_move: every client but the target. When skip_extra_clients_in_zone
         is set, a client already in target_zone (other than the target) is excluded too.
         Empty -> SUCCESS immediately, nothing to do.
      4. Call teleport_to_friend_from_list (wrapped in mouse_handler context) for all
         clients_to_move simultaneously. ValueError (friend not found/offline) -> FAILURE.
      5. "Ever seen" loading loop: on each tick, answer whatever modal each not-yet-loading
         client is showing — the dungeon exit and cross-realm warnings are confirmed so the
         port proceeds, the friend-busy dialog is dismissed and returns FAILURE. Record
         newly-loading clients. Re-attempt teleport for stragglers after TRIGGER_TIMEOUT has
         elapsed. Return FAILURE if ZONE_CHANGE_TIMEOUT expires.
      6. Wait for all loading screens to finish.
      7. Wait for every client (including the target and any skipped extras) to be in
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

    # 1. Find every client currently in target_zone, in player order.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current zones: {list(zones)}", logging.DEBUG)
    in_zone_indices = [i for i, z in enumerate(zones) if node_helpers.zone_matches(z, target_zone)]
    if not in_zone_indices:
        machine.log(f"FAILURE: no hooked client is currently in {target_zone!r}", logging.WARNING)
        return "FAILURE"

    # 2. First one found is the target.
    target_index = in_zone_indices[0]
    target_name = wizard_names.cached_name(clients[target_index])
    if not target_name:
        machine.log(
            f"FAILURE: P{target_index + 1}'s wizard name has not been detected yet",
            logging.WARNING,
        )
        return "FAILURE"
    friend_name = target_name.lower()
    machine.log(f"teleport_to_player_if_in_zone: target is P{target_index + 1}, in {target_zone!r}")
    machine.log(f"Resolved target name: {target_name!r}", logging.DEBUG)

    # 3. Build the set of clients to move.
    extra_in_zone = set(in_zone_indices[1:])
    clients_to_move = [
        c
        for i, c in enumerate(clients)
        if i != target_index and not (skip_extra_clients_in_zone and i in extra_in_zone)
    ]
    if not clients_to_move:
        machine.log(f"SUCCESS: no other clients need to teleport to {target_zone!r}")
        return "SUCCESS"
    machine.log(f"{len(clients_to_move)} of {len(clients)} client(s) will teleport", logging.DEBUG)

    # 4. Initiate teleport on all clients_to_move simultaneously.
    machine.log("Calling teleport_to_friend_from_list…", logging.DEBUG)
    try:
        await asyncio.gather(*[_teleport_with_mouse(c, friend_name) for c in clients_to_move])
    except ValueError as exc:
        machine.log(f"FAILURE: {exc}", logging.WARNING)
        return "FAILURE"

    # 5. "Ever seen" loading loop.
    seen_loading: set[int] = set()
    loop_start = asyncio.get_event_loop().time()
    straggler_after = loop_start + TRIGGER_TIMEOUT

    while True:
        for i, c in enumerate(clients_to_move):
            if i in seen_loading:
                continue
            outcome = await node_helpers.resolve_modal(c)
            if outcome.refusal:
                machine.log(
                    f"FAILURE: client {i} cannot reach the target (P{target_index + 1}) — "
                    f"{dialog_captions.describe(outcome.refusal)}",
                    logging.WARNING,
                )
                return "FAILURE"

        loading_flags = await asyncio.gather(*[c.is_loading() for c in clients_to_move])
        for i, is_now_loading in enumerate(loading_flags):
            if is_now_loading:
                seen_loading.add(i)

        if len(seen_loading) == len(clients_to_move):
            break

        now = asyncio.get_event_loop().time()
        if now - loop_start > ZONE_CHANGE_TIMEOUT:
            not_triggered = [i for i in range(len(clients_to_move)) if i not in seen_loading]
            machine.log(f"Clients not loading at timeout: {not_triggered}", logging.DEBUG)
            machine.log(
                f"FAILURE: {len(not_triggered)} client(s) did not enter a loading screen "
                f"within {ZONE_CHANGE_TIMEOUT:.0f}s",
                logging.WARNING,
            )
            return "FAILURE"

        if now >= straggler_after:
            stragglers = [c for i, c in enumerate(clients_to_move) if i not in seen_loading]
            try:
                await asyncio.gather(*[_teleport_with_mouse(c, friend_name) for c in stragglers])
            except ValueError as exc:
                machine.log(f"FAILURE: {exc} (during straggler re-attempt)", logging.WARNING)
                return "FAILURE"

        await asyncio.sleep(POLL_INTERVAL)

    # 6. Wait for all loading screens to finish.
    machine.log("Loading detected; waiting for screens to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients_to_move), None, POLL_INTERVAL)

    # 7. Verify all clients (including the target and any skipped extras) arrived.
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


async def _teleport_with_mouse(client, friend_name_lower: str) -> None:
    """Resolve the exact-case friend name, then teleport via the in-game friends list.

    teleport_to_friend_from_list uses a case-sensitive string comparison internally,
    so we must look up the exact-case name ourselves before delegating to it.
    The mouse handler context is activated here because both the resolution step and
    WizWalker's click chain require it.
    Raises ValueError if no friends are online or the name is not found.
    """
    async with client.mouse_handler:
        exact_name = await node_helpers.resolve_friend_name(client, friend_name_lower)
        await teleport_to_friend_from_list(client, name=exact_name)
