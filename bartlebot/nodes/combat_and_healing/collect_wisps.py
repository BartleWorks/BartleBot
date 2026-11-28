"""Node: collect health and mana wisps after combat."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from bartleby.runtime.timing import (
    POLL_INTERVAL,
    WISP_COLLECT_DELAY,
    WISP_COLLECT_TIMEOUT,
    WISP_LINGER,
    WISP_LISTED_EVERY,
    WISP_TAKE_WAIT,
    jitter,
)
from wizwalker import XYZ

from bartlebot.nodes import walking
from bartlebot.nodes.combat_and_healing import scaled_ratios
from bartlebot.nodes.help_text import seconds
from bartlebot.nodes.node_helpers import SAFE_RADIUS, mob_clearances, poll_until
from bartlebot.nodes.param_schema import SAFETY_DELAY_OPTION, NodeSpec, ParamField, ReturnSpec

#: Units from a wisp within which a walk to it ends: 100 under the 401 to 418 from which a
#: wizard took a health wisp (core's ``docs/WIZARD101.md``, *How wisps move*). Mana wisps were
#: not measured, so a walker that ends here with no pickup closes in (:func:`_walk_to_wisp`).
WISP_REACH = 300.0

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
        help="The longest to go without a wisp helping anyone. 0 means no limit.",
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
        "Collects health and mana wisps after a fight, until every player meets its thresholds, "
        "is full by the game's word, or no wisps are left. Fails if a player is still below the "
        "fail thresholds, so you can route to a potion node."
    ),
    params=PARAM_SCHEMA,
    required_bot_options=[SAFETY_DELAY_OPTION, walking.AVOID_MOBS_OPTION],
    returns=[
        ReturnSpec("SUCCESS", "Every player is at or above both fail thresholds, or full by the game's word."),
        ReturnSpec(
            "FAILURE",
            "A player is still below a fail threshold.",
        ),
    ],
    max_consecutive_retries=15,
    usage_notes=(
        (
            "Only the players below a threshold walk. Those short of health walk to a health wisp "
            "and those short of mana to a mana wisp, both at once, and the rest stay where they "
            "are, which keeps them safe from enemies for a while after a fight. A player who "
            "arrives after the wisp is taken goes on to the next one."
        )
        + "\n\n"
        + (
            "Each wisp is chosen by how long the walk there takes, not by how near it looks, so a "
            "wisp behind a wall loses to one farther off with a shorter way around. A player short "
            "of both goes first for the one it is nearer failing on. The timeout counts from the "
            "last time a wisp helped anyone, so a long walk that pays off is never cut short; a "
            "walk that would run past it is made only for a player below a fail threshold."
        )
        + "\n\n"
        + (
            f"Wisps drift, so a walk follows the wisp as it moves, and stops about {WISP_REACH:.0f} "
            "units short of it: a wizard picks a wisp up from farther off than that, and the wisp "
            "flies the rest of the way. If nobody who walked to it got it and it is still there, "
            "they walk right up to it once."
        )
        + "\n\n"
        + (
            "A player's maximum health sometimes reads higher than it is, so a player at full "
            "health can look short. When a player walked right up to a wisp, got nothing, and the "
            "wisp is still there a moment later, the game is saying that player is full of that "
            "kind: it walks to no more of them, and doesn't fail the node on it. That costs one "
            "wasted walk at most."
        )
        + "\n\n"
        + (
            "Place it right after a fight or a zone change. Wisps take a moment to appear, so when "
            "a player is below a fail threshold and no wisp of that kind is in sight, the node "
            "waits up to Safety Delay for one. If nobody is below a fail threshold, it doesn't wait."
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
          SprintyClient.get_mobs(), walking.seconds_to(), walking.walk_clients()

    Flow, a round at a time with a fresh scan each round (collect_as_group):
      1. Read every client's ratios. Health wisps are wanted if any client is below
         if_health_below, mana wisps if any is below if_mana_below. If neither is wanted,
         stop — nobody moves.
      2. List the wanted wisps not visited yet, and drop any within SAFE_RADIUS of an enemy
         (when only_safe). On the first round, when a client is below a fail threshold for a
         wanted kind and no wisp of that kind qualifies, keep scanning for up to Safety Delay:
         wisps spawn after a fight and load in after a move, and failing before they do sends
         the group to the potion route for health a wisp was about to give.
      3. Split the clients short of a kind into a health group and a mana group; one short of
         both goes with the kind it is further below its fail threshold on. Each group picks
         the wisp of its kind with the least total walking seconds from its walkers
         (walking.seconds_to), dropping one any walker has no route to; with no walking map,
         the least total straight-line distance. With only_safe, a wisp whose route passes
         inside the trigger radius of a patrol a live mob walks is skipped too; a beaten mob's
         patrol is no danger (walking.passes_clear_of_patrols).
      4. Walk both groups at once; the rest stay still, keeping their ghost window. Near the
         timeout, a group whose planned walk runs past the time left goes only if one of its
         walkers is below that kind's fail threshold. Each walk follows its wisp as it moves and
         ends within WISP_REACH of it, or once it is gone (_WispGoal); when nobody in the group
         got it and it is still there, those whose walks arrived walk right up to it once
         (_walk_to_wisp). One that closed in, took nothing, and still sees the wisp listed
         WISP_LINGER later is full of that kind by the game's word, for the rest of the node
         unless its ratio rises. Stop when no wisp is left, or when the timeout has passed
         since a hop last raised anyone's ratio.
      5. Re-check all ratios. Return FAILURE if any client's health is still below
         fail_if_health_below or mana is still below fail_if_mana_below, unless the game
         said it is full of that kind (logged at INFO).

    A visited wisp is never picked again, so one that lingers in memory after collection,
    or a ratio reading that never rises, ends the loop instead of spinning on one spot.

    FAILURE: any client is still below a fail threshold after wisps are exhausted or the
    timeout expires, and was never found full of that kind.
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

    full = await collect_as_group(
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

    final = {HEALTH: final_health, MANA: final_mana}
    for kind, below in ((HEALTH, fail_if_health_below), (MANA, fail_if_mana_below)):
        for i in sorted(full.of(kind, final[kind])):
            if final[kind][i] < below:
                machine.log(
                    f"collect_wisps: P{i + 1}'s {kind} reads {final[kind][i]:.2f}, under the fail threshold "
                    f"{below}, but the game gave it no {kind} wisp: counted full"
                )
    health_failures = [
        i for i, h in enumerate(final_health) if h < fail_if_health_below and i not in full.of(HEALTH, final_health)
    ]
    mana_failures = [
        i for i, m in enumerate(final_mana) if m < fail_if_mana_below and i not in full.of(MANA, final_mana)
    ]

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
) -> "_Full":
    """Walk the players who need a wisp from wisp to wisp until nobody needs one, none is left,
    or no wisp has helped anyone for `timeout_val` seconds, and return who the game said is full.

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

    The timeout is a progress deadline: its clock restarts whenever a round's ratios show a rise
    for anyone, so a slow hop that delivers is never cut short, and hops that deliver nothing
    still end. A walker the game said is full of a kind (:func:`_walk_to_wisp`) counts as not
    short of it, so it walks to no more of that kind, until its ratio of that kind rises, which
    withdraws the verdict (:class:`_Full`).

    `scaled_to` is the Challenge Mode level the ratios are read against (scaled_ratios), or
    None for the plain ratios.

    The fail thresholds and `safety_delay` decide whether the first scan waits for wisps to
    appear: for each kind a player is below the fail threshold of, until one of that kind is in
    sight. Their defaults never wait, which is what a caller with no fail threshold wants.
    """
    helped_at = time.monotonic()  # when a round last saw anyone's ratio rise, or the start
    visited: set[int] = set()
    fail_below = {HEALTH: fail_if_health_below, MANA: fail_if_mana_below}
    full = _Full()
    hop = 0
    first = True
    previous: dict[str, tuple] | None = None

    while True:
        read_health, read_mana = zip(*await asyncio.gather(*[scaled_ratios.ratios(c, scaled_to) for c in clients]))
        read = {HEALTH: read_health, MANA: read_mana}
        if previous is not None and any(
            now > before for kind in read for now, before in zip(read[kind], previous[kind])
        ):
            helped_at = time.monotonic()
        previous = read
        full.withdraw_risen(machine, read)
        # A player full by the game's word is not short of that kind, whatever its ratio reads.
        health, mana = (full.as_read(kind, read[kind]) for kind in (HEALTH, MANA))
        ratios = {HEALTH: health, MANA: mana}
        wanted = {
            kind
            for kind, below in ((HEALTH, if_health_below), (MANA, if_mana_below))
            if below > 0.0 and any(r < below for r in ratios[kind])
        }
        if not wanted:
            return full
        elapsed = time.monotonic() - helped_at
        if timeout_val is not None and elapsed > timeout_val:
            machine.log(f"collect_wisps: no wisp has helped anyone for {timeout_val}s; stopping", logging.DEBUG)
            return full
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
            return full

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
            return full

        for pick in going:
            hop += 1
            planned = "by straight line" if pick.planned is None else f"planned {pick.planned:.1f}s"
            machine.log(
                f"collect_wisps: hop {hop} to a {pick.kind} wisp ({pick.note}) for {_players(pick.walkers)}, "
                f"{planned}; before — health: {[f'{h:.2f}' for h in read_health]}, "
                f"mana: {[f'{m:.2f}' for m in read_mana]}",
                logging.DEBUG,
            )
        verdicts = await asyncio.gather(
            *[_walk_to_wisp(machine, clients, pick, read[pick.kind], scaled_to) for pick in going]
        )
        for pick, found in zip(going, verdicts):
            for i, ratio in found.items():
                full.add(pick.kind, i, ratio)
        await asyncio.sleep(jitter(WISP_COLLECT_DELAY))


HEALTH = "health"
MANA = "mana"


class _Full:
    """The players the game said are full, by kind: each one's place in the node's clients, and
    the ratio of that kind it read when the game said so. Its max health may read higher than it
    is, so the ratio itself says nothing; a later rise in it does, and withdraws the verdict,
    since the game gave that player something after all."""

    def __init__(self) -> None:
        self._at: dict[str, dict[int, float]] = {HEALTH: {}, MANA: {}}

    def add(self, kind: str, i: int, ratio: float) -> None:
        self._at[kind][i] = ratio

    def of(self, kind: str, read) -> set[int]:
        """Who is full of *kind*, given every client's ratio of it as read now."""
        return {i for i, ratio in self._at[kind].items() if read[i] <= ratio}

    def withdraw_risen(self, machine, read: dict[str, tuple]) -> None:
        """Drop every verdict whose ratio has risen since."""
        for kind, at in self._at.items():
            for i in [i for i, ratio in at.items() if read[kind][i] > ratio]:
                del at[i]
                machine.log(
                    f"collect_wisps: P{i + 1}'s {kind} rose to {read[kind][i]:.2f}: not full after all", logging.DEBUG
                )

    def as_read(self, kind: str, read) -> tuple:
        """*read*, with each player full of *kind* reading as full."""
        full = self.of(kind, read)
        return tuple(1.0 if i in full else ratio for i, ratio in enumerate(read))


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

    wisp: object
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
    return _Pick(best.wisp, await best.wisp.global_id_full(), best.location, kind, walkers, planned, note)


