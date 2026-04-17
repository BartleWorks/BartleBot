import asyncio
import logging

from wizwalker import XYZ

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
            "A point inside the destination zone's transition bounding box. "
            "Teleporting clients here triggers the automatic zone change."
        ),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "Optional. A safe position to teleport clients back to when the zone "
            "change does not fire (e.g. a trigger that requires a scripted event to "
            "complete first). When set, the node retries up to 3 times before "
            "returning FAILURE."
        ),
        default=None,
        default_hint="not set — zone entry is attempted once, no retry",
        required=False,
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
            "Clients were not in from_zone at the start, timed out leaving the zone "
            "(all retry attempts exhausted when reset_xyz is set), or not all clients "
            "reached to_zone after the transition.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=10,
)

from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, ZONE_CHANGE_TIMEOUT


async def _has_left_zone(client, zone: str) -> bool:
    return await client.zone_name() != zone


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_loading()

    Flow:
      1. Verify all clients are in from_zone. Return FAILURE if not.
      2. asyncio.gather teleport all clients into the transition bounding box (xyz).
      3. Dismiss any dungeon exit warning dialog that appears.
      4. Poll until all clients have left from_zone, re-teleporting stragglers each tick.
         If any clients time out and reset_xyz is set, teleport to reset_xyz and retry (up to MAX_RETRIES).
      5. Wait for all loading screens to finish.
      6. Wait for all clients to arrive in to_zone. Return FAILURE if any are not.

    FAILURE: clients not in from_zone at start; zone change timed out after all retry
    attempts; not all clients arrived in to_zone after the transition.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    reset_xyz_data: dict | None = params.get("reset_xyz")
    reset = XYZ(reset_xyz_data["x"], reset_xyz_data["y"], reset_xyz_data["z"]) if reset_xyz_data else None

    machine.log(f"enter_zone: {from_zone!r} → {to_zone!r} via XYZ({target.x:.1f}, {target.y:.1f}, {target.z:.1f})")

    # 1. Verify all clients are in from_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if not all(z == from_zone for z in zones):
        wrong = [z for z in zones if z != from_zone]
        machine.log(
            f"FAILURE: clients not in expected zone (expected={from_zone!r}, unexpected={wrong})",
            logging.WARNING,
        )
        return "FAILURE"

    attempts = MAX_RETRIES if reset else 1
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            machine.log(f"Attempt {attempt}/{attempts}…", logging.DEBUG)

        # 2. Teleport all clients into the transition bounding box.
        machine.log(f"Teleporting {len(clients)} client(s) to transition bounding box", logging.DEBUG)
        await asyncio.gather(*[c.teleport(target) for c in clients])

        # 2b. Dismiss dungeon exit warning if it appears (triggered when leaving a dungeon zone).
        machine.log("Checking for dungeon exit warning…", logging.DEBUG)
        await asyncio.gather(
            *[node_helpers.confirm_dungeon_warning_dialog(c, machine.get_option("Safety Delay", 3.0)) for c in clients]
        )

        # 3. Wait for all clients to leave from_zone (zone change triggered by bounding box).
        #    Re-teleport only stuck clients to the trigger on each poll; clients that have
        #    already transitioned are not disturbed.
        machine.log(f"Waiting for clients to leave {from_zone!r}…", logging.DEBUG)
        seen_left = await node_helpers.poll_each_until_seen(
            clients,
            check_fn=lambda c: _has_left_zone(c, from_zone),
            action_fn=lambda c: c.teleport(target),
            timeout=ZONE_CHANGE_TIMEOUT,
        )
        if len(seen_left) < len(clients):
            stuck = [clients[i] for i in range(len(clients)) if i not in seen_left]
            stuck_zones = await asyncio.gather(*[c.zone_name() for c in stuck])
            machine.log(f"Client zones at timeout: {list(stuck_zones)}", logging.DEBUG)
            if reset and attempt < attempts:
                machine.log(
                    f"Timed out after {ZONE_CHANGE_TIMEOUT}s; resetting stuck clients to "
                    f"XYZ({reset.x:.1f}, {reset.y:.1f}, {reset.z:.1f}) and retrying…",
                    logging.WARNING,
                )
                await asyncio.gather(*[c.teleport(reset) for c in stuck])
                continue
            machine.log(
                f"FAILURE: timed out after {ZONE_CHANGE_TIMEOUT}s waiting for clients to leave {from_zone!r}",
                logging.WARNING,
            )
            return "FAILURE"

        # Zone change triggered — no more retries needed.
        break

    # 4. Wait for loading screens to finish.
    machine.log("Zone transition triggered, waiting for loading screen to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 5. Wait for all clients to arrive in to_zone.
    if (actual := await node_helpers.wait_for_zone(clients, to_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)) is not None:
        machine.log(f"Zones after transition: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: clients not in expected zone after transition (expected={to_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {to_zone!r}")
    return "SUCCESS"
