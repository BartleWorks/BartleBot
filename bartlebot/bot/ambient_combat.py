"""One ambient combat session: a fight no node started, from detection until the FSM resumes (#116).

``BotMachine._ambient_combat_watcher`` notices a client in battle, pauses the FSM, cancels the
running node and waits for the loop to park. Everything after that is here:

1. Players proven to share a fighter's realm are its **helpers**. Each records where it stands
   (zone, position, mob clearance) and teleports to the fight's center.
   Everyone else helps too, by **porting** to a fighter first -- which puts the porter on the
   fighter's realm -- unless the bot keeps its players to their own realms (Player Realms is
   Separate) or has no port routes. Then they are left where they are. A port that is
   refused leaves that player where it is too.
2. The fighter's first cast is **held** until its helpers are in, or have given up, or
   AMBIENT_HELP_HOLD has passed since the later of its first planning phase and the latest
   join. The game restarts its 30s planning timer on every join, so the fighter still casts
   in round one.
3. **Every client seen in battle** gets its combat run with its own slot's strategy, whatever
   brought it there: a helper, a client that walked in, a bystander pulled into a second fight.
4. After a **won** fight, anyone low collects wisps, then helpers still in the zone they
   started in go back to where they started, and everyone else -- a helper that ported in from
   another zone included -- to the nearest spot a player has stood on without being pulled
   in. Anyone who lands within SAFE_RADIUS of a mob, or has nowhere to go, goes to the hub.
   Nobody moves before that: the ghost window after a fight lasts 30s only while a player
   keeps completely still.
5. After a **fled** fight the game has already put the players in their hub with empty mana,
   so they collect wisps there. A flee from the game's idle popup (#203) counts too: the
   machine's idle watcher clicks it and reports it here through :meth:`AmbientSession.note_fled`.

The session ends with the FSM told whether everyone is back in the zone it started in. If so
the interrupted state runs again; if not, the bot goes to its recovery state.

Players are named by number in every log line, never by name.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from bartlebot.clients import port_map
from bartlebot.nodes import combat_runner, node_helpers
from bartlebot.nodes.combat_and_healing import collect_wisps
from bartlebot.nodes.timing import (
    AMBIENT_COMBAT_MAX_DURATION,
    AMBIENT_HELP_HOLD,
    ENTER_COMBAT_TIMEOUT,
    POLL_INTERVAL,
    WISP_COLLECT_TIMEOUT,
)
from bartlebot.options import player_realms

logger = logging.getLogger(__name__)

#: Below this health or mana ratio a player collects wisps before it moves: collect_wisps'
#: default fail thresholds. A player defeated in a fight the group won is at 1 health.
WISP_BELOW = 0.5


@dataclass
class _Start:
    """Where a helper stood before it left to help."""

    zone: str | None
    position: object | None
    clearance: float
    """Distance to the nearest mob the helper had loaded there. Read before it leaves: after the
    fight everyone stands in the duel circle, which may be too far away to have those mobs loaded."""


@dataclass
class SessionEnd:
    """What the machine needs from a session."""

    fight: combat_runner.FightResult
    fled: int = 0
    """How many fights were fled; each counts toward the flee limit."""
    recover: bool = False
    """Someone is not in the zone it started in, or had to be sent to the hub: go to recovery."""


@dataclass
class _Hold:
    """One fighter's held first cast."""

    helpers: list
    planning_at: float | None = None
    released: bool = False


