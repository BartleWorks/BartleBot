"""teleport_to_world_hub node — teleport all clients to the current world's hub zone."""

import asyncio
import logging

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import HUB_BUTTON_PATH
from bartlebot.nodes.timing import COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL, TRIGGER_TIMEOUT, ZONE_CHANGE_TIMEOUT

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Hub Zone",
        help="The hub zone name clients must be in when this node completes. "
        "If all clients are already in this zone, the node returns SUCCESS immediately.",
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Teleports all clients to the hub zone for their current world by clicking the hub teleport button in the HUD compass.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients are now in the hub zone."),
        ReturnSpec(
            "FAILURE",
            "The hub teleport button did not become enabled within the timeout, "
            "clients did not enter a loading screen after pressing END, "
            "or not all clients arrived in the expected hub zone.",
        ),
    ],
    max_consecutive_retries=10,
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
      5. "Ever seen" loop: poll until all clients have entered a loading screen, re-clicking
         the button for stragglers. Return FAILURE if TRIGGER_TIMEOUT expires.
      6. Wait for all loading screens to finish.
      7. Wait for all clients (including those already in zone) to arrive in zone.
         Return FAILURE if any are not.

    FAILURE: hub teleport button not enabled within timeout; clients did not enter a loading
    screen within TRIGGER_TIMEOUT; not all clients arrived in the hub zone.
    """
    zone: str = params["zone"]

    machine.log(f"teleport_to_world_hub: target zone {zone!r}")

    # 1. Skip if already in hub zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if all(z == zone for z in zones):
        machine.log("SUCCESS: clients already in hub zone")
        return "SUCCESS"

    # Only teleport clients that aren't already in the hub zone.
    clients_to_move = [c for c, z in zip(clients, zones) if z != zone]
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

    # 3. Click the hub button on clients that need to move; re-click for any that haven't started loading.
    machine.log("Clicking hub teleport button…", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, HUB_BUTTON_PATH) for c in clients_to_move])

    seen_loading = await node_helpers.poll_each_until_seen(
        clients_to_move,
        check_fn=lambda c: c.is_loading(),
        action_fn=lambda c: node_helpers.click_by_path(c, HUB_BUTTON_PATH),
        timeout=TRIGGER_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    if len(seen_loading) < len(clients_to_move):
        not_loading_idx = [i for i in range(len(clients_to_move)) if i not in seen_loading]
        machine.log(f"Clients not loading at timeout: {not_loading_idx}", logging.DEBUG)
        machine.log(
            f"FAILURE: clients did not enter loading screen within {TRIGGER_TIMEOUT}s",
            logging.WARNING,
        )
        return "FAILURE"

    # 4. Wait for loading screens to finish.
    machine.log("Loading screen active, waiting for it to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients_to_move), None, POLL_INTERVAL)

    # 5. Wait for all clients to arrive in the expected hub zone.
    if (actual := await node_helpers.wait_for_zone(clients, zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)) is not None:
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
