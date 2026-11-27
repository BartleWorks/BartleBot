"""Node: collect health and mana wisps after combat."""

import asyncio
import logging

from bartleby.runtime.timing import POLL_INTERVAL, WISP_COLLECT_DELAY, WISP_COLLECT_TIMEOUT, jitter
from wizwalker import XYZ

from bartlebot.nodes import walking
from bartlebot.nodes.combat_and_healing import scaled_ratios
from bartlebot.nodes.help_text import seconds
from bartlebot.nodes.node_helpers import SAFE_RADIUS, mob_clearances, poll_until
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="if_health_below",
        type="ratio",
        label="Collect Health Wisps If Below",
        help="Players below this share of their health collect health wisps. At 0%, health wisps are skipped.",
        default=1.0,
        default_hint="below full health",
    ),
    ParamField(
        key="if_mana_below",
        type="ratio",
        label="Collect Mana Wisps If Below",
        help="Players below this share of their mana collect mana wisps. At 0%, mana wisps are skipped.",
        default=1.0,
        default_hint="below full mana",
    ),
    ParamField(
        key="timeout",
        type="float",
        label="Timeout (s)",
        help="The longest to spend collecting. 0 means no limit.",
        default=WISP_COLLECT_TIMEOUT,
        default_hint=seconds(WISP_COLLECT_TIMEOUT),
    ),
    ParamField(
        key="fail_if_health_below",
        type="ratio",
        label="Fail If Health Still Below",
        help=(
            "After collecting, the node fails if any player's health is still below this. At 0%, it "
            "never fails on health."
        ),
        default=0.5,
        default_hint="50%",
    ),
    ParamField(
        key="fail_if_mana_below",
        type="ratio",
        label="Fail If Mana Still Below",
        help=(
            "After collecting, the node fails if any player's mana is still below this. At 0%, it never fails on mana."
        ),
        default=0.5,
        default_hint="50%",
    ),
    ParamField(
        key="only_safe",
        type="bool",
        label="Only Collect Safe Wisps",
        help=(
            f"When on, skips wisps within {SAFE_RADIUS:.0f} units of an enemy, and wisps the walk to "
            "would pass inside a patrolling enemy's range, so collecting doesn't start a fight. Turn "
            "off only where enemies are always nearby."
        ),
        default=True,
        default_hint="skip wisps near enemies",
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Collects health and mana wisps after a fight, until every player meets its thresholds or "
        "no wisps are left. Fails if a player is still below the fail thresholds, so you can "
        "route to a potion node."
    ),
    params=PARAM_SCHEMA,
    required_bot_options=[SAFETY_DELAY_OPTION, walking.AVOID_MOBS_OPTION],
    returns=[
        ReturnSpec("SUCCESS", "Every player is at or above both fail thresholds."),
        ReturnSpec(
            "FAILURE",
            "A player is still below a fail threshold.",
        ),
    ],
    max_consecutive_retries=15,
    usage_notes=(
        (
            "Only the players below a threshold walk, all to the same wisp at once. The rest stay "
            "where they are, which keeps them safe from enemies for a while after a fight. A player "
            "who arrives after the wisp is taken goes on to the next one."
        )
        + "\n\n"
        + (
            "Place it right after a fight or a zone change. Wisps take a moment to appear, so when "
            "none is in sight and a player is below a fail threshold, the node waits up to Safety "
            "Delay for one before it gives up. If nobody is below a fail threshold, it doesn't wait."
        )
        + "\n\n"
        + (
            "With Challenge Mode on, a player outside the dungeon is measured against the lower "
            "maximum its next entry scales it to, as in Handle Potions."
        )
    ),
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: scaled_ratios.ratios() (SprintyClient.calc_health_ratio(), calc_mana_ratio()),
          SprintyClient.get_health_wisps(), SprintyClient.get_mana_wisps(),
          SprintyClient.get_mobs(), walking.walk_clients()

    Flow, one wisp at a time with a fresh scan each time:
      1. Read every client's ratios. Health wisps are wanted if any client is below
         if_health_below, mana wisps if any is below if_mana_below. If neither is wanted,
         stop — nobody moves.
      2. List the wanted wisps not visited yet, drop any within SAFE_RADIUS of an
         enemy (when only_safe), and pick the one nearest the clients that need one. On
         the first, when nothing qualifies and a client is below a fail threshold for a
         wanted resource, keep scanning for up to Safety Delay: wisps spawn after a fight
         and load in after a move, and failing before they do sends the group to the
         potion route for health a wisp was about to give. With only_safe, a wisp whose
         route passes inside the trigger radius of a patrol a live mob walks is skipped
         too; a beaten mob's patrol is no danger (walking.passes_clear_of_patrols).
      3. Walk the clients below the threshold for that wisp's kind to it, all at once; the
         rest stay still, keeping their ghost window. Stop when no wisp is left or the
         timeout expires.
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
    safety_delay: float = machine.get_option("Safety Delay", 3.0)
    scaled_to = scaled_ratios.challenge_level(machine)

    machine.log(
        f"collect_wisps: collect_health<{if_health_below}, collect_mana<{if_mana_below}, "
        f"timeout={timeout_raw}, fail_health<{fail_if_health_below}, fail_mana<{fail_if_mana_below}, "
        f"only_safe={only_safe}, scaled_to={scaled_to}, safety_delay={safety_delay}"
    )

    await collect_as_group(
        machine,
        clients,
        if_health_below,
        if_mana_below,
        timeout_val,
        only_safe,
        scaled_to,
        fail_if_health_below=fail_if_health_below,
        fail_if_mana_below=fail_if_mana_below,
        safety_delay=safety_delay,
    )

    # Post-collection check: return FAILURE if any client is still below a fail threshold.
    final_health, final_mana = zip(*await asyncio.gather(*[scaled_ratios.ratios(c, scaled_to) for c in clients]))

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
    machine,
    clients: list,
    if_health_below: float,
    if_mana_below: float,
    timeout_val: float | None,
    only_safe: bool,
    scaled_to: int | None = None,
    *,
    fail_if_health_below: float = 0.0,
    fail_if_mana_below: float = 0.0,
    safety_delay: float = 0.0,
) -> None:
    """Walk the players who need a wisp from wisp to wisp until nobody needs one, none is left,
    or time runs out.

    The game gives a wisp to each wizard that reaches it together, and only to the first
    otherwise. Walking, the players no longer arrive at one moment, so only those below the
    threshold for a wisp's kind walk to it, all at once from where they stand, and the rest stay
    still: a player after a fight is safe from mobs for 30 s only while it keeps completely
    still. A player that arrives after the wisp is taken still needs one, so the next scan sends
    it to another. Each hop's DEBUG line records the ratios before it; the next hop's line, or the
    post-collection one, shows what it gave.

    `scaled_to` is the Challenge Mode level the ratios are read against (scaled_ratios), or
    None for the plain ratios.

    The fail thresholds and `safety_delay` decide whether the first scan waits for wisps to
    appear. Their defaults never wait, which is what a caller with no fail threshold wants.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    visited: set[int] = set()
    hop = 0

    while True:
        health, mana = zip(*await asyncio.gather(*[scaled_ratios.ratios(c, scaled_to) for c in clients]))
        want_health = if_health_below > 0.0 and any(h < if_health_below for h in health)
        want_mana = if_mana_below > 0.0 and any(m < if_mana_below for m in mana)
        if not (want_health or want_mana):
            return
        if timeout_val is not None and loop.time() - start > timeout_val:
            machine.log(f"collect_wisps: timed out after {timeout_val}s", logging.DEBUG)
            return

        short_of_fail = (
            want_health and 0.0 < fail_if_health_below and any(h < fail_if_health_below for h in health)
        ) or (want_mana and 0.0 < fail_if_mana_below and any(m < fail_if_mana_below for m in mana))
        needy = [
            c
            for c, h, m in zip(clients, health, mana)
            if (want_health and h < if_health_below) or (want_mana and m < if_mana_below)
        ]
        if short_of_fail and safety_delay > 0.0 and not visited:
            machine.log(f"collect_wisps: no wisp in sight yet, waiting up to {safety_delay}s", logging.DEBUG)
            pick = await _await_wisp(clients, needy, want_health, want_mana, visited, only_safe, safety_delay)
        else:
            pick = await _pick_wisp(clients, needy, want_health, want_mana, visited, only_safe)
        if pick is None:
            machine.log("collect_wisps: no wanted wisp left to collect", logging.DEBUG)
            return
        wisp_id, location, note, kind = pick
        visited.add(wisp_id)
        below = if_health_below if kind == HEALTH else if_mana_below
        ratios = health if kind == HEALTH else mana
        walkers = [i for i, (c, r) in enumerate(zip(clients, ratios)) if r < below]
        if only_safe and not await walking.passes_clear_of_patrols(machine, clients[walkers[0]], location):
            machine.log(
                f"collect_wisps: skipping a {kind} wisp ({note}): the walk there passes inside a patrol's range",
                logging.DEBUG,
            )
            continue
        hop += 1
        machine.log(
            f"collect_wisps: hop {hop} to a {kind} wisp ({note}) for {', '.join(f'P{i + 1}' for i in walkers)};"
            f" before — health: {[f'{h:.2f}' for h in health]}, mana: {[f'{m:.2f}' for m in mana]}",
            logging.DEBUG,
        )
        await walking.walk_clients(machine, [clients[i] for i in walkers], location, numbers=[i + 1 for i in walkers])
        await asyncio.sleep(jitter(WISP_COLLECT_DELAY))


async def _await_wisp(
    clients: list,
    needy: list,
    want_health: bool,
    want_mana: bool,
    visited: set[int],
    only_safe: bool,
    safety_delay: float,
) -> tuple[int, XYZ, str, str] | None:
    """Scan for a wanted wisp until one qualifies or `safety_delay` seconds pass, and return it or None."""
    found: list[tuple[int, XYZ, str, str]] = []

    async def _seen() -> bool:
        pick = await _pick_wisp(clients, needy, want_health, want_mana, visited, only_safe)
        if pick is not None:
            found.append(pick)
        return pick is not None

    await poll_until(_seen, safety_delay, POLL_INTERVAL)
    return found[0] if found else None


HEALTH = "health"
MANA = "mana"


async def _pick_wisp(
    clients: list, needy: list, want_health: bool, want_mana: bool, visited: set[int], only_safe: bool
) -> tuple[int, XYZ, str, str] | None:
    """Return (id, location, log note, kind) for the wanted wisp nearest the needy clients, or None.

    Every client is in the same zone, so the first one's entity list stands for all.
    Nearest means the smallest total distance from every client in *needy* (all of them when
    none is), since only they walk to it.
    """
    lead = clients[0]
    wisps = []
    if want_health:
        wisps += [(w, HEALTH) for w in await lead.get_health_wisps(visited)]
    if want_mana:
        wisps += [(w, MANA) for w in await lead.get_mana_wisps(visited)]
    if not wisps:
        return None

    located = [(w, kind, await w.location()) for w, kind in wisps]
    if only_safe:
        clearances = await mob_clearances(lead, [loc for _w, _k, loc in located])
        located = [(w, kind, loc, c) for (w, kind, loc), c in zip(located, clearances) if c >= SAFE_RADIUS]
        if not located:
            return None
    else:
        located = [(w, kind, loc, None) for w, kind, loc in located]

    positions = [await c.body.position() for c in (needy or clients)]
    wisp, kind, location, clearance = min(located, key=lambda wl: sum(wl[2].distance(p) for p in positions))
    if only_safe:
        note = f"{len(located)} of {len(wisps)} wisps safe, nearest enemy {clearance:.0f}"
    else:
        note = f"{len(wisps)} wisps, safety check off"
    return await wisp.global_id_full(), location, note, kind
