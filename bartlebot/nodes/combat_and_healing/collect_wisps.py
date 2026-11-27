"""Node: collect health and mana wisps after combat."""

import asyncio
import logging
from dataclasses import dataclass, field

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
    threshold for a wisp's kind walk to it, from where they stand, and the rest stay still: a
    player after a fight is safe from mobs for 30 s only while it keeps completely still. A player
    that arrives after the wisp is taken still needs one, so the next scan sends it to another.

    Each round has up to two groups, the health walkers and the mana walkers, and each gets the
    wisp of its kind its players reach soonest in all, by the seconds their routes take
    (:func:`_pick_for`); both groups walk at once. A player short of both goes with the kind it
    is further below its fail threshold on, or with the other when its kind has no wisp in
    sight (:func:`_groups`). Near the timeout, a group whose walk would run past it goes only
    if one of its players is below that kind's fail threshold: that player fails the node
    otherwise, and the others would walk for nothing. Each hop's DEBUG line records the ratios
    before it and the seconds planned; the next hop's line, or the post-collection one, shows
    what it gave.

    `scaled_to` is the Challenge Mode level the ratios are read against (scaled_ratios), or
    None for the plain ratios.

    The fail thresholds and `safety_delay` decide whether the first scan waits for wisps to
    appear: for each kind a player is below the fail threshold of, until one of that kind is in
    sight. Their defaults never wait, which is what a caller with no fail threshold wants.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    visited: set[int] = set()
    fail_below = {HEALTH: fail_if_health_below, MANA: fail_if_mana_below}
    hop = 0
    first = True

    while True:
        health, mana = zip(*await asyncio.gather(*[scaled_ratios.ratios(c, scaled_to) for c in clients]))
        ratios = {HEALTH: health, MANA: mana}
        wanted = {
            kind
            for kind, below in ((HEALTH, if_health_below), (MANA, if_mana_below))
            if below > 0.0 and any(r < below for r in ratios[kind])
        }
        if not wanted:
            return
        elapsed = loop.time() - start
        if timeout_val is not None and elapsed > timeout_val:
            machine.log(f"collect_wisps: timed out after {timeout_val}s", logging.DEBUG)
            return
        left = None if timeout_val is None else timeout_val - elapsed

        needy_kinds = {k for k in wanted if 0.0 < fail_below[k] and any(r < fail_below[k] for r in ratios[k])}
        if first and needy_kinds and safety_delay > 0.0:
            machine.log(f"collect_wisps: no wisp in sight yet, waiting up to {safety_delay}s", logging.DEBUG)
            seen = await _await_wisps(clients[0], wanted, needy_kinds, visited, only_safe, safety_delay)
        else:
            seen = await _scan(clients[0], wanted, visited, only_safe)
        first = False

        in_sight = {kind for kind, s in seen.items() if s.safe}
        groups = _groups(health, mana, if_health_below, if_mana_below, fail_below, in_sight)
        picks = [
            pick
            for kind, walkers in groups.items()
            if (pick := await _pick_for(machine, clients, kind, walkers, seen[kind], visited, only_safe)) is not None
        ]
        if not picks:
            machine.log("collect_wisps: no wanted wisp left to collect", logging.DEBUG)
            return

        going: list[_Pick] = []
        skipped = False
        for pick in picks:
            needy = any(ratios[pick.kind][i] < fail_below[pick.kind] for i in pick.walkers)
            if left is not None and pick.planned is not None and pick.planned > left and not needy:
                machine.log(
                    f"collect_wisps: not walking {_players(pick.walkers)} to a {pick.kind} wisp: its "
                    f"{pick.planned:.1f}s walk runs past the {left:.1f}s left, and nobody in it is below "
                    "its fail threshold",
                    logging.DEBUG,
                )
                continue
            visited.add(pick.wisp_id)
            if only_safe and not await walking.passes_clear_of_patrols(
                machine, clients[pick.walkers[0]], pick.location
            ):
                machine.log(
                    f"collect_wisps: skipping a {pick.kind} wisp ({pick.note}): the walk there passes inside "
                    "a patrol's range",
                    logging.DEBUG,
                )
                skipped = True
                continue
            going.append(pick)
        if not going:
            if skipped:
                continue
            return

        for pick in going:
            hop += 1
            planned = "by straight line" if pick.planned is None else f"planned {pick.planned:.1f}s"
            machine.log(
                f"collect_wisps: hop {hop} to a {pick.kind} wisp ({pick.note}) for {_players(pick.walkers)}, "
                f"{planned}; before — health: {[f'{h:.2f}' for h in health]}, mana: {[f'{m:.2f}' for m in mana]}",
                logging.DEBUG,
            )
        await asyncio.gather(
            *[
                walking.walk_clients(
                    machine, [clients[i] for i in pick.walkers], pick.location, numbers=[i + 1 for i in pick.walkers]
                )
                for pick in going
            ]
        )
        await asyncio.sleep(jitter(WISP_COLLECT_DELAY))


HEALTH = "health"
MANA = "mana"


@dataclass
class _Candidate:
    """A wisp in sight, of *kind*, that the safety check let through."""

    wisp: object
    kind: str
    location: XYZ
    clearance: float | None  # to the nearest enemy, or None with the safety check off