@dataclass
class AmbientSession:
    """One fight session. Build one per detection and :meth:`run` it once.

    *may_move* is False when the watcher could not get the node out of the way (relog, which
    is never cancelled, or a node that would not stop): the session then fights for whoever is
    in battle and moves nobody, as the watcher did before #116.
    """

    machine: object
    clients: list
    fighters: list
    may_move: bool = True
    helpers: dict = field(default_factory=dict)  # helper -> the fighter it helps
    starts: dict = field(default_factory=dict)  # helper -> _Start
    start_zones: dict = field(default_factory=dict)  # client -> zone at detection
    participants: list = field(default_factory=list)  # every client that fought, in order seen
    joined_at: dict = field(default_factory=dict)  # helper -> when it read in battle
    force_recovery: bool = False
    idle_fled: set = field(default_factory=set)  # player numbers fled from the idle popup
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self):
        self._slot = {c: i for i, c in enumerate(self.clients)}
        self._detected = self.clock()

    def note_fled(self, client) -> None:
        """Record a flee made outside the session's combat: the machine's idle watcher answering
        "You Are Idle" (#203). The fight then ends as a fled one, counted toward the flee limit."""
        self.idle_fled.add(self._slot[client] + 1)

    def _p(self, client) -> str:
        return f"P{self._slot[client] + 1}"

    def _names(self, clients) -> str:
        return ", ".join(self._p(c) for c in clients)

    @staticmethod
    def _is(clients) -> str:
        return "is" if len(clients) == 1 else "are"

    def _log(self, message: str, level: int = logging.INFO) -> None:
        # The loop has parked, so machine.log files under the machine rather than a node.
        self.machine.log(f"Ambient combat: {message}", level)

    # ------------------------------------------------------------------ the session

    async def run(self) -> SessionEnd:
        machine, clients = self.machine, self.clients
        dsls, _order, source, _level = combat_runner.resolve_combat(
            machine, use_default_strategy=True, num_clients=len(clients), ambient=True
        )
        self._log(f"DSL source={source!r}", logging.DEBUG)

        zones = await asyncio.gather(*(_zone(c) for c in clients))
        self.start_zones = dict(zip(clients, zones))
        centers = combat_runner.FightCenter(share=lambda a, b: machine.are_together([a, b]))
        apart = await self._find_helpers() if self.may_move else []
        porters = await self._find_porters(apart)
        for fighter in self.fighters:
            await centers.publish(machine, fighter, self._slot[fighter])

        run = combat_runner.CombatRun({c: self._slot[c] + 1 for c in clients}, machine.log, AMBIENT_COMBAT_MAX_DURATION)
        await run.identify()
        holds = {f: _Hold([h for h, hf in self.helpers.items() if hf is f]) for f in self.fighters}
        ported = asyncio.create_task(self._port(porters)) if porters else None
        joins = {
            h: asyncio.create_task(self._port_and_join(h, ported, centers) if h in porters else self._join(h, centers))
            for h in self.helpers
        }
        try:
            await self._fight(run, centers, dsls, holds, joins)
        finally:
            for task in joins.values():
                task.cancel()
            if ported is not None:
                ported.cancel()
            run.cancel()
        fight = run.result()
        for number, exc in fight.errors:
            self._log(f"error on P{number}: {exc}", logging.DEBUG)
        if fight.end is combat_runner.FightEnd.STUCK:
            self._log("the fight did not end after a flee was called for", logging.WARNING)

        if self.participants:
            # In the background, so the FSM resumes as soon as the fight is over.
            combat_runner.start_combat_drop_poll(machine, self.participants, [self._slot[c] for c in self.participants])

        if not self.may_move:
            return SessionEnd(fight, fled=int(fight.end is combat_runner.FightEnd.FLED or bool(self.idle_fled)))
        if fight.end is combat_runner.FightEnd.STUCK:
            return SessionEnd(fight, recover=True)  # players still in battle: moving them does nothing
        fled = await self._leave(centers, run.watch.fled | self.idle_fled)
        return SessionEnd(fight, fled=fled, recover=self.force_recovery or not await self._all_home())

    async def _find_helpers(self) -> list:
        """Every client not in battle that is proven to share a fighter's realm, and where it stands.

        Returns the rest of the clients not in battle: the ones that would have to port to help.
        """
        machine = self.machine
        await node_helpers.read_sightings(self.clients, machine)
        apart = []
        for client in self.clients:
            if client in self.fighters:
                continue
            fighter = next((f for f in self.fighters if machine.are_together([client, f])), None)
            if fighter is None:
                apart.append(client)
                continue
            await self._record_start(client)
            self.helpers[client] = fighter
        if self.helpers:
            self._log(f"{self._names(self.helpers)} {self._is(self.helpers)} joining the fight")
        return apart

    async def _find_porters(self, apart: list) -> list:
        """Which of *apart* help by porting to the first fighter (Phase B), recorded as its helpers.

        A friends-list port moves the porter onto the target's realm. That is what makes help
        across realms work, and what would break a Separate bot's split, so a Separate bot
        never ports. Without a port map there are no known routes to port along.
        """
        if not apart:
            return []
        why = None
        if player_realms.machine_is_separate(self.machine):
            why = f"this bot keeps its players to their own realms ({player_realms.OPTION})"
        elif self.machine.port_map is None:
            why = "there are no port routes to reach it by"
        if why is not None:
            where = "it is" if len(apart) == 1 else "they are"
            self._log(
                f"{self._names(apart)} {self._is(apart)} not known to share a fighter's realm, and {why};"
                f" left where {where}"
            )
            return []
        fighter = self.fighters[0]
        for client in apart:
            await self._record_start(client)
            self.helpers[client] = fighter
        self._log(f"{self._names(apart)} {self._is(apart)} porting to {self._p(fighter)} to join the fight")
        return list(apart)

    async def _record_start(self, client) -> None:
        """Record where *client* stands, and how clear of mobs it is there, before it leaves to help."""
        position = await _position(client)
        clearance = 0.0
        if position is not None:
            try:
                clearance = await node_helpers.mob_clearance(client, position)
            except Exception:
                logger.debug("Could not read mobs around a helper", exc_info=True)
        self.starts[client] = _Start(self.start_zones.get(client), position, clearance)

    async def _port(self, porters: list) -> list:
        """Port *porters* to the first fighter, in waves. Returns the ones that landed on its realm."""
        fighter = self.fighters[0]
        try:
            left = await port_map.port_waves(self.machine, self.clients, porters, [fighter], self.machine.port_map)
        except Exception as exc:
            self._log(f"porting to {self._p(fighter)} failed ({exc!r})", logging.WARNING)
            left = porters
        if left:
            self._log(f"{self._names(left)} could not port to {self._p(fighter)}; the fight goes ahead without them")
        return [c for c in porters if c not in left]

    async def _port_and_join(self, helper, ported: asyncio.Task, centers) -> bool:
        # Shielded: every porter awaits the one port task, and cancelling one of them must not end it.
        if helper not in await asyncio.shield(ported):
            return False
        return await self._join(helper, centers)

    async def _join(self, helper, centers) -> bool:
        if await _in_battle(helper):
            # A port onto a fighter can land the helper straight in its duel circle.
            await centers.publish(self.machine, helper, self._slot[helper])
            joined = True
        else:
            joined = await combat_runner.join_fight(
                self.machine, helper, self._slot[helper], centers, ENTER_COMBAT_TIMEOUT
            )
        if joined:
            self.joined_at[helper] = self.clock()
            self._log(f"{self._p(helper)} joined the fight")
        else:
            self._log(f"{self._p(helper)} did not get into the fight", logging.DEBUG)
        return joined

    async def _fight(self, run, centers, dsls: list[str], holds: dict, joins: dict) -> None:
        """Start each client's combat once it is in battle, until no one is and every task is done."""
        while True:
            flags = await asyncio.gather(*(c.in_battle() for c in self.clients), return_exceptions=True)
            in_battle = [c for c, flag in zip(self.clients, flags) if flag is True]
            for client in in_battle:
                if run.fighting(client):
                    continue
                if client in holds and not await self._released(client, holds[client], joins):
                    continue
                await centers.publish(self.machine, client, self._slot[client])
                if client not in self.participants:
                    self.participants.append(client)
                    await combat_runner.seed_chat_baselines(self.machine, [client])
                run.start(client, dsls[self._slot[client]])
                self._log(f"{self._p(client)} is fighting", logging.DEBUG)
            if run.check_time_limit():
                return
            if not in_battle and not run.busy and all(task.done() for task in joins.values()):
                return
            await asyncio.sleep(POLL_INTERVAL)

    async def _released(self, fighter, hold: _Hold, joins: dict) -> bool:
        """Whether *fighter* may start casting: its helpers are all settled, or the hold ran out."""
        if hold.released:
            return True
        now = self.clock()
        if all(joins[h].done() for h in hold.helpers):
            hold.released = True
        else:
            if hold.planning_at is None:
                try:
                    from wizwalker.memory import DuelPhase  # noqa: PLC0415

                    if await fighter.duel.duel_phase() == DuelPhase.planning:
                        hold.planning_at = now
                except Exception:
                    hold.planning_at = self._detected  # unreadable: count from detection
            if hold.planning_at is not None:
                anchor = max([hold.planning_at, *(self.joined_at.get(h, 0.0) for h in hold.helpers)])
                hold.released = now >= anchor + AMBIENT_HELP_HOLD
        if hold.released and hold.helpers:
            self._log(f"{self._p(fighter)} starts casting", logging.DEBUG)
        return hold.released

    # ------------------------------------------------------------------ afterwards

    async def _leave(self, centers, fled_numbers: set[int]) -> int:
        """Move everyone somewhere safe after the fight. Returns how many fights were fled."""
        fights = [[m for m in group if m in self.participants] for group in centers.groups()]
        fights = [f for f in fights if f]
        fled = [f for f in fights if any(self._slot[m] + 1 in fled_numbers for m in f)]
        won = [f for f in fights if f not in fled]
        fought = {m for f in fights for m in f}
        stayed_out = [h for h in self.helpers if h not in fought]
        await asyncio.gather(
            *(self._after_won(f) for f in won),
            *(self._after_fled(f) for f in fled),
            *(self._home_unless_there(h) for h in stayed_out),
        )
        return len(fled)

    async def _after_won(self, members: list) -> None:
        await self._wisps_if_low(members)
        await self._settle(members)

    async def _settle(self, members: list) -> None:
        """Move each of *members* somewhere safe: its own start if it can get there, else the nearest anchor."""
        bystanders = [c for c in self.clients if c not in self.participants and c not in self.helpers]
        to_hub = []
        moves = []
        for member in members:
            if await self._can_go_home(member):
                moves.append((member, self.starts[member].position, "its starting point"))
                continue
            anchor = await self._nearest_anchor(member, bystanders)
            if anchor is None:
                self._log(f"{self._p(member)} has no safe spot in its zone to go to; going to the hub")
                to_hub.append(member)
            else:
                moves.append((member, anchor, "the nearest safe spot"))
        results = await asyncio.gather(*(self._move(m, point, what) for m, point, what in moves))
        to_hub += [m for (m, _p, _w), ok in zip(moves, results) if not ok]
        if to_hub:
            await self._to_hub(to_hub)

    async def _after_fled(self, members: list) -> None:
        names = ", ".join(self._p(m) for m in members)
        self._log(f"{names} fled to the hub; collecting wisps there")
        await self._wisps_if_low(members)

    async def _home_unless_there(self, helper) -> None:
        """A helper that never got in goes somewhere safe, if it had left where it started."""
        start = self.starts[helper]
        if await _zone(helper) == start.zone:
            here = await _position(helper)
            if start.position is None or here is None:
                return
            if node_helpers.distance(here, start.position) <= node_helpers.POSITION_MARGIN:
                return
        await self._settle([helper])

    async def _can_go_home(self, member) -> bool:
        """Whether *member* is a helper still in the zone it started in, so it can teleport back.

        A helper that ported in from another zone cannot: its starting point is coordinates in
        a zone it has left. A helper that ported from another realm of this zone can, and lands
        on the fighter's realm, which in a Together bot is the one it should be on anyway.
        """
        start = self.starts.get(member)
        return start is not None and start.position is not None and await _zone(member) == start.zone

    async def _wisps_if_low(self, members: list) -> None:
        ratios = await asyncio.gather(*(_ratios(m) for m in members))
        if not any(h < WISP_BELOW or m < WISP_BELOW for h, m in ratios):
            return
        await collect_wisps.collect_as_group(self.machine, members, 1.0, 1.0, WISP_COLLECT_TIMEOUT, True)

    async def _nearest_anchor(self, member, bystanders: list):
        """The nearest spot in *member*'s zone a player has stood on without being pulled into a fight.

        Candidates are this zone's helpers' starting points, and where any bystander in the
        zone stands now -- on any realm, since a teleport to coordinates keeps the player on
        its own. Either must be SAFE_RADIUS clear of mobs, as read by a client standing there.
        """
        zone = await _zone(member)
        here = await _position(member)
        if zone is None or here is None:
            return None
        candidates = [
            start.position
            for start in self.starts.values()
            if start.zone == zone and start.position is not None and start.clearance >= node_helpers.SAFE_RADIUS
        ]
        for bystander in bystanders:
            if self.start_zones.get(bystander) != zone or (spot := await _position(bystander)) is None:
                continue
            try:
                if await node_helpers.mob_clearance(bystander, spot) >= node_helpers.SAFE_RADIUS:
                    candidates.append(spot)
            except Exception:
                logger.debug("Could not read mobs around a bystander", exc_info=True)
        return min(candidates, key=lambda spot: node_helpers.distance(here, spot), default=None)

    async def _move(self, client, point, what: str) -> bool:
        """Teleport *client* to *point*, then check it landed SAFE_RADIUS clear of every mob."""
        try:
            await client.teleport(point)
            clearance = await node_helpers.mob_clearance(client, point)
        except Exception as exc:
            self._log(f"{self._p(client)} could not move to {what} ({exc!r}); going to the hub", logging.WARNING)
            return False
        if clearance < node_helpers.SAFE_RADIUS:
            self._log(f"{self._p(client)} landed {clearance:.0f} units from a mob at {what}; going to the hub")
            return False
        self._log(f"{self._p(client)} moved to {what}", logging.DEBUG)
        return True

    async def _to_hub(self, players: list) -> None:
        """Send *players* to their world's hub. Any that cannot go send the bot to recovery all the same."""
        self.force_recovery = True
        by_hub: dict[str, list] = {}
        for player in players:
            hub = await _hub_of(player)
            if hub is None:
                self._log(f"{self._p(player)}'s world has no known hub; leaving it to recovery", logging.WARNING)
                continue
            by_hub.setdefault(hub, []).append(player)
        for hub, group in by_hub.items():
            if not await _go_to_hub(self.machine, group, hub):
                names = ", ".join(self._p(p) for p in group)
                self._log(f"{names} could not reach the hub; leaving it to recovery", logging.WARNING)

    async def _all_home(self) -> bool:
        """Whether every participant and helper is in the zone it started in."""
        moved = [*self.participants, *(h for h in self.helpers if h not in self.participants)]
        zones = await asyncio.gather(*(_zone(c) for c in moved))
        away = [c for c, zone in zip(moved, zones) if zone != self.start_zones.get(c)]
        if away:
            self._log(f"{', '.join(self._p(c) for c in away)} left the zone the interrupted state had them in")
        return not away


