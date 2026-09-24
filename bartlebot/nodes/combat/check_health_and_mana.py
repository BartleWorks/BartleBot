"""Node: check whether every player is at or above a health and a mana ratio.

Merged from ``check_health`` and ``check_mana`` (both since removed), which had the same
shape -- one threshold, TRUE/FALSE -- and needed chaining whenever a bot cared about both.

A threshold of 0 switches that stat off: no ratio is below 0, so the check always
passes, and the stat is not read at all. That is also why detecting an *empty* player
takes a small positive threshold, 0.01, rather than 0.

Its job is narrow: gating a detour that is worth skipping while the players are already
topped up, such as porting away to collect wisps and back. It is not a general-purpose
health gate ahead of combat; ``handle_potions`` is what keeps players alive. A bot that
routes low health and low mana to different places uses two of these, each with the
other stat at 0.
"""

import asyncio

from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="health_threshold",
        type="float",
        label="Health Threshold",
        help=(
            "Health ratio (0.0–1.0). FALSE if any player's health is below this value. "
            "Set to 0 to skip the health check."
        ),
        default=1.0,
        minimum=0.0,
        maximum=1.0,
        default_hint="FALSE unless every player is at full health",
    ),
    ParamField(
        key="mana_threshold",
        type="float",
        label="Mana Threshold",
        help=(
            "Mana ratio (0.0–1.0). FALSE if any player's mana is below this value. "
            "Set to 0 to skip the mana check, or to 0.01 to catch only a player with no mana "
            "at all — after fleeing a battle, for instance."
        ),
        default=1.0,
        minimum=0.0,
        maximum=1.0,
        default_hint="FALSE unless every player is at full mana",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Returns TRUE if every player's health and mana ratios are at or above their thresholds, "
        "FALSE otherwise. A threshold of 0 skips that stat. Useful for skipping a top-up detour, "
        "such as collecting wisps, while the players are already healthy."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("TRUE", "Every player is at or above both thresholds."),
        ReturnSpec("FALSE", "At least one player is below at least one threshold."),
    ],
)


async def _ratios(clients: list, threshold: float, read) -> list[float] | None:
    """Every client's ratio via *read*, or None when *threshold* switches the stat off."""
    if threshold <= 0.0:
        return None
    return list(await asyncio.gather(*[read(c) for c in clients]))


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.calc_health_ratio(), client.calc_mana_ratio()

    Flow:
      1. For each stat whose threshold is above 0, gather every client's ratio in parallel.
         A stat at 0 is not read.
      2. If every read ratio is at or above its threshold, return TRUE.
      3. Otherwise log each player and stat that fell short, and return FALSE.
    """
    health_threshold: float = params.get("health_threshold", 1.0)
    mana_threshold: float = params.get("mana_threshold", 1.0)

    health, mana = await asyncio.gather(
        _ratios(clients, health_threshold, lambda c: c.calc_health_ratio()),
        _ratios(clients, mana_threshold, lambda c: c.calc_mana_ratio()),
    )

    short: list[str] = []
    for stat, ratios, threshold in (("health", health, health_threshold), ("mana", mana, mana_threshold)):
        if ratios is None:
            continue
        short += [f"player {i + 1} {stat} {r:.2f} < {threshold:.2f}" for i, r in enumerate(ratios) if r < threshold]

    if not short:
        machine.log(
            f"check_health_and_mana: TRUE — health {_fmt(health)} ≥ {health_threshold:.2f}, "
            f"mana {_fmt(mana)} ≥ {mana_threshold:.2f}"
        )
        return "TRUE"

    machine.log(f"check_health_and_mana: FALSE — {'; '.join(short)}")
    return "FALSE"


def _fmt(ratios: list[float] | None) -> str:
    return "not checked" if ratios is None else str([f"{r:.2f}" for r in ratios])
