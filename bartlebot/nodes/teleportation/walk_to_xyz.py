"""walk_to_xyz node — walk every client to a spot in the zone it is in."""

import logging

from bartleby.runtime.timing import POLL_INTERVAL

from bartlebot.nodes import node_helpers, walking
from bartlebot.nodes.help_text import ZONE_AT_START
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="zone",
        type="zone",
        label="Zone",
        help=ZONE_AT_START,
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="XYZ",
        help="Where in Zone to walk players to.",
        default=None,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Walks every player to a spot in the current zone.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player stands at XYZ."),
        ReturnSpec(
            "FAILURE",
            "A player wasn't in Zone at the start, or couldn't walk to XYZ, such as past a closed gate.",
        ),
    ],
    required_bot_options=[walking.AVOID_MOBS_OPTION],
    max_consecutive_retries=20,
    usage_notes=(
        "Players walk the way the zone's map plans, each from where it stands. A walk that meets a "
        "closed gate fails and names it: put a Teleport to XYZ there to hop past it."
    ),
    see_also=["teleport_to_xyz"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), walking.walk_all()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Walk every client to XYZ at once, each from where it stands.

    FAILURE: clients not in zone at the start; a walk that did not arrive, logged by
    walk_clients with where it ended and the closed gate in the way when one is why.
    """
    zone: str = params["zone"]
    target = walking.point(_xyz(params["xyz"]))

    machine.log(f"walk_to_xyz: {zone!r} → ({target[0]:.1f}, {target[1]:.1f}, {target[2]:.1f})")

    if not await node_helpers.in_expected_zone(machine, clients, zone, POLL_INTERVAL):
        return "FAILURE"

    if not await walking.walk_all(machine, clients, target):
        machine.log("FAILURE: not every player could walk to XYZ", logging.WARNING)
        return "FAILURE"

    machine.log(f"SUCCESS: every player at XYZ in {zone!r}")
    return "SUCCESS"


def _xyz(value: dict) -> tuple[float, float, float]:
    return (value["x"], value["y"], value["z"])