# ---------------------------------------------------------------------------
# Game reads and the hub trip, as seams for the tests
# ---------------------------------------------------------------------------


async def _zone(client) -> str | None:
    try:
        return await client.zone_name()
    except Exception:
        return None


async def _position(client):
    try:
        return await client.body.position()
    except Exception:
        return None


async def _in_battle(client) -> bool:
    try:
        return await client.in_battle() is True
    except Exception:
        return False


async def _ratios(client) -> tuple[float, float]:
    """(health, mana) ratios; unreadable reads as full, so it never triggers a wisp run on its own."""
    try:
        return await client.calc_health_ratio(), await client.calc_mana_ratio()
    except Exception:
        return math.inf, math.inf


async def _hub_of(client) -> str | None:
    from bartlebot.game import zone_nav  # noqa: PLC0415

    zone = await _zone(client)
    world = zone_nav.load().world_of(zone) if zone else None
    return world.hub if world is not None else None


async def _go_to_hub(machine, players: list, hub: str) -> bool:
    """teleport_to_world_hub for *players*: waits out the button's cooldown, confirms a dungeon exit.

    Clicking the button is not movement, so the ghost window holds while it waits.
    """
    from bartlebot.nodes.teleportation import teleport_to_world_hub  # noqa: PLC0415

    return await teleport_to_world_hub.run(machine, players, {"zone": hub}) == "SUCCESS"
