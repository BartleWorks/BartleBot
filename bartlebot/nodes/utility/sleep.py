"""Node: sleep for a fixed duration, or for the user's Safety Delay."""

import asyncio
from dataclasses import replace

from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec

PARAM_USE_SAFETY_DELAY = "use_safety_delay"

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="duration",
        type="float",
        label="Duration (s)",
        help="How long to wait.",
        default=1.0,
        minimum=0.0,
        disabled_by=PARAM_USE_SAFETY_DELAY,
        required=True,
    ),
    ParamField(
        key=PARAM_USE_SAFETY_DELAY,
        type="bool",
        label="Use Safety Delay",
        help=(
            "When on, the node waits for the user's Safety Delay instead of Duration. Use it when "
            "waiting on the game server, since that depends on the user's connection."
        ),
        default=False,
    ),
]

NODE_SPEC = NodeSpec(
    description="Waits for Duration, or for Safety Delay.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "Always, once the wait is over.",
        ),
    ],
    # Only a sleep that reads it puts Safety Delay in the bot's options.
    required_bot_options=[replace(SAFETY_DELAY_OPTION, required_when=PARAM_USE_SAFETY_DELAY)],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: asyncio.sleep()

    Flow:
      1. Read the Safety Delay option if use_safety_delay is on, else the duration param.
      2. Sleep for that many seconds.

    FAILURE: never — always returns SUCCESS.
    """
    if params.get(PARAM_USE_SAFETY_DELAY, False):
        duration: float = machine.get_option("Safety Delay", SAFETY_DELAY_OPTION.default)
        machine.log(f"sleep: waiting the Safety Delay, {duration}s…")
    else:
        duration = params["duration"]
        machine.log(f"sleep: waiting {duration}s…")
    await asyncio.sleep(duration)
    return "SUCCESS"
