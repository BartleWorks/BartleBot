"""Node: dismiss all dialogue and wait for the conversation to fully conclude."""

import asyncio
import logging

from bartlebot.nodes import node_helpers
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.paths import ADVANCE_DIALOG_PATH
from bartlebot.nodes.timing import POLL_INTERVAL

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="timeout",
        type="float",
        label="Timeout (seconds, 0 = no limit)",
        help=(
            "Maximum seconds to wait before returning FAILURE. "
            "Set to 0 to wait indefinitely (mirrors Deimos waitforfree behavior)."
        ),
        default=60.0,
        default_hint="60s",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Dismisses dialogue on all clients. Polls for at least Safety Delay seconds; if no client has dialogue "
        "for the full delay, returns SUCCESS. Each time dialogue is dismissed, the Safety Delay timer resets, "
        "preventing premature exit between consecutive dialogue windows. Set timeout to 0 for no time limit."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients have been dialogue-free for at least the Safety Delay."),
        ReturnSpec("FAILURE", "The timeout expired while players were still in dialogue."),
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
    max_consecutive_retries=None,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: SprintyClient.is_in_dialog(), node_helpers.click_by_path()

    Flow:
      1. Poll for Safety Delay seconds. If no dialogue appears, return SUCCESS.
      2. When dialogue is seen on any client, dismiss it and reset the Safety Delay timer.
      3. Repeat until Safety Delay seconds have elapsed with no dialogue on any client.
      4. If timeout > 0 and it expires while clients are still in dialogue, return FAILURE.

    FAILURE: timeout expired before all dialogue cleared
    (unreachable when timeout=0).
    """
    timeout_raw: float = params.get("timeout", 60.0)
    timeout_val: float | None = None if timeout_raw == 0.0 else timeout_raw
    safety_delay: float = machine.get_option("Safety Delay")

    loop = asyncio.get_event_loop()
    overall_start = loop.time()
    quiet_since = overall_start  # start of the current dialogue-free window

    while True:
        now = loop.time()
        dialogs = await asyncio.gather(*[c.is_in_dialog() for c in clients])

        if not any(dialogs) and (now - quiet_since) >= safety_delay:
            machine.log("wait_for_dialogue: SUCCESS — dialogue clear for Safety Delay", logging.DEBUG)
            return "SUCCESS"

        if timeout_val is not None and (now - overall_start) >= timeout_val:
            machine.log(f"wait_for_dialogue: FAILURE — timed out after {timeout_raw}s", logging.WARNING)
            return "FAILURE"

        if any(dialogs):
            await asyncio.gather(
                *[node_helpers.click_by_path(c, ADVANCE_DIALOG_PATH) for c, d in zip(clients, dialogs) if d]
            )
            quiet_since = loop.time()

        await asyncio.sleep(POLL_INTERVAL)
