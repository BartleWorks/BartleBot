"""Node: enter a dungeon via the team-up queue."""

import asyncio
import logging

from wizwalker import XYZ, Keycode

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import (
    DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH,
    ENTER_TEAMUP_BUTTON_PATH,
    TEAMUP_2_PLAYER_CHECKBOX,
    TEAMUP_3_PLAYER_CHECKBOX,
    TEAMUP_4_PLAYER_CHECKBOX,
)

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
        key="dungeon_zone",
        type="str",
        label="Dungeon Zone",
        help="Zone name all clients must be in after successfully entering the dungeon.",
        default="",
        required=True,
    ),
    ParamField(
        key="sigil_xyz",
        type="xyz",
        label="Sigil XYZ",
        help="Coordinates of the dungeon sigil. Standing here should trigger the dungeon entrance prompt.",
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
    ParamField(
        key="minimum_team_size",
        type="int_choice",
        label="Minimum Team Size",
        help="Minimum number of players required before the dungeon starts.",
        default=2,
        choices=[2, 3, 4],
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Enters a dungeon via the team-up queue. Handles prompt detection, "
        "team size selection, anti-AFK inputs during the queue wait, and "
        "client zone desynchronization."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients are now in dungeon_zone."),
        ReturnSpec(
            "FAILURE",
            "Clients were not in from_zone at the start, the entrance prompt "
            "never appeared after retries, the team-up confirmation window never "
            "opened, an invalid minimum_team_size was given, timed out waiting "
            "for the queue match, or not all clients reached dungeon_zone.",
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

from bartlebot.nodes.timing import (  # noqa: E402
    AFK_INTERVAL,
    MAX_RETRIES,
    POLL_INTERVAL,
    TEAMUP_QUEUE_TIMEOUT,
    ZONE_CHANGE_TIMEOUT,
)

_TEAM_SIZE_PATHS = {
    2: TEAMUP_2_PLAYER_CHECKBOX,
    3: TEAMUP_3_PLAYER_CHECKBOX,
    4: TEAMUP_4_PLAYER_CHECKBOX,
}


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), client.teleport(XYZ), client.is_loading(),
          client.send_key(Keycode.A), node_helpers.click_by_path()

    Flow:
      1. Validate minimum_team_size (must be 2, 3, or 4). Return FAILURE if invalid.
      2. Verify all clients are in from_zone. Return FAILURE if not.
      3. Clear any pre-existing dungeon entrance prompt by teleporting to reset_xyz.
      4. Retry loop (up to MAX_RETRIES): teleport to sigil_xyz, wait for the entrance
         prompt. If it does not appear, teleport to reset_xyz and retry.
      5. Click the team-up button on all clients.
      6. Wait for the team-up confirmation window to appear on all clients. Return FAILURE if not.
      7. Select the minimum_team_size checkbox on all clients.
      8. Click the enter team-up button on all clients to join the queue.
      9. Poll for zone change, sending a brief A keypress every AFK_INTERVAL seconds
         to prevent idle disconnection. Timeout after TEAMUP_QUEUE_TIMEOUT (1 hour).
      10. Wait for all loading screens to finish.
      11. Wait for all clients to arrive in dungeon_zone. Return FAILURE if any are not.

    FAILURE: invalid minimum_team_size; clients not in from_zone; entrance prompt never
    appeared; team-up window never opened; queue timed out; not all clients reached dungeon_zone.
    """
    from_zone: str = params["from_zone"]
    dungeon_zone: str = params["dungeon_zone"]
    sigil = XYZ(**params["sigil_xyz"])
    reset = XYZ(**params["reset_xyz"])
    minimum_team_size: int = params.get("minimum_team_size", 2)
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    machine.log(f"enter_dungeon_teamup: {from_zone!r} → {dungeon_zone!r} (min team size: {minimum_team_size})")

    # Validate minimum_team_size early so we fail fast before any game interaction.
    checkbox_path = _TEAM_SIZE_PATHS.get(minimum_team_size)
    if checkbox_path is None:
        machine.log(
            f"FAILURE: invalid minimum_team_size {minimum_team_size!r}, must be 2, 3, or 4",
            logging.WARNING,
        )
        return "FAILURE"

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
        *[node_helpers.is_visible_by_path(c, DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) for c in clients]
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

    # 4. Click the team-up button to open the confirmation window.
    machine.log("Clicking team-up button", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH) for c in clients])

    # 5. Wait for the team-up confirmation window to appear on every client.
    machine.log("Waiting for team-up confirmation window…", logging.DEBUG)
    appeared = await asyncio.gather(
        *[
            node_helpers.poll_until(
                lambda c=c: node_helpers.is_visible_by_path(c, ENTER_TEAMUP_BUTTON_PATH),
                safety_delay,
                POLL_INTERVAL,
            )
            for c in clients
        ]
    )
    if not all(appeared):
        machine.log("FAILURE: team-up confirmation window never appeared", logging.WARNING)
        return "FAILURE"

    # 6. Select the minimum team size checkbox.
    machine.log(f"Selecting {minimum_team_size}-player team size checkbox", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, checkbox_path) for c in clients])

    # 7. Click the enter team-up button to join the queue.
    machine.log("Clicking enter team-up button", logging.DEBUG)
    await asyncio.gather(*[node_helpers.click_by_path(c, ENTER_TEAMUP_BUTTON_PATH) for c in clients])

    # 8. Wait for zone change with periodic anti-AFK inputs.
    # Team-up matchmaking can take a long time; we send a brief A key press every
    # AFK_INTERVAL seconds to prevent the character from going idle.
    machine.log(f"Waiting for team-up zone change (timeout: {TEAMUP_QUEUE_TIMEOUT / 60:.0f}m)…", logging.DEBUG)
    loop_start = asyncio.get_event_loop().time()
    last_afk = loop_start
    while True:
        now = asyncio.get_event_loop().time()
        if await node_helpers.all_left_zone(clients, from_zone):
            break
        if now - loop_start > TEAMUP_QUEUE_TIMEOUT:
            machine.log(
                f"FAILURE: timed out after {TEAMUP_QUEUE_TIMEOUT / 60:.0f}m waiting for team-up zone change",
                logging.WARNING,
            )
            return "FAILURE"
        if now - last_afk >= AFK_INTERVAL:
            machine.log("Sending anti-AFK input during team-up wait", logging.DEBUG)
            await asyncio.gather(*[c.send_key(Keycode.A, 0.1) for c in clients])
            last_afk = now
        await asyncio.sleep(POLL_INTERVAL)

    # 9. Wait for loading screens to finish.
    machine.log("Waiting for loading screen to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 10. Wait for all clients to arrive in dungeon_zone.
    if (
        actual := await node_helpers.wait_for_zone(clients, dungeon_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)
    ) is not None:
        machine.log(f"Zones after team-up entry: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients reached dungeon zone (expected={dungeon_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {dungeon_zone!r}")
    return "SUCCESS"
