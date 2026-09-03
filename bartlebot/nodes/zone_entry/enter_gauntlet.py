"""Node: enter a gauntlet dungeon via its housing sigil."""

import asyncio
import logging

from wizwalker import Keycode

from bartlebot import dialog_captions
from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="dungeon_zone",
        type="str",
        label="Dungeon Zone",
        help="Zone name all players must be in after successfully entering the gauntlet.",
        default="",
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Enters a gauntlet dungeon placed in a player's house. Unlike enter_dungeon, "
        "this node does not teleport players — they must walk to the gauntlet sigil. "
        "Waits for the entrance prompt to appear, presses X, confirms any warning dialog, "
        "and waits for all players to arrive in the dungeon zone."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are now in dungeon_zone."),
        ReturnSpec(
            "FAILURE",
            "The entrance prompt never appeared within the timeout, or not all players reached dungeon_zone.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            minimum=0.0,
            suffix=" s",
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=10,
)

from bartlebot.nodes.timing import POLL_INTERVAL, ZONE_CHANGE_TIMEOUT


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.send_key(Keycode.X), client.is_loading()

    Flow:
      1. Wait for the gauntlet entrance prompt to appear on all clients within Safety Delay
         seconds. Return FAILURE if the prompt does not appear.
      2. Press X on all clients simultaneously.
      3. Poll for a dungeon warning dialog for Safety Delay seconds; confirm it if present.
      4. Wait for all loading screens to finish.
      5. Wait for all clients to arrive in dungeon_zone. Return FAILURE if any are not.

    FAILURE: gauntlet entrance prompt never appeared within the timeout; not all clients
    reached dungeon_zone after loading.
    """
    dungeon_zone: str = params["dungeon_zone"]
    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    machine.log(f"enter_gauntlet: waiting for entrance prompt → {dungeon_zone!r}")

    # 1. Wait for all clients to have the gauntlet entrance prompt.
    machine.log("Waiting for gauntlet entrance prompt…", logging.DEBUG)
    if not await node_helpers.poll_until(
        lambda: node_helpers.all_at_dungeon_entrance(clients, path=node_helpers.GAUNTLET_ENTRANCE_PATH),
        safety_delay,
        POLL_INTERVAL,
    ):
        machine.log("FAILURE: gauntlet entrance prompt never appeared", logging.WARNING)
        return "FAILURE"

    # 2. Press X to enter.
    machine.log("Pressing X to enter gauntlet", logging.DEBUG)
    await asyncio.gather(*[c.send_key(Keycode.X, 0.1) for c in clients])

    # 3. Answer the entry prompt if the gauntlet raises one.
    machine.log("Checking for an instance prompt…", logging.DEBUG)
    if refusal := await node_helpers.answer_instance_prompts(clients, safety_delay, POLL_INTERVAL):
        machine.log(
            f"FAILURE: could not enter the gauntlet — {dialog_captions.describe(refusal)}",
            logging.WARNING,
        )
        return "FAILURE"

    # 4. Wait for loading screens to clear.
    machine.log("Waiting for loading screen to clear…", logging.DEBUG)
    await node_helpers.poll_until(lambda: node_helpers.none_loading(clients), None, POLL_INTERVAL)

    # 5. Wait for all clients to arrive in dungeon_zone.
    if (
        actual := await node_helpers.wait_for_zone(clients, dungeon_zone, ZONE_CHANGE_TIMEOUT, POLL_INTERVAL)
    ) is not None:
        machine.log(f"Zones after gauntlet entry: {actual}", logging.DEBUG)
        machine.log(
            f"FAILURE: not all clients reached gauntlet zone (expected={dungeon_zone!r}, got={list(set(actual))})",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {dungeon_zone!r}")
    return "SUCCESS"
