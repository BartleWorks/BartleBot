"""teleport_to_coordinates node — teleport all clients to an XYZ coordinate within the current zone."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.nodes import node_helpers
from bartlebot.nodes.node_helpers import POSITION_MARGIN
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import MAX_RETRIES

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
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
            "reached the target XYZ after several teleports a Safety Delay apart.",
        ),
    ],
    required_bot_options=[SAFETY_DELAY_OPTION],
    max_consecutive_retries=20,
)


async def _off_target(clients: list, target: XYZ) -> list[int]:
    """Indices of the clients farther than POSITION_MARGIN from target."""
    positions = await asyncio.gather(*[c.body.position() for c in clients])
    return [i for i, p in enumerate(positions) if node_helpers.distance(p, target) > POSITION_MARGIN]


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.body.position(), client.is_loading()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. asyncio.gather teleport all clients to the target XYZ.
      3. Check every client is within POSITION_MARGIN of the target. Any that are not get
         up to MAX_RETRIES attempts in all, a Safety Delay apart: a client that has only just
         entered the zone (a dungeon entrance, most often) reads as in it before it will
         accept a teleport. Each retry re-checks the zone, and skips a client still loading.

    FAILURE: clients not in zone at start, or one left it between attempts; one or more
    clients not within POSITION_MARGIN of the target XYZ after the last attempt.
    """
    zone: str = params["zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])
    safety_delay: float = machine.get_option("Safety Delay", 3.0)

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

    # 3. Verify all clients reached the target, retrying the ones that did not.
    off_target = await _off_target(clients, target)
    for attempt in range(2, MAX_RETRIES + 1):
        if not off_target:
            break
        machine.log(
            f"Client(s) {off_target} not at target XYZ yet; retrying in {safety_delay:.1f}s "
            f"(attempt {attempt}/{MAX_RETRIES})",
        )
        await asyncio.sleep(safety_delay)

        zones = await asyncio.gather(*[clients[i].zone_name() for i in off_target])
        left = [i for i, z in zip(off_target, zones) if not node_helpers.zone_matches(z, zone)]
        if left:
            machine.log(f"FAILURE: client(s) {left} left {zone!r} before reaching target XYZ", logging.WARNING)
            return "FAILURE"

        # A teleport issued mid-load pulls the client back out of the zone it lands in (#70).
        loading = await asyncio.gather(*[clients[i].is_loading() for i in off_target])
        ready = [i for i, busy in zip(off_target, loading) if not busy]
        await asyncio.gather(*[clients[i].teleport(target) for i in ready])
        off_target = await _off_target(clients, target)

    if off_target:
        machine.log(
            f"FAILURE: client(s) {off_target} did not reach target XYZ after {MAX_RETRIES} attempts "
            f"(margin={POSITION_MARGIN})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients at target XYZ in {zone!r}")
    return "SUCCESS"
