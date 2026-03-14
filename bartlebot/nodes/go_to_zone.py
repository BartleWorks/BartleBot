import asyncio

from wizwalker import XYZ

from bartlebot.log_level import LogLevel
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="str",
        label="From Zone",
        help="Zone name the clients must currently be in when this node executes.",
        default="",
    ),
    ParamField(
        key="to_zone",
        type="str",
        label="To Zone",
        help="Zone name clients must be in after this node completes.",
        default="",
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

    # 3. Wait for all clients to leave from_zone (zone change triggered by bounding box).
    machine.log(f"Waiting for clients to leave {from_zone!r}…", LogLevel.DEBUG)
    if not await _wait_until(lambda: _all_left_zone(clients, from_zone), ZONE_CHANGE_TIMEOUT):
        machine.log(
            f"FAILURE: timed out after {ZONE_CHANGE_TIMEOUT}s waiting for clients "
            f"to leave {from_zone!r}",
            LogLevel.WARNING,
        )
        return "FAILURE"

    # 4. Wait for loading screens to finish.
    machine.log("Zone transition triggered, waiting for loading screen to clear…", LogLevel.DEBUG)
    await _wait_until(lambda: _none_loading(clients), timeout=None)

    # 5. Verify all clients arrived in to_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Zones after transition: {list(zones)}", LogLevel.DEBUG)
    if not all(z == to_zone for z in zones):
        actual = list(set(zones))
        machine.log(
            f"FAILURE: clients not in expected zone after transition "
            f"(expected={to_zone!r}, got={actual})",
            LogLevel.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"


async def _wait_until(condition_coro, timeout: float | None) -> bool:
    """Poll condition_coro every POLL_INTERVAL until it returns True or timeout expires."""
    async def _poll():
        while not await condition_coro():
            await asyncio.sleep(POLL_INTERVAL)

    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _all_left_zone(clients: list, zone: str) -> bool:
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(z != zone for z in zones)


async def _none_loading(clients: list) -> bool:
    loading = await asyncio.gather(*[c.is_loading() for c in clients])
    return not any(loading)
