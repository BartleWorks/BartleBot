"""teleport_to_xyz node — walk every client to a spot, then hop each a short way past a gate."""

import asyncio
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
        key="start_xyz",
        type="xyz",
        label="Start XYZ",
        help="Where players walk to first, just before the gate.",
        default=None,
        required=True,
    ),
    ParamField(
        key="end_xyz",
        type="xyz",
        label="End XYZ",
        help=f"Where players land, just past the gate: at most {walking.HOP_SPAN:.0f} game units from Start XYZ.",
        default=None,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Walks every player to a spot, then teleports each a short way, past a closed gate.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "Every player stands at End XYZ."),
        ReturnSpec(
            "FAILURE",
            (
                "A player wasn't in Zone at the start, couldn't walk to Start XYZ, or was put back by "
                "the game after the teleport."
            ),
        ),
    ],
    required_bot_options=[walking.AVOID_MOBS_OPTION],
    max_consecutive_retries=5,
    usage_notes=(
        f"Only for getting past something a walk can't, such as a closed gate. Start XYZ and End XYZ "
        f"may lie at most {walking.HOP_SPAN:.0f} game units apart, and a player is never moved farther "
        f"than {walking.HOP_MAX:.0f}. A teleport the game undoes isn't tried again."
    ),
    see_also=["walk_to_xyz"],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), walking.walk_all(), walking.hop()

    Flow:
      1. Verify all clients are in zone. Return FAILURE if not.
      2. Walk every client to Start XYZ at once.
      3. Hop every client to End XYZ at once, each with walking.hop's checks.

    FAILURE: clients not in zone at the start; a walk to Start XYZ that did not arrive; a
    hop refused or undone, logged by walking.hop. A start and end farther apart than
    walking.HOP_SPAN never runs: the editor marks the node and Run refuses the bot.
    """
    zone: str = params["zone"]
    start = walking.point(_xyz(params["start_xyz"]))
    end = walking.point(_xyz(params["end_xyz"]))

    machine.log(
        f"teleport_to_xyz: {zone!r} ({start[0]:.1f}, {start[1]:.1f}, {start[2]:.1f}) → "
        f"({end[0]:.1f}, {end[1]:.1f}, {end[2]:.1f})"
    )

    if not await node_helpers.in_expected_zone(machine, clients, zone, POLL_INTERVAL):
        return "FAILURE"

    if not await walking.walk_all(machine, clients, start):
        machine.log("FAILURE: not every player could walk to Start XYZ", logging.WARNING)
        return "FAILURE"

    landed = await asyncio.gather(
        *[walking.hop(machine, c, zone, start, end, number=n) for n, c in enumerate(clients, start=1)]
    )
    if not all(landed):
        failed = [n for n, ok in enumerate(landed, start=1) if not ok]
        machine.log(f"FAILURE: P{', P'.join(map(str, failed))} did not reach End XYZ", logging.WARNING)
        return "FAILURE"

    machine.log(f"SUCCESS: every player at End XYZ in {zone!r}")
    return "SUCCESS"


def _xyz(value: dict) -> tuple[float, float, float]:
    return (value["x"], value["y"], value["z"])