class _WispGoal:
    """Where *pick*'s wisp is now, read afresh on every call, or None once it is gone: what a
    walk to it follows (core's ``GoalRead``). Gone is an entity that no longer reads, or reads
    as another one, or, checked every :data:`WISP_LISTED_EVERY`, one *lead*'s list of that kind
    no longer holds, since a freed entity can go on reading the numbers it had. Once gone it
    stays gone. Every walker of the group shares one."""

    def __init__(self, lead, pick: _Pick) -> None:
        self.lead = lead
        self.pick = pick
        self.gone = False
        self._listed_at = time.monotonic()  # the scan that picked it had it listed

    async def __call__(self):
        if self.gone:
            return None
        wisp = self.pick.wisp
        try:
            same = await wisp.global_id_full() == self.pick.wisp_id
            at = await wisp.location()
        except Exception:  # noqa: BLE001 - freed: taken, or despawned
            same = False
        if same and time.monotonic() - self._listed_at >= WISP_LISTED_EVERY:
            self._listed_at = time.monotonic()
            same = self.pick.wisp_id in await _listed_ids(self.lead, self.pick.kind)
        if not same:
            self.gone = True
            return None
        return (float(at.x), float(at.y), float(at.z))


async def _listed_ids(lead, kind: str) -> set[int]:
    """The ids of every wisp of *kind* *lead*'s client lists."""
    listed = await (lead.get_health_wisps() if kind == HEALTH else lead.get_mana_wisps())
    ids = set()
    for wisp in listed:
        try:
            ids.add(await wisp.global_id_full())
        except Exception:  # noqa: BLE001, S112 - freed while listed
            continue
    return ids


