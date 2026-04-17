"""Node: sleep for a fixed duration."""

import asyncio

from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="duration",
        type="float",
        label="Duration (seconds)",
        help="Number of seconds to wait before continuing.",
        default=1.0,
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description="Waits for the given number of seconds, then returns SUCCESS.",
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec(
            "SUCCESS",
            "Always returned after the delay has elapsed.",
        ),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: asyncio.sleep()

    Flow:
      1. Read duration param.
      2. Sleep for duration seconds.

    FAILURE: never — always returns SUCCESS.
    """
    duration: float = params["duration"]
    machine.log(f"sleep: waiting {duration}s…")
    await asyncio.sleep(duration)
    return "SUCCESS"
