"""Node: block until all clients are out of dialogue."""

import asyncio
import logging

from bartlebot.nodes.node_helpers import poll_until
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
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
        "Waits at least the Safety Delay for dialogue to begin, then blocks until all players are no longer in "
        "dialogue. Returns SUCCESS immediately if no dialogue appears within the Safety Delay. Set timeout to 0 "
        "for no time limit."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are out of dialogue, or no dialogue appeared within the Safety Delay."),
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
    APIs: SprintyClient.is_in_dialog()

    Flow:
      1. Poll is_in_dialog() for at least Safety Delay seconds.
         If no client ever enters dialogue within that window, return SUCCESS.
      2. Once dialogue is detected (or if already in dialogue at start), wait for
         all clients to leave dialogue.
      3. If timeout > 0 and it expires while clients are still in dialogue, return FAILURE.

    FAILURE: timeout expired before all clients left dialogue
    (unreachable when timeout=0).
    """
    timeout_raw: float = params.get("timeout", 60.0)
    timeout_val: float | None = None if timeout_raw == 0.0 else timeout_raw
    safety_delay: float = machine.get_option("Safety Delay", 3.0)

    machine.log("wait_for_dialogue: waiting for dialogue to begin", logging.DEBUG)

    loop = asyncio.get_event_loop()
    start = loop.time()

    # Phase 1: wait up to safety_delay for any client to enter dialogue.
    while True:
        in_dialog = await asyncio.gather(*[c.is_in_dialog() for c in clients])
        elapsed = loop.time() - start
        if any(in_dialog):
            machine.log("wait_for_dialogue: dialogue detected", logging.DEBUG)
            break
        if elapsed >= safety_delay:
            machine.log(
                "wait_for_dialogue: SUCCESS — no dialogue appeared within Safety Delay",
                logging.DEBUG,
            )
            return "SUCCESS"
        await asyncio.sleep(POLL_INTERVAL)

    # Phase 2: dialogue was seen — wait for all clients to leave it.
    machine.log("wait_for_dialogue: waiting for all clients to leave dialogue", logging.DEBUG)

    async def all_out() -> bool:
        in_dialog = await asyncio.gather(*[c.is_in_dialog() for c in clients])
        return not any(in_dialog)

    remaining: float | None = None
    if timeout_val is not None:
        remaining = timeout_val - (loop.time() - start)
        if remaining <= 0:
            machine.log(f"wait_for_dialogue: FAILURE — timed out after {timeout_raw}s", logging.WARNING)
            return "FAILURE"

    met = await poll_until(all_out, remaining, POLL_INTERVAL)
    if met:
        machine.log("wait_for_dialogue: SUCCESS — all clients are out of dialogue", logging.DEBUG)
        return "SUCCESS"
    else:
        machine.log(f"wait_for_dialogue: FAILURE — timed out after {timeout_raw}s", logging.WARNING)
        return "FAILURE"
