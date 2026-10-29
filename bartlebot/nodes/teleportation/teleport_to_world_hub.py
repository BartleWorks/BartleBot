"""teleport_to_world_hub node — teleport all clients to the current world's hub zone."""

import asyncio
import logging

from bartleby.runtime.game import dialog_captions
from bartleby.runtime.paths import HUB_BUTTON_PATH
from bartleby.runtime.timing import COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT

from bartlebot.nodes import node_helpers
from bartlebot.nodes.help_text import ARRIVAL_NOTE
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.options import player_realms

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Hub Zone",
        help=(
            "The hub zone every player must be in when the node ends. If every player is already "
            "there, the node succeeds at once."
        ),
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Teleports every player to their world's hub with the compass's hub button.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player is in Hub Zone."),
        ReturnSpec(
            "FAILURE",
            "The hub button stayed grayed, or not every player reached Hub Zone.",
        ),
    ],
    required_bot_options=[player_realms.SPEC],
    max_consecutive_retries=10,
    usage_notes=(ARRIVAL_NOTE),
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.is_loading(), node_helpers.click_by_path(),
          node_helpers.is_enabled_by_path()

    Flow:
      1. Check if all clients are already in zone. Return SUCCESS immediately if so.
      2. Filter to only clients not yet in zone.
      3. Wait for the hub teleport button to become enabled on clients to move. Return
         FAILURE if COMPASS_TELEPORT_TIMEOUT expires (30-second game cooldown).
      4. Click the hub teleport button on all clients to move.
      5. "Ever seen" loop: poll until all clients have entered a loading screen. For each
         straggler, confirm a dungeon exit warning if one is up, otherwise re-click the
         button. A client that never enters a loading screen within TRIGGER_TIMEOUT is not
         failed here — see step 7.
      6. Wait for all loading screens to finish.
      7. Wait for all clients (including those already in zone) to arrive in zone, trying a
         teleport-to-player recovery for any straggler the moment a groupmate arrives. Return
         FAILURE if any are still not in zone once that is exhausted.

    FAILURE: hub teleport button not enabled within timeout; not all clients arrived in the
    hub zone after the recovery-aware arrival check.
    """
    zone: str = params["zone"]

    machine.log(f"teleport_to_world_hub: target zone {zone!r}")

    # 1. Skip if already in hub zone.
    zones = await node_helpers.read_zones(clients, poll_interval=POLL_INTERVAL)
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if all(node_helpers.zone_matches(z, zone) for z in zones):
        machine.log("SUCCESS: clients already in hub zone")
        return "SUCCESS"

    # Only teleport clients that aren't already in the hub zone.
    clients_to_move = [c for c, z in zip(clients, zones) if not node_helpers.zone_matches(z, zone)]
    machine.log(
        f"{len(clients_to_move)} of {len(clients)} client(s) need to teleport",
        logging.DEBUG,
    )

    # 2. Wait for hub button to become enabled (30s cooldown after last use).
    machine.log("Waiting for hub teleport button to become enabled…", logging.DEBUG)
    if not await node_helpers.poll_until(
        lambda: _all_hub_button_enabled(clients_to_move), COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL
    ):
        machine.log(
            f"FAILURE: hub teleport button did not become enabled within {COMPASS_TELEPORT_TIMEOUT}s",
            logging.WARNING,
        )
        return "FAILURE"

    # Who shares whose realm, for straggler recovery in a Separate bot. The hub keeps realms.
    await node_helpers.note_realm_peers(machine, clients)

    # 3. Click the hub button on clients that need to move; re-click for any that haven't started loading.
    machine.log("Clicking hub teleport button…", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, HUB_BUTTON_PATH) for c in clients_to_move])

    # Clients teleporting out of a dungeon are held behind the exit warning, which no
    # amount of re-clicking the hub button will clear — answer it first each tick. A
    # refusal (a gauntlet, say) ends the wait instead of running it out.
    retry = node_helpers.ModalAwareRetry(lambda c: node_helpers.click_by_path(c, HUB_BUTTON_PATH))
    seen_loading = await node_helpers.poll_each_until_seen(
        clients_to_move,
        check_fn=lambda c: c.is_loading(),
        action_fn=retry,
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    if retry.refusal:
        machine.log(
            f"FAILURE: hub teleport refused — {dialog_captions.describe(retry.refusal)}",
            logging.WARNING,
        )
        return "FAILURE"
    if len(seen_loading) < len(clients_to_move):
        not_loading_idx = [i for i in range(len(clients_to_move)) if i not in seen_loading]
        machine.log(f"Clients not loading at timeout: {not_loading_idx}", logging.DEBUG)
        machine.log(
            f"{len(not_loading_idx)} client(s) did not enter a loading screen within "
            f"{TRIGGER_TIMEOUT}s; continuing to the recovery-aware arrival check",
            logging.DEBUG,
        )

    # 4. Wait for loading screens to finish.
    machine.log("Loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients_to_move), None, POLL_INTERVAL)

    # 5. Wait for all clients to arrive in the expected hub zone.
    if (
        actual := await node_helpers.wait_for_zone_with_recovery(
            machine, clients, zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
        )
    ) is not None:
        machine.log(f"Zones after hub teleport: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients in expected hub zone (expected={zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {zone!r}")
    return "SUCCESS"


async def _all_hub_button_enabled(clients: list) -> bool:
    """Return True when the hub teleport button is enabled for every client."""
    results = await asyncio.gather(*[node_helpers.is_enabled_by_path(c, HUB_BUTTON_PATH) for c in clients])
    return all(results)
