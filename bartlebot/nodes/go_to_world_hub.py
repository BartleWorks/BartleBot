"""go_to_world_hub node — teleport all clients to the current world's hub zone."""

import asyncio

from wizwalker import Keycode

from bartlebot.log_level import LogLevel
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

HUB_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "GoHomeButton"]

BUTTON_TIMEOUT = 35.0   # 30s cooldown + 5s safety margin
TRIGGER_TIMEOUT = 10.0  # seconds to wait for loading to start after pressing END
POLL_INTERVAL = 0.25

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
    description="Teleports all clients to the hub zone for their current world using the END key.",
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
)


async def run(machine, clients: list, params: dict) -> str:
    """
    Teleport all clients to the current world's hub zone.

    Params:
        zone (str): Expected hub zone name. If clients are already here, returns SUCCESS
                    immediately. Used to verify arrival after loading.
    """
    zone: str = params["zone"]

    machine.log(f"go_to_world_hub: target zone {zone!r}")

    # 1. Skip if already in hub zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", LogLevel.DEBUG)
    if all(z == zone for z in zones):
        machine.log("SUCCESS: clients already in hub zone")
        return "SUCCESS"

    # 2. Wait for hub button to become enabled (30s cooldown after last use).
    machine.log("Waiting for hub teleport button to become enabled…", LogLevel.DEBUG)
    if not await node_helpers.wait_until(
        lambda: _all_hub_button_enabled(clients), BUTTON_TIMEOUT, POLL_INTERVAL
    ):
        machine.log(
            f"FAILURE: hub teleport button did not become enabled within {BUTTON_TIMEOUT}s",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 3. Press END on all clients; re-press for any client that hasn't started loading.
    machine.log("Pressing END to teleport to hub…", LogLevel.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.END, 0.1) for c in clients])

    loop_start = asyncio.get_event_loop().time()
    while True:
        loading = await asyncio.gather(*[c.is_loading() for c in clients])
        if all(loading):
            break
        if asyncio.get_event_loop().time() - loop_start > TRIGGER_TIMEOUT:
            not_loading_idx = [i for i, l in enumerate(loading) if not l]
            machine.log(f"Clients not loading at timeout: {not_loading_idx}", LogLevel.DEBUG)
            machine.log(
                f"FAILURE: clients did not enter loading screen within {TRIGGER_TIMEOUT}s",
                LogLevel.WARNING,
            )
            return "FAILURE"
        not_loading_clients = [c for c, l in zip(clients, loading) if not l]
        await asyncio.gather(*[c.send_key(Keycode.END, 0.1) for c in not_loading_clients])
        await asyncio.sleep(POLL_INTERVAL)

    # 4. Wait for loading screens to finish.
    machine.log("Loading screen active, waiting for it to clear…", LogLevel.DEBUG)
    await node_helpers.wait_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 5. Verify all clients arrived in the expected hub zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Zones after hub teleport: {list(zones)}", LogLevel.DEBUG)
    if not all(z == zone for z in zones):
        actual = list(set(zones))
        machine.log(
            f"FAILURE: not all clients in expected hub zone (expected={zone!r}, got={actual})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {zone!r}")
    return "SUCCESS"


async def _all_hub_button_enabled(clients: list) -> bool:
    """Return True when the hub teleport button is enabled for every client."""
    results = await asyncio.gather(
        *[node_helpers.is_enabled_by_path(c, HUB_BUTTON_PATH) for c in clients]
    )
    return all(results)
