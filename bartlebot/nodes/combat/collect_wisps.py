"""Node: collect health and mana wisps after combat."""

import asyncio
import logging

from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import jitter

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="if_health_below",
        type="float",
        label="Collect Health Wisps If Below",
        help=(
            "Health threshold (0.0–1.0). Teleport to the closest health wisp while "
            "the client's health ratio is below this value. Set to 0.0 to skip "
            "health wisp collection entirely."
        ),
        default=1.0,
        default_hint="Default: collect health wisps whenever not at full health",
    ),
    ParamField(
        key="if_mana_below",
        type="float",
        label="Collect Mana Wisps If Below",
        help=(
            "Mana threshold (0.0–1.0). Teleport to the closest mana wisp while "
            "the client's mana ratio is below this value. Set to 0.0 to skip "
            "mana wisp collection entirely."
        ),
        default=1.0,
        default_hint="Default: collect mana wisps whenever not at full mana",
    ),
    ParamField(
        key="fail_if_health_below",
        type="float",
        label="Fail If Health Still Below",
        help=(
            "Health threshold (0.0–1.0). After wisp collection, if any client's "
            "health ratio is still below this value, the node returns FAILURE. "
            "Useful for routing to a potion node when wisps are insufficient. "
            "Set to 0.0 to never fail on health."
        ),
        default=0.5,
        default_hint="Default: FAILURE if any client is still below 50% health",
    ),
    ParamField(
        key="fail_if_mana_below",
        type="float",
        label="Fail If Mana Still Below",
        help=(
            "Mana threshold (0.0–1.0). After wisp collection, if any client's "
            "mana ratio is still below this value, the node returns FAILURE. "
            "Useful for routing to a potion node when wisps are insufficient. "
            "Set to 0.0 to never fail on mana."
        ),
        default=0.5,
        default_hint="Default: FAILURE if any client is still below 50% mana",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Collects health and mana wisps after combat if clients are below specified "
        "thresholds. Teleports each client to the nearest wisp repeatedly until the "
        "threshold is met or no more wisps are in range. Returns FAILURE if any client "
        "is still below the configured fail thresholds after collection — useful for "
        "routing to a potion fallback node."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All clients are at or above both fail thresholds after collection."),
        ReturnSpec(
            "FAILURE",
            "At least one client is still below a fail threshold after wisps were exhausted.",
        ),
    ],
    max_consecutive_retries=15,
)

# Small delay between wisp teleports so the wisp collection registers before
# the health/mana ratio is re-checked. Matches Deimos utils.py pattern.
_WISP_COLLECT_DELAY = 0.1


async def run(machine, clients: list, params: dict) -> str:
    """Collect health and mana wisps for all clients based on configured thresholds."""
    if_health_below: float = params.get("if_health_below", 1.0)
    if_mana_below: float = params.get("if_mana_below", 1.0)
    fail_if_health_below: float = params.get("fail_if_health_below", 0.5)
    fail_if_mana_below: float = params.get("fail_if_mana_below", 0.5)

    machine.log(
        f"collect_wisps: collect_health<{if_health_below}, collect_mana<{if_mana_below}, "
        f"fail_health<{fail_if_health_below}, fail_mana<{fail_if_mana_below}"
    )

    await asyncio.gather(*[_collect_for_client(c, if_health_below, if_mana_below) for c in clients])

    # Post-collection check: return FAILURE if any client is still below a fail threshold.
    final_health, final_mana = zip(*await asyncio.gather(*[_get_ratios(c) for c in clients]))

    health_failures = [i for i, h in enumerate(final_health) if h < fail_if_health_below]
    mana_failures = [i for i, m in enumerate(final_mana) if m < fail_if_mana_below]

    machine.log(
        f"Post-collection ratios — health: {[f'{h:.2f}' for h in final_health]}, "
        f"mana: {[f'{m:.2f}' for m in final_mana]}",
        logging.DEBUG,
    )

    if health_failures:
        machine.log(
            f"FAILURE: client(s) {health_failures} still below health threshold "
            f"{fail_if_health_below} after wisp collection",
            logging.WARNING,
        )
        return "FAILURE"

    if mana_failures:
        machine.log(
            f"FAILURE: client(s) {mana_failures} still below mana threshold {fail_if_mana_below} after wisp collection",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log("SUCCESS: all clients above resource thresholds")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_for_client(client, if_health_below: float, if_mana_below: float) -> None:
    """Collect health and mana wisps for a single client until thresholds are met."""
    if if_health_below > 0.0:
        while await client.calc_health_ratio() < if_health_below:
            found = await client.tp_to_closest_health_wisp()
            if not found:
                break
            await asyncio.sleep(jitter(_WISP_COLLECT_DELAY))

    if if_mana_below > 0.0:
        while await client.calc_mana_ratio() < if_mana_below:
            found = await client.tp_to_closest_mana_wisp()
            if not found:
                break
            await asyncio.sleep(jitter(_WISP_COLLECT_DELAY))


async def _get_ratios(client) -> tuple[float, float]:
    """Return (health_ratio, mana_ratio) for a client."""
    health, mana = await asyncio.gather(client.calc_health_ratio(), client.calc_mana_ratio())
    return health, mana
