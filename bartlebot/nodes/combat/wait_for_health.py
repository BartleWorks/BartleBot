"""Node: block until all clients' health is at or above a threshold."""

import asyncio

from bartlebot.nodes.node_helpers import poll_until
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import POLL_INTERVAL

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="threshold",
        type="float",
        label="Health Threshold",
        help="Wait until all clients' health ratio is at or above this value (0.0–1.0).",
        default=0.8,
        required=True,
    ),
    ParamField(
        key="timeout",
        type="float",
        label="Timeout (seconds, 0 = no limit)",
        help=("Maximum seconds to wait before returning FAILURE. Set to 0 to wait indefinitely."),
        default=120.0,
        default_hint="120s",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Blocks until all clients' health ratio reaches or exceeds the threshold, "
        "then returns SUCCESS. Set timeout to 0 for no time limit."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients met the health threshold before the timeout."),
        ReturnSpec("FAILURE", "The timeout expired before all clients met the health threshold."),
    ],
    max_consecutive_retries=None,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: SprintyClient.calc_health_ratio()

    Flow:
      1. Poll calc_health_ratio() on all clients simultaneously via asyncio.gather.
      2. If all ratios are at or above threshold, return SUCCESS.
      3. Otherwise sleep POLL_INTERVAL and repeat.
      4. If timeout > 0 and it expires, return FAILURE.

    FAILURE: timeout expired before all clients met the health threshold
    (unreachable when timeout=0).
    """
    threshold: float = params["threshold"]
    timeout_raw: float = params.get("timeout", 120.0)
    timeout_val: float | None = None if timeout_raw == 0.0 else timeout_raw

    machine.log(f"wait_for_health: waiting for all clients to reach {threshold:.0%} health")

    async def condition() -> bool:
        ratios = await asyncio.gather(*[c.calc_health_ratio() for c in clients])
        return all(r >= threshold for r in ratios)

    met = await poll_until(condition, timeout_val, POLL_INTERVAL)
    if met:
        machine.log("wait_for_health: SUCCESS — health threshold reached")
        return "SUCCESS"
    else:
        machine.log(f"wait_for_health: FAILURE — timed out after {timeout_raw}s", "WARNING")
        return "FAILURE"
