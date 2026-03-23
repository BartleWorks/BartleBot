"""go_to_xyz node — teleport all clients to an XYZ coordinate within the current zone."""

import asyncio
import math

from wizwalker import XYZ

import logging
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

POSITION_MARGIN = 10.0  # units; teleport is direct so any deviation indicates an issue

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="str",
        label="Zone",
        help="Zone name all clients must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Target XYZ",
        help="Coordinates within zone to teleport clients to.",
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Teleports all clients to a specific XYZ coordinate within the current zone.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients are now at the target XYZ within the expected zone."),
        ReturnSpec(
            "FAILURE",
            "Clients were not in the expected zone at the start, or not all clients "
            "reached the target XYZ after teleporting.",
        ),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    Teleport all clients to an XYZ coordinate within the current zone.

    Params:
        zone (str): Zone clients must be in when this node is called.
        xyz (dict): {"x": float, "y": float, "z": float} — target coordinates.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(
        f"go_to_xyz: {zone!r} → XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})"
    )

    # 1. Verify all clients are in zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(z == zone for z in zones):
        wrong = [z for z in zones if z != zone]
        machine.log(
            f"FAILURE: clients not in expected zone "
            f"(expected={zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    # 2. Teleport all clients to the target XYZ.
    machine.log(
        f"Teleporting {len(clients)} client(s) to "
        f"XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})",
        logging.DEBUG,
    )
    await asyncio.gather(*[c.teleport(target) for c in clients])

    # 3. Verify all clients reached the target.
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    machine.log(
        f"Positions after teleport: {[(round(p.x, 1), round(p.y, 1), round(p.z, 1)) for p in positions]}",
        logging.DEBUG,
    )
    off_target = [
        i for i, p in enumerate(positions) if _distance(p, target) > POSITION_MARGIN
    ]
    if off_target:
        machine.log(
            f"FAILURE: client(s) {off_target} did not reach target XYZ "
            f"(margin={POSITION_MARGIN})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients at target XYZ in {zone!r}")
    return "SUCCESS"


def _distance(a: XYZ, b: XYZ) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)
