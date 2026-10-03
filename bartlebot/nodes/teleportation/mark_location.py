"""mark_location node — mark the current in-game location with failsafe handling."""

import asyncio
import logging
from dataclasses import dataclass

from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.node_helpers import POSITION_MARGIN
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import MARK_BUTTON_PATH, RECALL_BUTTON_PATH
from bartlebot.nodes.timing import (
    POLL_INTERVAL,
    POSITION_STABILIZE_TIMEOUT,
    POST_MARK_WAIT,
    jitter,
)

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone",
        help="Zone name all players must be in when this node executes. "
        "If players are in the correct zone but at the wrong XYZ, they will be teleported.",
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Mark XYZ",
        help="Coordinates to teleport players to before marking, if not already there.",
        default=None,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Marks the current in-game location by clicking the MarkButton in the compass HUD. "
        "A player who already marked this spot earlier in the run is left alone, so a bot can "
        "pass through this node every loop. Handles the edge case where marking the same "
        "location as an existing mark clears it rather than resetting it."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players have a mark here: set now, or set earlier in this run."),
        ReturnSpec(
            "FAILURE",
            "One or more players had no mana; players were not in the expected zone; "
            "or the mark was not set after a second click.",
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Marking has no cooldown of its own. The timer under the Recall button is the cooldown the "
        "hub button, HOME and recall share, and it does not hold up a mark."
    ),
)

#: Where each client last marked in this run, in ``machine.node_scratch``. Every mark goes
#: through :func:`mark_client`, handle_level_scaling's included, so the record never names a
#: mark that has since been replaced.
MARKS_KEY = "marks"


@dataclass(frozen=True)
class Mark:
    """Where one client stood when it marked: what its recall is checked against."""

    zone: str
    position: XYZ


def marked_at(machine, client) -> Mark | None:
    """Where `client` last marked in this run, or None if it has not."""
    for marked, mark in machine.node_scratch.get(MARKS_KEY, ()):
        if marked is client:
            return mark
    return None


def _remember(machine, client, mark: Mark) -> None:
    kept = [(c, m) for c, m in machine.node_scratch.get(MARKS_KEY, ()) if c is not client]
    machine.node_scratch[MARKS_KEY] = [*kept, (client, mark)]


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.calc_mana_ratio(),
          client.body.position(), node_helpers.click_by_path()

    Flow:
      1. Leave out every client that already marked this spot in this run and still has a
         mark (its RecallButton is enabled). SUCCESS at once if that is every client.
      2. Verify the rest have mana (ratio > 0.0). Return FAILURE if any do not.
      3. Verify all clients are in zone. Return FAILURE if not.
      4. Teleport any of the rest not already at xyz to the mark position, then wait for the
         server-reported position to stabilize before marking.
      5. Click MarkButton on each of the rest.
      6. If RecallButton is still grayed after marking, the same-spot edge case triggered —
         the existing mark was cleared. Click MarkButton a second time to re-set the mark.

    FAILURE: any client left to mark has no mana; clients not in zone; a mark was still not
    set after the second click.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(f"mark_location: zone={zone!r} XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})")

    # 1. A mark set here earlier in the run stands: a recall does not use it up.
    kept = await asyncio.gather(*[_still_marked_here(machine, c, zone, target) for c in clients])
    to_mark = [c for c, ok in zip(clients, kept) if not ok]
    if not to_mark:
        machine.log("SUCCESS: every client already marked this spot in this run")
        return "SUCCESS"

    # 2. Verify the clients that will mark have mana to cast mark location.
    mana_ratios = await asyncio.gather(*[c.calc_mana_ratio() for c in to_mark])
    machine.log(f"Mana ratios: {[round(r, 2) for r in mana_ratios]}", logging.DEBUG)
    if any(r <= 0.0 for r in mana_ratios):
        machine.log("FAILURE: one or more clients have no mana to mark location", logging.WARNING)
        return "FAILURE"

    # 3. Verify zone; teleport to xyz if needed.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    positions = await asyncio.gather(*[c.body.position() for c in to_mark])
    away = [c for c, p in zip(to_mark, positions) if _distance(p, target) > 1.0]
    if away:
        machine.log(
            f"Teleporting clients to mark XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(target) for c in away])
        # Wait for the server-reported position to stabilize before pressing PAGE_DOWN.
        # Without this, a sigil's NPC range event can fire between the teleport write and
        # the PAGE_DOWN, causing the mark to be set at the server's adjusted position.
        machine.log("Waiting for client positions to stabilize after teleport…", logging.DEBUG)
        await asyncio.gather(*[_wait_stabilized(machine, c, target) for c in away])

    # 5 and 6. Mark each client, handling the same-spot edge case.
    results = await asyncio.gather(*[mark_client(machine, c) for c in to_mark])
    if not all(results):
        machine.log("FAILURE: one or more clients failed to set a mark", logging.WARNING)
        return "FAILURE"

    machine.log("SUCCESS: all clients have marked their location")
    return "SUCCESS"


async def mark_client(machine, client) -> bool:
    """Mark location for a single client, where it stands. Returns True if mark was confirmed set.

    Public: handle_level_scaling marks each client wherever it is, with no recorded spot. A mark
    that is set is remembered for the rest of the run (:func:`marked_at`).
    """
    # No wait first: marking has no cooldown. The timer under the RecallButton is the teleport
    # cooldown the hub button, HOME and recall share.
    await node_helpers.click_by_path(client, MARK_BUTTON_PATH)
    await asyncio.sleep(jitter(POST_MARK_WAIT))

    # Check if the RecallButton is now enabled (mark successfully set).
    if not await node_helpers.is_enabled_by_path(client, RECALL_BUTTON_PATH):
        # Edge case: client had an existing mark at this exact spot. Clicking MarkButton
        # cleared it instead of resetting it, so click again to re-mark.
        machine.log("RecallButton grayed after marking — same-spot mark cleared; marking again…", logging.DEBUG)
        await node_helpers.click_by_path(client, MARK_BUTTON_PATH)
        await asyncio.sleep(jitter(POST_MARK_WAIT))
        if not await node_helpers.is_enabled_by_path(client, RECALL_BUTTON_PATH):
            return False

    zone, position = await asyncio.gather(client.zone_name(), client.body.position())
    _remember(machine, client, Mark(zone, position))
    return True


async def _still_marked_here(machine, client, zone: str, target: XYZ) -> bool:
    """Whether `client` marked this spot earlier in the run and the mark is still there."""
    mark = marked_at(machine, client)
    if mark is None or not node_helpers.zone_matches(mark.zone, zone):
        return False
    if _distance(mark.position, target) > POSITION_MARGIN:
        return False
    # Cleared by hand, or by a same-spot click outside this run's record.
    return await node_helpers.is_enabled_by_path(client, RECALL_BUTTON_PATH)


async def _wait_stabilized(machine, client, target: XYZ) -> None:
    """Poll until the server-reported position is within 1.0 unit of target."""
    if not await node_helpers.poll_until(
        lambda: _position_near(client, target), POSITION_STABILIZE_TIMEOUT, POLL_INTERVAL
    ):
        machine.log(
            f"Position did not stabilize within {POSITION_STABILIZE_TIMEOUT}s after teleport",
            logging.DEBUG,
        )


async def _position_near(client, target: XYZ) -> bool:
    """Return True when the server-reported position is within 1.0 unit of target."""
    pos = await client.body.position()
    return _distance(pos, target) < 1.0


def _distance(a: XYZ, b: XYZ) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5
