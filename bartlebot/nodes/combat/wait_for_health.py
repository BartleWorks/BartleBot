"""Node: block until all clients' health is at or above a threshold.

For zones where ``handle_potions`` cannot refill: housing gauntlets today, boss rematches
later, both entered through a housing item, where there is no spot to mark and come back
to. Anywhere else, ``handle_potions`` is the answer, and this is not a general-purpose
health check -- four of its first five uses were standing in for potion handling their
bots never got (see docs/LIBRARY_MIGRATION.md).

The cap on consecutive retries is what bounds a ``FAILURE`` self-loop: at the default
timeout, five two-minute waits and the bot stops rather than waiting forever.
"""

import asyncio
import logging

from wizwalker import Keycode

from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import AFK_INTERVAL, POLL_INTERVAL

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="threshold",
        type="ratio",
        label="Health Threshold",
        help="Wait until every player's health is at or above this percentage.",
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
        "Blocks until every player's health ratio reaches or exceeds the threshold, "
        "then returns SUCCESS. Set timeout to 0 for no time limit."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players met the health threshold before the timeout."),
        ReturnSpec("FAILURE", "The timeout expired before all players met the health threshold."),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "Only for zones where Handle Potions cannot refill — a housing gauntlet, entered through "
        "a housing item, has no spot to mark and come back to. Anywhere else use Handle Potions: "
        "this waits on natural regeneration, and is not a general-purpose health check."
    ),
    see_also=["handle_potions"],
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

    loop_start = asyncio.get_event_loop().time()
    last_afk = loop_start
    met = False
    while True:
        now = asyncio.get_event_loop().time()
        ratios = await asyncio.gather(*[c.calc_health_ratio() for c in clients])
        if all(r >= threshold for r in ratios):
            met = True
            break
        if timeout_val is not None and now - loop_start > timeout_val:
            break
        if now - last_afk >= AFK_INTERVAL:
            machine.log("Sending anti-AFK input during health wait", logging.DEBUG)
            await asyncio.gather(*[c.send_key(Keycode.A, 0.1) for c in clients])
            last_afk = now
        await asyncio.sleep(POLL_INTERVAL)
    if met:
        machine.log("wait_for_health: SUCCESS — health threshold reached")
        return "SUCCESS"
    else:
        machine.log(f"wait_for_health: FAILURE — timed out after {timeout_raw}s", logging.WARNING)
        return "FAILURE"