async def _walk_to_wisp(machine, clients: list, pick: _Pick, before, scaled_to: int | None) -> dict[int, float]:
    """Walk *pick*'s walkers to its wisp, following it as it moves, each ending within
    :data:`WISP_REACH` of it or once it is gone, and return the walkers the game said are full of
    its kind: each one's place in the node's clients, with the ratio it read. *before* is every
    client's ratio of the pick's kind before the walk.

    When nobody who walked had that ratio rise :data:`WISP_TAKE_WAIT` after, and the wisp is
    still there, those whose walks arrived walk on to it once, to the walker's own arrival
    distance: a smaller reach than the health wisps' (mana wisps were not measured), or a still
    wizard beside a wisp that barely moves, would otherwise be taken for the game's word that
    the player is full. A rise for any of them means the wisp was taken, so nobody closes in.

    Those whose close-in arrived beside the wisp are full when, :data:`WISP_LINGER` later, nobody
    in the group has had a rise and the client still lists the wisp: a wisp someone else took
    stays listed for 1.3 to 1.8 s, so a read any sooner would call a player full who was only
    too late."""
    goal = _WispGoal(clients[0], pick)
    walkers = [clients[i] for i in pick.walkers]
    numbers = [i + 1 for i in pick.walkers]
    walked = await walking.walk_clients(machine, walkers, goal, reach=WISP_REACH, numbers=numbers)
    await asyncio.sleep(WISP_TAKE_WAIT)
    now = await _ratios_of(clients, pick, scaled_to)
    if any(now[i] > before[i] for i in pick.walkers):
        return {}
    closing = [i for i, w in zip(pick.walkers, walked) if w.ok]
    if not closing or await goal() is None:
        return {}
    machine.log(
        f"collect_wisps: {_players(closing)} came within {WISP_REACH:.0f} of the "
        f"{pick.kind} wisp and nobody took it: walking up to it",
        logging.DEBUG,
    )
    closed = await walking.walk_clients(machine, [clients[i] for i in closing], goal, numbers=[i + 1 for i in closing])
    beside = [i for i, w in zip(closing, closed) if w.ok]
    if not beside or goal.gone:
        return {}
    await asyncio.sleep(WISP_LINGER)
    now = await _ratios_of(clients, pick, scaled_to)
    if any(now[i] > before[i] for i in pick.walkers):
        return {}
    if await goal() is None or pick.wisp_id not in await _listed_ids(clients[0], pick.kind):
        return {}
    for i in beside:
        machine.log(
            f"collect_wisps: P{i + 1} is full of {pick.kind} by the game's word: it walked up to a "
            f"{pick.kind} wisp that stayed, and its ratio stayed {now[i]:.2f}",
            logging.DEBUG,
        )
    return {i: now[i] for i in beside}


async def _ratios_of(clients: list, pick: _Pick, scaled_to: int | None) -> dict[int, float]:
    """Each of *pick*'s walkers' ratio of its kind now, by the walker's place in *clients*."""
    side = 0 if pick.kind == HEALTH else 1
    return {i: (await scaled_ratios.ratios(clients[i], scaled_to))[side] for i in pick.walkers}
