"""Node: collect health and mana wisps after combat."""

import asyncio
import logging

from wizwalker import XYZ

from bartlebot.nodes.node_helpers import SAFE_RADIUS, mob_clearances
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.timing import WISP_COLLECT_DELAY, WISP_COLLECT_TIMEOUT, jitter

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="if_health_below",
        type="float",
        label="Collect Health Wisps If Below",
        help=(
            "Health threshold (0.0–1.0). Teleport to the closest health wisp while "
            "the player's health ratio is below this value. Set to 0.0 to skip "
            "health wisp collection entirely."
        ),
        default=1.0,
        default_hint="collect health wisps whenever not at full health",
    ),
    ParamField(
        key="if_mana_below",
        type="float",
        label="Collect Mana Wisps If Below",
        help=(
            "Mana threshold (0.0–1.0). Teleport to the closest mana wisp while "
            "the player's mana ratio is below this value. Set to 0.0 to skip "
            "mana wisp collection entirely."
        ),
        default=1.0,
        default_hint="collect mana wisps whenever not at full mana",
    ),
    ParamField(
        key="timeout",
        type="float",
        label="Timeout (seconds, 0 = no limit)",
        help=(
            "Maximum seconds to spend collecting wisps. Bounds the collection loop in case a "
            "stale or bugged health/mana ratio reading never crosses the threshold. Set to 0 "
            "to wait indefinitely."
        ),
        default=WISP_COLLECT_TIMEOUT,
        default_hint="10s",
    ),
    ParamField(
        key="fail_if_health_below",
        type="float",
        label="Fail If Health Still Below",
        help=(
            "Health threshold (0.0–1.0). After wisp collection, if any player's "
            "health ratio is still below this value, the node returns FAILURE. "
            "Useful for routing to a potion node when wisps are insufficient. "
            "Set to 0.0 to never fail on health."
        ),
        default=0.5,
        default_hint="FAILURE if any player is still below 50% health",
    ),
    ParamField(
        key="fail_if_mana_below",
        type="float",
        label="Fail If Mana Still Below",
        help=(
            "Mana threshold (0.0–1.0). After wisp collection, if any player's "
            "mana ratio is still below this value, the node returns FAILURE. "
            "Useful for routing to a potion node when wisps are insufficient. "
            "Set to 0.0 to never fail on mana."
        ),
        default=0.5,
        default_hint="FAILURE if any player is still below 50% mana",
    ),
    ParamField(
        key="only_safe",
        type="bool",
        label="Only Collect Safe Wisps",
        help=(
            f"Skip wisps that are within {SAFE_RADIUS:.0f} units of an enemy. Prevents "
            "accidentally entering combat while collecting wisps after a fight. Disable only "
            "if you need to collect wisps in areas where enemies are always nearby."
        ),
        default=True,
        default_hint="skip wisps near enemies",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Collects health and mana wisps after combat if players are below specified "
        "thresholds. The players move as one group, all teleporting to the same wisp at "
        "once, so a player who is already full passes through without using it up and nobody "
        "is left behind on their own. Repeats until every player meets its thresholds, no "
        "wisp is left, or the timeout expires. "
        f"By default, skips wisps within {SAFE_RADIUS:.0f} units of an enemy to avoid "
        "re-entering combat. Returns FAILURE if any player is still below the configured fail "
        "thresholds after collection — useful for routing to a potion fallback node."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are at or above both fail thresholds after collection."),
        ReturnSpec(
            "FAILURE",
            "At least one player is still below a fail threshold after wisps were exhausted.",
        ),
    ],
    max_consecutive_retries=15,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: SprintyClient.calc_health_ratio(), SprintyClient.calc_mana_ratio(),
          SprintyClient.get_health_wisps(), SprintyClient.get_mana_wisps(),
          SprintyClient.get_mobs(), SprintyClient.teleport()

    Flow, one hop at a time with a fresh scan each hop:
      1. Read every client's ratios. Health wisps are wanted if any client is below
         if_health_below, mana wisps if any is below if_mana_below. If neither is wanted,
         stop — nobody moves.
      2. List the wanted wisps not visited yet, drop any within SAFE_RADIUS of an
         enemy (when only_safe), and pick the one nearest the group.
      3. Teleport every client to it at once. Stop when no wisp is left or the timeout
         expires.
      4. Re-check all ratios. Return FAILURE if any client's health is still below
         fail_if_health_below or mana is still below fail_if_mana_below.

    A visited wisp is never picked again, so one that lingers in memory after collection,
    or a ratio reading that never rises, ends the loop instead of spinning on one spot.

    FAILURE: any client is still below a fail threshold after wisps are exhausted or the
    timeout expires — e.g. a stale/bugged health ratio reading that never crosses the threshold.
    """
    if_health_below: float = params.get("if_health_below", 1.0)
    if_mana_below: float = params.get("if_mana_below", 1.0)
    timeout_raw: float = params.get("timeout", WISP_COLLECT_TIMEOUT)
    timeout_val: float | None = None if timeout_raw == 0.0 else timeout_raw
    fail_if_health_below: float = params.get("fail_if_health_below", 0.5)
    fail_if_mana_below: float = params.get("fail_if_mana_below", 0.5)
    only_safe: bool = params.get("only_safe", True)

    machine.log(
        f"collect_wisps: collect_health<{if_health_below}, collect_mana<{if_mana_below}, "
        f"timeout={timeout_raw}, fail_health<{fail_if_health_below}, fail_mana<{fail_if_mana_below}, "
        f"only_safe={only_safe}"
    )

    await collect_as_group(machine, clients, if_health_below, if_mana_below, timeout_val, only_safe)

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


async def collect_as_group(
    machine, clients: list, if_health_below: float, if_mana_below: float, timeout_val: float | None, only_safe: bool
) -> None:
    """Hop the whole group from wisp to wisp until nobody needs one, none is left, or time runs out.

    Every client lands on the same wisp at the same moment. The game gives a wisp to each
    wizard that reaches it together, and a wizard already full of that resource doesn't
    take it, so moving as one lets every client that needs it collect it. Each hop's DEBUG
    line records the ratios before it; the next hop's line, or the post-collection one,
    shows what it gave — which is how to tell whether arriving together is close enough.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    visited: set[int] = set()
    hop = 0

    while True:
        health, mana = zip(*await asyncio.gather(*[_get_ratios(c) for c in clients]))
        want_health = if_health_below > 0.0 and any(h < if_health_below for h in health)
        want_mana = if_mana_below > 0.0 and any(m < if_mana_below for m in mana)
        if not (want_health or want_mana):
            return
        if timeout_val is not None and loop.time() - start > timeout_val:
            machine.log(f"collect_wisps: timed out after {timeout_val}s", logging.DEBUG)
            return

        pick = await _pick_wisp(clients, want_health, want_mana, visited, only_safe)
        if pick is None:
            machine.log("collect_wisps: no wanted wisp left to collect", logging.DEBUG)
            return
        wisp_id, location, note = pick
        visited.add(wisp_id)
        hop += 1
        machine.log(
            f"collect_wisps: hop {hop} ({note}); before — "
            f"health: {[f'{h:.2f}' for h in health]}, mana: {[f'{m:.2f}' for m in mana]}",
            logging.DEBUG,
        )
        await asyncio.gather(*[c.teleport(location) for c in clients])
        await asyncio.sleep(jitter(WISP_COLLECT_DELAY))


