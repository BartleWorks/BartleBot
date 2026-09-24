"""teleport_to_coordinates node — teleport all clients to an XYZ coordinate within the current zone."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.node_helpers import POSITION_MARGIN
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Target XYZ",
        help="Coordinates within zone to teleport players to.",
        default=None,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Teleports all players to a specific XYZ coordinate within the current zone.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now at the target XYZ within the expected zone."),
        ReturnSpec(
            "FAILURE",
            "Players were not in the expected zone at the start, or not all players "
            "reached the target XYZ after teleporting.",
        ),
    ],
    max_consecutive_retries=20,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.body.position()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. asyncio.gather teleport all clients to the target XYZ.
      3. Verify all clients' positions are within POSITION_MARGIN of the target.
         Return FAILURE if any client is too far from the target.

    FAILURE: clients not in zone at start; one or more clients not within POSITION_MARGIN
    of the target XYZ after teleporting.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(f"teleport_to_coordinates: {zone!r} → XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})")

    # 1. Verify all clients are in zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(node_helpers.zone_matches(z, zone) for z in zones):
        wrong = [z for z in zones if not node_helpers.zone_matches(z, zone)]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Teleport all clients to the target XYZ.
    machine.log(
        f"Teleporting {len(clients)} client(s) to XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})",
        logging.DEBUG,
    )
    await asyncio.gather(*[c.teleport(target) for c in clients])

    # 3. Verify all clients reached the target.
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    machine.log(
        f"Positions after teleport: {[(round(p.x, 1), round(p.y, 1), round(p.z, 1)) for p in positions]}",
        logging.DEBUG,
    )
    off_target = [i for i, p in enumerate(positions) if node_helpers.distance(p, target) > POSITION_MARGIN]
    if off_target:
        machine.log(
            f"FAILURE: client(s) {off_target} did not reach target XYZ (margin={POSITION_MARGIN})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients at target XYZ in {zone!r}")
    return "SUCCESS"
