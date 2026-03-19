import asyncio

from wizwalker import XYZ

from bartlebot.log_level import LogLevel
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="str",
        label="From Zone",
        help="Zone name the clients must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="to_zone",
        type="str",
        label="To Zone",
        help="Zone name clients must be in after this node completes.",
        default="",
        required=True,
    ),
    ParamField(
        key="xyz",
        type="xyz",
        label="Transition XYZ",
        help=(
            "A point inside the destination zone's transition bounding box.\n"
            "Teleporting clients here triggers the automatic zone change."
        ),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Transitions all clients from one zone to another by teleporting them "
        "into the destination zone's transition bounding box."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "All clients are now in to_zone.",
        ),
        ReturnSpec(
            "FAILURE",
            "Clients were not in from_zone at the start, timed out leaving "
            "the zone, or not all clients reached to_zone after the transition.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            description=(
                "Seconds to wait after triggering an interaction or dialogue "
                "before checking for a response window."
            ),
        ),
    ],
)

ZONE_CHANGE_TIMEOUT = 30.0
POLL_INTERVAL = 0.25


async def run(machine, clients: list, params: dict) -> str:
    """
    Transition all clients from one zone to another.

    Params:
        from_zone (str): Zone clients must be in when this node is called.
        to_zone (str):   Zone clients must be in when this node completes.
        xyz (dict):      {"x": float, "y": float, "z": float} — a point inside
                         the destination zone's transition bounding box.
                         Teleporting clients here triggers the zone change automatically.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    machine.log(
        f"go_to_zone: {from_zone!r} → {to_zone!r} "
        f"via XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})"
    )

    # 1. Verify all clients are in from_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", LogLevel.DEBUG)
    if not all(z == from_zone for z in zones):
        wrong = [z for z in zones if z != from_zone]
        machine.log(
            f"FAILURE: clients not in expected zone "
            f"(expected={from_zone!r}, unexpected={wrong})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 2. Teleport all clients into the transition bounding box.
    machine.log(f"Teleporting {len(clients)} client(s) to transition bounding box", LogLevel.DEBUG)
    await asyncio.gather(*[c.teleport(target) for c in clients])

    # 2b. Dismiss dungeon exit warning if it appears (triggered when leaving a dungeon zone).
    machine.log("Checking for dungeon exit warning…", LogLevel.DEBUG)
    await asyncio.gather(*[node_helpers.confirm_dialog_if_visible(c, machine.get_option("Safety Delay", 3.0)) for c in clients])

    # 3. Wait for all clients to leave from_zone (zone change triggered by bounding box).
    machine.log(f"Waiting for clients to leave {from_zone!r}…", LogLevel.DEBUG)
    if not await node_helpers.wait_until(
        lambda: node_helpers.all_left_zone(clients, from_zone), ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
    ):
        zones_at_timeout = await asyncio.gather(*[c.zone_name() for c in clients])
        machine.log(f"Client zones at timeout: {list(zones_at_timeout)}", LogLevel.DEBUG)
        machine.log(
            f"FAILURE: timed out after {ZONE_CHANGE_TIMEOUT}s waiting for clients "
            f"to leave {from_zone!r}",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 4. Wait for loading screens to finish.
    machine.log("Zone transition triggered, waiting for loading screen to clear…", LogLevel.DEBUG)
    await node_helpers.wait_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 5. Wait for all clients to arrive in to_zone.
    if (actual := await node_helpers.wait_for_zone(clients, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)) is not None:
        machine.log(f"Zones after transition: {actual}", LogLevel.DEBUG)
        machine.log(
            f"FAILURE: clients not in expected zone after transition "
            f"(expected={to_zone!r}, got={list(set(actual))})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"

