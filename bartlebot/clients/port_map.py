"""Who among our clients can port to whom, and porting clients to one another in waves.

A port is the cheapest zone change there is for a group: one loading screen, from anywhere,
for any number of clients. Zone navigation leans on it -- one client travels the route and the
rest port to it -- but a port only works between two of ours who can see each other: a member to
its group leader, by the HUD's Teleport to Leader; friends, through the friends list; or failing
those, members of the same group, through the Group tab.

Who leads is read at the port, never surveyed: the game has no way to hand leadership on, so it
moves only when the leader leaves or logs off -- after a crash or a relog -- and a survey's answer
would outlive it. What the survey keeps is the group, which is what puts a leader in reach.

That is surveyed once before the run (AppController's pre-run setup) rather than discovered by
failing a port mid-route: a friends list is one text read, while a failed port costs its whole
timeout and leaves a straggler to sort out. An edge that fails anyway is demoted for the rest
of the run.

Keyed by wizard name rather than by slot: ClientManager.swap_clients reorders slots mid-run,
and a friendship belongs to the character. Names never reach a log line -- players are named
by number -- because logs get pasted into bug reports.

Free of Qt and FSM dependencies, like group_manager, so it can be tested without either.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from bartleby.runtime import run_trace
from bartleby.runtime.clients import group_manager
from bartleby.runtime.game import dialog_captions, wizard_names
from bartleby.runtime.timing import POLL_INTERVAL, SIGHTING_SETTLE_TIMEOUT

from bartlebot.nodes import node_helpers

logger = logging.getLogger(__name__)

LEADER = "leader"
FRIEND = "friend"
GROUP = "group"
ROUTES = (LEADER, FRIEND, GROUP)
"""In order of preference: Teleport to Leader is two clicks on the HUD and no confirmation, the
friends list takes one window, the group route two."""

_HOW = {LEADER: "by Teleport to Leader", FRIEND: "through the friends list", GROUP: "through the group"}


def _key(name: str | None) -> str | None:
    return name.casefold() if name else None


def _group_pairs(names: Sequence[str | None], rosters: Sequence[Sequence[str]]) -> set[tuple[str, str]]:
    """Every (src, dst) pair of our own clients that a Group tab roster puts in one group.

    A client's roster counts only if it lists that client itself, so a stale or foreign
    roster cannot vouch for a group the client is not in.
    """
    ours = {key for name in names if (key := _key(name))}
    pairs: set[tuple[str, str]] = set()
    for name, roster in zip(names, rosters):
        src = _key(name)
        members = {key for member in roster if (key := _key(member))}
        if src is not None and src in members:
            pairs.update((src, dst) for dst in (members & ours) - {src})
    return pairs


class PortMap:
    """The routes each of our clients has to each of the others."""

    def __init__(self, routes: dict[tuple[str, str], set[str]] | None = None) -> None:
        self._routes = {pair: set(kinds) for pair, kinds in (routes or {}).items()}

    @classmethod
    def from_survey(
        cls,
        names: Sequence[str | None],
        friends: Sequence[Sequence[str] | None],
        rosters: Sequence[Sequence[str]],
    ) -> PortMap:
        """Build from what a survey read, every argument positionally aligned with the clients.

        `friends[i]` is the online friends list client i showed (None when unreadable) and
        `rosters[i]` the names on its Group tab. Only pairs of our own clients are kept. A
        shared group gives both the group route and the leader route, the second offered only
        while its target leads (routes).
        """
        ours = {key for name in names if (key := _key(name))}
        routes: dict[tuple[str, str], set[str]] = {}
        for i, name in enumerate(names):
            src = _key(name)
            if src is None:
                continue
            for friend in friends[i] or ():
                dst = _key(friend)
                if dst in ours and dst != src:
                    routes.setdefault((src, dst), set()).add(FRIEND)
        for pair in _group_pairs(names, rosters):
            routes.setdefault(pair, set()).update((GROUP, LEADER))
        return cls(routes)

    def set_groups(self, names: Sequence[str | None], rosters: Sequence[Sequence[str]]) -> None:
        """Replace every group route with the ones these rosters give, mid-run.

        For after a regroup: a crash drops a client from its group, so the group routes the
        run started with no longer hold, while its friendships do. Friend routes are kept as
        they are, demotions included; group and leader routes are rebuilt from scratch, since a
        demoted one failed against a group that no longer exists. Whoever leads the new group
        is read at the next port, as always.
        """
        for kinds in self._routes.values():
            kinds.difference_update((GROUP, LEADER))
        for pair in _group_pairs(names, rosters):
            self._routes.setdefault(pair, set()).update((GROUP, LEADER))

    def _kinds(self, src, dst) -> set[str]:
        pair = (_key(wizard_names.cached_name(src)), _key(wizard_names.cached_name(dst)))
        if None in pair:
            return set()
        return self._routes.get(pair, set())

    def routes(self, src, dst, dst_leads: bool | None = None) -> list[str]:
        """The routes client `src` has to client `dst`, preferred first; [] if either name is unknown.

        The leader route needs `dst_leads`, whether `dst` leads its group as the HUD read it
        just now (group_manager.hud_leads): it is offered only when that is True and the two
        share a group. Left out, the answer is the routes that need no read, which is what a
        caller that does not port wants: a shared group always gives the group route too.
        """
        kinds = self._kinds(src, dst)
        return [route for route in ROUTES if route in kinds and (route != LEADER or dst_leads is True)]

    def may_lead(self, dst, clients: Sequence) -> bool:
        """Whether any of `clients` could reach `dst` by Teleport to Leader, were `dst` to lead.

        Says whose leadership is worth reading: one HUD read each, but only where it can matter.
        """
        return any(LEADER in self._kinds(c, dst) for c in clients if c is not dst)

    def demote(self, src, dst, route: str) -> bool:
        """Stop offering `route` from `src` to `dst`: it failed, and will not be tried again this run.

        Returns whether it was still offered, so a caller can tell a demotion from a no-op.
        """
        kinds = self._kinds(src, dst)
        if route not in kinds:
            return False
        kinds.discard(route)
        return True

    def friends_everywhere(self, names: Sequence[str | None]) -> bool:
        """Whether every one of these named clients has the friends-list route to every other."""
        keys = [_key(name) for name in names]
        if None in keys:
            return False
        return all(FRIEND in self._routes.get((a, b), ()) for a in keys for b in keys if a != b)

    def reach(self, target, clients: Sequence) -> list:
        """Every client in `clients` that can get to `target` by porting, in the order it would.

        In waves: straight to `target`, or to one that has already got there. `target` itself
        is never in the result.
        """
        arrived = [target]
        left = [c for c in clients if c is not target]
        reached: list = []
        while True:
            wave = [c for c in left if any(self.routes(c, a) for a in arrived)]
            if not wave:
                return reached
            reached += wave
            arrived += wave
            left = [c for c in left if c not in wave]


async def survey(clients: list, names: list[str | None] | None = None, states: list | None = None) -> PortMap:
    """Read who can port to whom. Never raises: whatever cannot be read simply has no route.

    Every client's friends list is read concurrently -- one text read each, and each client's
    input goes to its own window -- then closed. Then who is in a group with whom, which puts
    the leader route and the group route in reach: from `states` when pre-run grouping has
    just read them, else from the one leader's roster when the HUD shows every client grouped
    and one of them leading (group_manager.leader_roster), one social-window visit in all. Only
    when that cannot vouch for everyone and the friends lists leave a pair unconnected is
    every Group tab read, at a visit per client.
    """
    if len(clients) < 2:
        return PortMap()
    if names is None:
        with run_trace.span("port_survey.resolve_names"):
            names = await wizard_names.resolve_names(clients)
    friends = list(
        await asyncio.gather(*(run_trace.traced(_online_friends(c), "port_survey.friends", client=c) for c in clients))
    )
    if states is not None:
        return PortMap.from_survey(names, friends, [state.roster for state in states])
    no_rosters = [() for _ in clients]
    rosters = await _leader_rosters(clients, names)
    if rosters is not None and all(rosters):
        return PortMap.from_survey(names, friends, rosters)
    if PortMap.from_survey(names, friends, no_rosters).friends_everywhere(names):
        return PortMap.from_survey(names, friends, rosters or no_rosters)
    states = await group_manager.read_group_states(clients)
    return PortMap.from_survey(names, friends, [state.roster for state in states])


async def _leader_rosters(clients: list, names: Sequence[str | None]) -> list[tuple[str, ...]] | None:
    """Each client's Group tab roster as the one leader among them shows it, from one visit.

    None unless the HUD shows every client grouped and exactly one leading, and that leader's
    roster lists it (group_manager.leader_roster). A wizard is in one group at most, so every
    one of ours that the roster lists shares it, and one it does not list gets an empty one:
    it is grouped elsewhere, with nobody of ours to lead it.
    """
    found = await group_manager.leader_roster(clients)
    if found is None or not found.lists(names[found.index]):
        return None
    return [found.state.roster if found.lists(name) else () for name in names]


async def _online_friends(client) -> list[str] | None:
    """This client's online friends, [] when nobody is online, None when the list cannot be read.

    Opening the list is a click, and every click needs the mouseless cursor hook: without it
    the read raised HookNotActive on every client, so no friend route was ever surveyed and
    every port fell back to the slower group route (#139). The close is a key press.
    """
    try:
        async with client.mouse_handler:
            return await node_helpers.list_online_friends(client)
    except ValueError:
        return []
    except Exception:
        logger.debug("Could not read a friends list for the port survey", exc_info=True)
        return None
    finally:
        await node_helpers.close_friends_list(client)


async def port_waves(machine, clients: list, waiting: list, arrived: list, port_map: PortMap) -> list:
    """Port every client in `waiting` to one in `arrived`, and return those still waiting.

    Each wave sends every waiting client with a route to someone who has arrived, in ROUTES'
    order; whoever lands counts as arrived for the next wave, which is how a client with no
    route to the first arrival still gets there through another. A batch that fails demotes its
    route, and its movers go into the next wave on their next one, so a failed Teleport to
    Leader falls back to the friends list in the same call. Stops when nobody is waiting, or a
    wave moves nobody and demotes nothing: a refusal (a friend busy) demotes nothing, so it is
    not retried on its own, and since every other wave moves a client or uses up a route, the
    loop ends. `clients` is only for naming players by number in the log.

    Each wave reads, once per arrived client, whether it leads its group, and only for one a
    waiting client shares a group with (PortMap.may_lead). Read every wave rather than kept,
    since a crash or a relog moves the leadership mid-run.
    """
    waiting, arrived = list(waiting), list(arrived)

    def number(client) -> int:
        return next((i + 1 for i, c in enumerate(clients) if c is client), 0)

    while waiting:
        maybe = [a for a in arrived if port_map.may_lead(a, waiting)]
        read = await asyncio.gather(*(group_manager.hud_leads(a) for a in maybe))
        leads = {id(a): lead for a, lead in zip(maybe, read)}
        batches: dict[tuple[int, str], tuple[object, list]] = {}
        for client in waiting:
            for route in ROUTES:
                target = next((a for a in arrived if route in port_map.routes(client, a, leads.get(id(a)))), None)
                if target is not None:
                    batches.setdefault((id(target), route), (target, []))[1].append(client)
                    break
        if not batches:
            break
        outcomes = await asyncio.gather(
            *(
                _port_batch(machine, movers, target, route, port_map, number)
                for (_, route), (target, movers) in batches.items()
            )
        )
        moved = [client for landed, _ in outcomes for client in landed]
        if not moved and not any(demoted for _, demoted in outcomes):
            break
        arrived += moved
        waiting = [c for c in waiting if not any(c is m for m in moved)]
    return waiting


async def _port_batch(machine, movers: list, target, route: str, port_map: PortMap, number) -> tuple[list, bool]:
    """Port `movers` to `target` along `route`; return the ones that landed with it, and whether a route was demoted.

    Landing is the target's zone *and* its realm, the second told by the two seeing each
    other (#130): a port that fails silently can leave a mover in the right zone on the
    wrong realm, which would enter the next dungeon or battle on its own.
    """
    who, to = [number(c) for c in movers], number(target)
    machine.log(f"Porting player(s) {who} to player {to} {_HOW[route]}")
    try:
        if route == LEADER:
            # Teleport to Leader takes no name: each mover goes to its own leader, which the
            # group and the read just taken say is `target`. The landing check below catches it if not.
            result = await group_manager.port_to_leader(movers)
        else:
            start = node_helpers.port_to_friend if route == FRIEND else group_manager.port_to_group_member
            result = await start(movers, (wizard_names.cached_name(target) or "").lower())
    except ValueError:
        # The message names the player it could not find, so it stays out of the log.
        machine.log(f"The {route} route from player(s) {who} to player {to} could not start", logging.WARNING)
        return [], _demote(port_map, movers, target, route)
    if result.refusal:
        machine.log(f"Port to player {to} refused — {dialog_captions.describe(result.refusal)}", logging.WARNING)

    await node_helpers.poll_until(lambda: node_helpers.none_loading(movers), None, POLL_INTERVAL)
    # The screen clearing is not the zone being readable, and None == None would count as a match.
    goal, *zones = await node_helpers.read_zones([target, *movers])
    machine.log(
        f"Port to player {to}: its zone is {goal!r}; "
        + ", ".join(f"player {number(c)}'s is {zone!r}" for c, zone in zip(movers, zones)),
        logging.DEBUG,
    )
    in_zone = [c for c, zone in zip(movers, zones) if goal is not None and zone == goal]
    if len(in_zone) < len(movers):
        unplaced = [number(c) for c in movers if not any(c is z for z in in_zone)]
        machine.log(f"Player(s) {unplaced} did not end up in player {to}'s zone after the port", logging.WARNING)

    async def all_seen() -> bool:
        check = await node_helpers.read_sightings([target, *in_zone], machine)
        return check.all_together()

    landed = in_zone
    if in_zone and not await node_helpers.poll_until(all_seen, SIGHTING_SETTLE_TIMEOUT, POLL_INTERVAL):
        check = await node_helpers.read_sightings([target, *in_zone], machine)
        group = check.group_of(0)
        landed = [c for i, c in enumerate(in_zone, start=1) if i in group]
        machine.log(
            f"Player(s) {[number(c) for c in in_zone if c not in landed]} reached player {to}'s zone but not its realm",
            logging.WARNING,
        )
    demoted = False
    if not result.refusal:
        # A refusal is the moment (a friend busy), not the route; anything else that did not land is.
        strays = [client for client in movers if not any(client is c for c in landed)]
        demoted = _demote(port_map, strays, target, route)
    return landed, demoted


def _demote(port_map: PortMap, movers: list, target, route: str) -> bool:
    """Demote `route` from each of `movers` to `target`; whether any of them still had it."""
    return any([port_map.demote(client, target, route) for client in movers])