@dataclass
class _Seen:
    """One kind's wisps on one scan: those safe to collect, and how many there were in all."""

    safe: list[_Candidate] = field(default_factory=list)
    total: int = 0


@dataclass
class _Pick:
    """The wisp one group walks to this round."""

    wisp_id: int
    location: XYZ
    kind: str
    walkers: list[int]  # each walker's place in the node's clients
    planned: float | None  # the slowest walker's seconds there, or None when picked by straight line
    note: str


def _players(walkers: list[int]) -> str:
    return ", ".join(f"P{i + 1}" for i in walkers)


async def _scan(lead, wanted: set[str], visited: set[int], only_safe: bool) -> dict[str, _Seen]:
    """Each wanted kind's wisps not visited yet, those within SAFE_RADIUS of an enemy left out when
    *only_safe*. Every client is in the same zone, so *lead*'s entity list stands for all."""
    wisps = []
    if HEALTH in wanted:
        wisps += [(w, HEALTH) for w in await lead.get_health_wisps(visited)]
    if MANA in wanted:
        wisps += [(w, MANA) for w in await lead.get_mana_wisps(visited)]
    seen = {kind: _Seen() for kind in wanted}
    if not wisps:
        return seen
    located = [(w, kind, await w.location()) for w, kind in wisps]
    if only_safe:
        clearances: list[float | None] = list(await mob_clearances(lead, [loc for _w, _k, loc in located]))
    else:
        clearances = [None] * len(located)
    for (wisp, kind, location), clearance in zip(located, clearances):
        seen[kind].total += 1
        if clearance is None or clearance >= SAFE_RADIUS:
            seen[kind].safe.append(_Candidate(wisp, kind, location, clearance))
    return seen


async def _await_wisps(
    lead, wanted: set[str], needy_kinds: set[str], visited: set[int], only_safe: bool, safety_delay: float
) -> dict[str, _Seen]:
    """Scan until a safe wisp of every kind in *needy_kinds* is in sight, or `safety_delay` seconds
    pass, and return the last scan: wisps spawn after a fight and load in after a move."""
    last: list[dict[str, _Seen]] = []

    async def _seen() -> bool:
        last[:] = [await _scan(lead, wanted, visited, only_safe)]
        return all(last[0][kind].safe for kind in needy_kinds)

    await poll_until(_seen, safety_delay, POLL_INTERVAL)
    return last[0] if last else {kind: _Seen() for kind in wanted}


def _groups(
    health, mana, if_health_below: float, if_mana_below: float, fail_below: dict[str, float], in_sight: set[str]
) -> dict[str, list[int]]:
    """Who walks for which kind this round, by each client's place: a kind's walkers are the
    clients below its collect threshold, for each kind with a safe wisp in sight.

    A client short of both goes with the kind it is further below its fail threshold on, health
    on a tie, so a player at risk of failing the node is served first; and with the other kind
    when its own has no wisp in sight, rather than standing still a round."""
    out: dict[str, list[int]] = {HEALTH: [], MANA: []}
    for i, (h, m) in enumerate(zip(health, mana)):
        short = [
            kind
            for kind, ratio, below in ((HEALTH, h, if_health_below), (MANA, m, if_mana_below))
            if below > 0.0 and ratio < below and kind in in_sight
        ]
        if len(short) == 2:
            out[HEALTH if h - fail_below[HEALTH] <= m - fail_below[MANA] else MANA].append(i)
        elif short:
            out[short[0]].append(i)
    return {kind: walkers for kind, walkers in out.items() if walkers}


async def _pick_for(
    machine, clients: list, kind: str, walkers: list[int], seen: _Seen, visited: set[int], only_safe: bool
) -> _Pick | None:
    """The safe wisp of *kind* that *walkers* reach soonest in all, or None.

    Soonest is the least total of each walker's seconds along the route its walk would plan
    (walking.seconds_to). A wisp some walker has no route to is dropped, and added to *visited*
    so later scans leave it out instead of searching every cell for it again. In a zone with no
    walking map it is the least total straight-line distance instead, as before routes were
    costed."""
    candidates = seen.safe
    walking_clients = [clients[i] for i in walkers]
    costs = await walking.seconds_to(machine, walking_clients, [c.location for c in candidates])
    if costs is None:
        positions = [await c.body.position() for c in walking_clients]
        best = min(candidates, key=lambda c: sum(c.location.distance(p) for p in positions))
        planned = None
    else:
        routed = []
        for k, candidate in enumerate(candidates):
            seconds = [row[k] for row in costs]
            if None in seconds:
                visited.add(await candidate.wisp.global_id_full())
                machine.log(
                    f"collect_wisps: dropping a {kind} wisp at {walking.point(candidate.location)}: no route there "
                    f"for {_players([i for i, s in zip(walkers, seconds) if s is None])}",
                    logging.DEBUG,
                )
            else:
                routed.append((candidate, seconds))
        if not routed:
            return None
        best, seconds = min(routed, key=lambda cs: sum(cs[1]))
        planned = max(seconds)
    if only_safe:
        note = f"{len(candidates)} of {seen.total} {kind} wisps safe, nearest enemy {best.clearance:.0f}"
    else:
        note = f"{seen.total} {kind} wisps, safety check off"
    return _Pick(await best.wisp.global_id_full(), best.location, kind, walkers, planned, note)
