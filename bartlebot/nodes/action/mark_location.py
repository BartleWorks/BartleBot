"""mark_location node — mark the current in-game location with failsafe handling."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import MARK_BUTTON_PATH, RECALL_BUTTON_PATH, RECALL_TIMER_PATH
from bartlebot.nodes.timing import COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL, jitter

POST_MARK_WAIT = 2.0  # seconds to wait for UI to reflect mark state after PAGE_DOWN
STABILIZE_TIMEOUT = 5.0  # seconds to wait for server position to match teleport target

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all clients must be in when this node executes. "
        "If clients are in the correct zone but at the wrong XYZ, they will be teleported.",
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Mark XYZ",
        help="Coordinates to teleport clients to before marking, if not already there.",
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Marks the current in-game location by clicking the MarkButton in the compass HUD. "
        "Handles the 30-second mark cooldown timer and the edge case where marking the "
        "same location as an existing mark clears it rather than resetting it."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients have successfully marked their location."),
        ReturnSpec(
            "FAILURE",
            "One or more clients had no mana; clients were not in the expected zone; "
            "or the mark cooldown timer did not clear within the timeout.",
        ),
    ],
    max_consecutive_retries=5,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    Mark the current in-game location for all clients.

    Params:
        zone (str): Zone clients must be in when this node is called.
        xyz (dict): {"x": float, "y": float, "z": float} — location to mark.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(f"mark_location: zone={zone!r} XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})")

    # 1. Verify all clients have mana to cast mark location.
    mana_ratios = await asyncio.gather(*[c.calc_mana_ratio() for c in clients])
    machine.log(f"Mana ratios: {[round(r, 2) for r in mana_ratios]}", logging.DEBUG)
    if any(r <= 0.0 for r in mana_ratios):
        machine.log("FAILURE: one or more clients have no mana to mark location", logging.WARNING)
        return "FAILURE"

    # 2. Verify zone; teleport to xyz if needed.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(z == zone for z in zones):
        wrong = [z for z in zones if z != zone]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    positions = await asyncio.gather(*[c.body.position() for c in clients])
    if any(_distance(p, target) > 1.0 for p in positions):
        machine.log(
            f"Teleporting clients to mark XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(target) for c in clients])
        # Wait for the server-reported position to stabilize before pressing PAGE_DOWN.
        # Without this, a sigil's NPC range event can fire between the teleport write and
        # the PAGE_DOWN, causing the mark to be set at the server's adjusted position.
        machine.log("Waiting for client positions to stabilize after teleport…", logging.DEBUG)
        await asyncio.gather(*[_wait_stabilized(machine, c, target) for c in clients])

    # 3. Mark each client, handling the cooldown timer and same-spot edge case.
    results = await asyncio.gather(*[_mark_client(machine, c) for c in clients])
    if not all(results):
        machine.log("FAILURE: one or more clients failed to set a mark", logging.WARNING)
        return "FAILURE"

    machine.log("SUCCESS: all clients have marked their location")
    return "SUCCESS"


async def _mark_client(machine, client) -> bool:
    """Mark location for a single client. Returns True if mark was confirmed set."""
    # Wait for any existing mark cooldown to expire.
    machine.log("Waiting for mark cooldown to clear…", logging.DEBUG)
    if not await node_helpers.poll_until(lambda: _timer_is_clear(client), COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL):
        machine.log(
            f"FAILURE: mark cooldown did not clear within {COMPASS_TELEPORT_TIMEOUT}s",
            logging.WARNING,
        )
        return False

    # Click the MarkButton to mark location.
    await node_helpers.click_by_path(client, MARK_BUTTON_PATH)
    await asyncio.sleep(jitter(POST_MARK_WAIT))

    # Check if the RecallButton is now enabled (mark successfully set).
    recall_enabled = await node_helpers.is_enabled_by_path(client, RECALL_BUTTON_PATH)
    if recall_enabled:
        return True

    # Edge case: client had an existing mark at this exact spot. Clicking MarkButton
    # cleared it instead of resetting it. Wait for the cooldown again and re-mark.
    machine.log(
        "RecallButton grayed after marking — same-spot mark cleared; waiting to re-mark…",
        logging.DEBUG,
    )
    if not await node_helpers.poll_until(lambda: _timer_is_clear(client), COMPASS_TELEPORT_TIMEOUT, POLL_INTERVAL):
        machine.log(
            f"FAILURE: re-mark cooldown did not clear within {COMPASS_TELEPORT_TIMEOUT}s",
            logging.WARNING,
        )
        return False

    await node_helpers.click_by_path(client, MARK_BUTTON_PATH)
    await asyncio.sleep(jitter(POST_MARK_WAIT))

    # Final check.
    return await node_helpers.is_enabled_by_path(client, RECALL_BUTTON_PATH)


async def _wait_stabilized(machine, client, target: XYZ) -> None:
    """Poll until the server-reported position is within 1.0 unit of target."""
    if not await node_helpers.poll_until(lambda: _position_near(client, target), STABILIZE_TIMEOUT, POLL_INTERVAL):
        machine.log(
            f"Position did not stabilize within {STABILIZE_TIMEOUT}s after teleport",
            logging.DEBUG,
        )


async def _position_near(client, target: XYZ) -> bool:
    """Return True when the server-reported position is within 1.0 unit of target."""
    pos = await client.body.position()
    return _distance(pos, target) < 1.0


async def _timer_is_clear(client) -> bool:
    """Return True when the recall timer text is empty (no mark cooldown active)."""
    window = await node_helpers._get_window_from_path(client.root_window, RECALL_TIMER_PATH)
    if window is None:
        return True  # Timer element absent means no cooldown
    text = await window.maybe_text()
    return text == "" or text is None


def _distance(a: XYZ, b: XYZ) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5
