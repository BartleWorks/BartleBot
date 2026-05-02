"""Node: block until all clients are out of dialogue."""

import asyncio

from bartlebot.nodes.node_helpers import poll_until
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
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
        "Blocks until all players are no longer in dialogue, then returns SUCCESS. Set timeout to 0 for no time limit."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are out of dialogue."),
        ReturnSpec("FAILURE", "The timeout expired while players were still in dialogue."),
    ],
    max_consecutive_retries=None,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: SprintyClient.is_in_dialog()

    Flow:
      1. Poll is_in_dialog() on all clients simultaneously via asyncio.gather.
      2. If no client is in dialogue, return SUCCESS.
      3. Otherwise sleep POLL_INTERVAL and repeat.
      4. If timeout > 0 and it expires, return FAILURE.

    FAILURE: timeout expired before all clients left dialogue
    (unreachable when timeout=0).
    """
    timeout_raw: float = params.get("timeout", 60.0)
    timeout_val: float | None = None if timeout_raw == 0.0 else timeout_raw

    machine.log("wait_for_dialogue: waiting for all clients to leave dialogue")

    async def condition() -> bool:
        in_dialog = await asyncio.gather(*[c.is_in_dialog() for c in clients])
        return not any(in_dialog)

    met = await poll_until(condition, timeout_val, POLL_INTERVAL)
    if met:
        machine.log("wait_for_dialogue: SUCCESS — all clients are out of dialogue")
        return "SUCCESS"
    else:
        machine.log(f"wait_for_dialogue: FAILURE — timed out after {timeout_raw}s", "WARNING")
        return "FAILURE"