async def _pick_wisp(
    clients: list, want_health: bool, want_mana: bool, visited: set[int], only_safe: bool
) -> tuple[int, XYZ, str] | None:
    """Return (id, location, log note) for the wanted wisp nearest the group, or None.

    Every client is in the same zone, so the first one's entity list stands for all.
    Nearest means the smallest total distance from every client, so the pick is the same
    whether or not the group already stands together.
    """
    lead = clients[0]
    wisps = []
    if want_health:
        wisps += await lead.get_health_wisps(visited)
    if want_mana:
        wisps += await lead.get_mana_wisps(visited)
    if not wisps:
        return None

    located = [(w, await w.location()) for w in wisps]
    if only_safe:
        clearances = await mob_clearances(lead, [loc for _w, loc in located])
        located = [(w, loc, c) for (w, loc), c in zip(located, clearances) if c >= SAFE_RADIUS]
        if not located:
            return None
    else:
        located = [(w, loc, None) for w, loc in located]

    positions = [await c.body.position() for c in clients]
    wisp, location, clearance = min(located, key=lambda wl: sum(wl[1].distance(p) for p in positions))
    if only_safe:
        note = f"{len(located)} of {len(wisps)} wisps safe, nearest enemy {clearance:.0f}"
    else:
        note = f"{len(wisps)} wisps, safety check off"
    return await wisp.global_id_full(), location, note


async def _get_ratios(client) -> tuple[float, float]:
    """Return (health_ratio, mana_ratio) for a client."""
    health, mana = await asyncio.gather(client.calc_health_ratio(), client.calc_mana_ratio())
    return health, mana
