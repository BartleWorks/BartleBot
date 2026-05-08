"""Node: check whether all clients are at or above a health ratio threshold."""

import asyncio

from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="threshold",
        type="float",
        label="Health Threshold",
        help="Health ratio (0.0–1.0). Returns TRUE if every player's health is at or above this value.",
        default=1.0,
        default_hint="TRUE only if all players are at full health",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Returns TRUE if all players are at or above a health ratio threshold, FALSE otherwise. "
        "Useful for routing to a recovery node (potions, wisps) when any player is low on health."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "All players have a health ratio at or above the threshold."),
        ReturnSpec("FALSE", "At least one player has a health ratio below the threshold."),
    ],
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.calc_health_ratio()

    Flow:
      1. Gather calc_health_ratio() for all clients in parallel.
      2. If all ratios >= threshold, return TRUE.
      3. Otherwise log which clients are below and return FALSE.
    """
    threshold: float = params.get("threshold", 1.0)

    ratios: list[float] = list(await asyncio.gather(*[c.calc_health_ratio() for c in clients]))

    below = [i for i, r in enumerate(ratios) if r < threshold]

    if not below:
        machine.log(f"check_health: TRUE — all clients at or above {threshold} ({[f'{r:.2f}' for r in ratios]})")
        return "TRUE"

    machine.log(f"check_health: FALSE — client(s) {below} below health threshold {threshold}")
    return "FALSE"
