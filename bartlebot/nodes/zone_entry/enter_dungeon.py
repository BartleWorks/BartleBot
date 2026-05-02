"""Node: enter a dungeon via its sigil."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="from_zone",
        type="str",
        label="From Zone",
        help="Zone name players must currently be in when this node executes.",
        default="",
        required=True,
    ),
    ParamField(
        key="dungeon_zone",
        type="str",
        label="Dungeon Zone",
        help="Zone name all players must be in after successfully entering the dungeon.",
        default="",
        required=True,
    ),
    ParamField(
        key="sigil_xyz",
        type="xyz",
        label="Sigil XYZ",
        help=("Coordinates of the dungeon sigil. Standing here should trigger the dungeon entrance prompt."),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
    ParamField(
        key="reset_xyz",
        type="xyz",
        label="Reset XYZ",
        help=(
            "Coordinates used to dismiss the entrance prompt and reset state. "
            "The prompt must NOT appear when standing here."
        ),
        default={"x": 0.0, "y": 0.0, "z": 0.0},
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Enters a dungeon via its sigil. Handles prompt detection, "
        "dungeon warning confirmation, and player zone desynchronization."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now in dungeon_zone."),
        ReturnSpec(
            "FAILURE",
            "Players were not in from_zone at the start, the entrance prompt "
            "never appeared after retries, timed out waiting for zone change, "
            "or not all players reached dungeon_zone.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3,
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=10,
)

from bartlebot.nodes.timing import MAX_RETRIES, POLL_INTERVAL, ZONE_CHANGE_TIMEOUT


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_loading(),
          client.send_key(Keycode.X)

    Flow:
      1. Verify all clients are in from_zone. Return FAILURE if not.
      2. Clear any pre-existing dungeon entrance prompt by teleporting to reset_xyz.
      3. Retry loop (up to MAX_RETRIES): teleport to sigil_xyz, wait for the entrance
         prompt. If it does not appear, teleport to reset_xyz and retry.
      4. Press X on all clients to interact with the sigil.
      5. Poll for a dungeon warning dialog for Safety Delay seconds; confirm it if present.
      6. Wait for all clients to leave from_zone. Return FAILURE if timeout expires.
      7. Wait for all loading screens to finish.
      8. Wait for all clients to arrive in dungeon_zone. Return FAILURE if any are not.

    FAILURE: clients not in from_zone; entrance prompt never appeared after MAX_RETRIES;
    zone change timed out; not all clients reached dungeon_zone.
    """
    from_zone: str = params["from_zone"]
    dungeon_zone: str = params["dungeon_zone"]
    sigil = XYZ(**params["sigil_xyz"])
    reset = XYZ(**params["reset_xyz"])
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"enter_dungeon: {from_zone!r} → {dungeon_zone!r}")

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

    # 2. Clear any pre-existing dungeon entrance prompt before starting.
    prompts = await asyncio.gather(
        *[node_helpers.is_visible_by_path(c, node_helpers.DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) for c in clients]
    )
    if any(prompts):
        stale = [i for i, p in enumerate(prompts) if p]
        machine.log(
            f"Pre-existing dungeon entrance prompt on client(s) {stale}; teleporting to reset_xyz to clear it",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])
        await node_helpers.poll_until(
            lambda: node_helpers.none_at_dungeon_entrance(clients), safety_delay, POLL_INTERVAL
        )

    # 3. Retry loop: teleport to sigil, wait for dungeon entrance prompt.
    prompt_appeared = False
    for attempt in range(1, MAX_RETRIES + 1):
        machine.log(f"Attempt {attempt}/{MAX_RETRIES}: teleporting clients to sigil", logging.DEBUG)
        await asyncio.gather(*[c.teleport(sigil) for c in clients])

        machine.log("Waiting for dungeon entrance prompt…", logging.DEBUG)
        if await node_helpers.poll_until(
            lambda: node_helpers.all_at_dungeon_entrance(clients), safety_delay, POLL_INTERVAL
        ):
            prompt_appeared = True
            break

        machine.log(
            f"Entrance prompt did not appear (attempt {attempt}); resetting to reset_xyz",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(reset) for c in clients])

    if not prompt_appeared:
        machine.log(
            f"FAILURE: entrance prompt never appeared after {MAX_RETRIES} attempts",
            logging.WARNING,
        )
        return "FAILURE"

    # 4. Press X to interact with the sigil (entrance prompt is showing).
    machine.log("Pressing X to enter dungeon sigil", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # 5. Wait briefly, then confirm dungeon warning dialog if it appeared.
    machine.log("Checking for dungeon warning dialog…", logging.DEBUG)
    await asyncio.gather(
        *[node_helpers.confirm_dungeon_warning_dialog(c, safety_delay, POLL_INTERVAL) for c in clients]
    )

    # 6. Wait for all clients to leave from_zone.
    machine.log(f"Waiting for clients to leave {from_zone!r}…", logging.DEBUG)
    if not await node_helpers.poll_until(
        lambda: node_helpers.all_left_zone(clients, from_zone), ZONE_CHANGE_TIMEOUT, POLL_INTERVAL
    ):
        zones_at_timeout = await asyncio.gather(*[c.zone_name() for c in clients])
        machine.log(f"Client zones at timeout: {list(zones_at_timeout)}", logging.DEBUG)
        machine.log(
            f"FAILURE: timed out after {ZONE_CHANGE_TIMEOUT}s waiting for clients to leave {from_zone!r}",
            logging.WARNING,
        )
        return "FAILURE"

    # 7. Wait for loading screens to finish.
    machine.log("Waiting for loading screen to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 8. Wait for all clients to arrive in dungeon_zone.
    if (
        actual := await node_helpers.wait_for_zone(clients, dungeon_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)
    ) is not None:
        machine.log(f"Zones after dungeon entry: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients reached dungeon zone (expected={dungeon_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {dungeon_zone!r}")
    return "SUCCESS"
