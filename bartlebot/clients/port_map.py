"""Who among our clients can port to whom, and porting clients to one another in waves.

A port is the cheapest zone change there is for a group: one loading screen, from anywhere,
for any number of clients. Zone navigation leans on it -- one client travels the route and the
rest port to it -- but a port only works between two of ours who can see each other: friends,
through the friends list, or failing that members of the same group, through the Group tab.

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

from bartlebot.bot import run_trace
from bartlebot.clients import group_manager
from bartlebot.game import dialog_captions, wizard_names
from bartlebot.nodes import node_helpers
from bartlebot.nodes.timing import POLL_INTERVAL, SIGHTING_SETTLE_TIMEOUT

logger = logging.getLogger(__name__)

FRIEND = "friend"
GROUP = "group"
ROUTES = (FRIEND, GROUP)
"""In order of preference: the friends list takes one window, the group route two."""


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
        `rosters[i]` the names on its Group tab. Only pairs of our own clients are kept.
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
            routes.setdefault(pair, set()).add(GROUP)
        return cls(routes)

    def set_groups(self, names: Sequence[str | None], rosters: Sequence[Sequence[str]]) -> None:
        """Replace every group route with the ones these rosters give, mid-run.

        For after a regroup: a crash drops a client from its group, so the group routes the
        run started with no longer hold, while its friendships do. Friend routes are kept as
        they are, demotions included; group routes are rebuilt from scratch, since a demoted
        one failed against a group that no longer exists.
        """
        for kinds in self._routes.values():
            kinds.discard(GROUP)
        for pair in _group_pairs(names, rosters):
            self._routes.setdefault(pair, set()).add(GROUP)

    def routes(self, src, dst) -> list[str]:
        """The routes client `src` has to client `dst`, preferred first; [] if either name is unknown."""
        pair = (_key(wizard_names.cached_name(src)), _key(wizard_names.cached_name(dst)))
        if None in pair:
            return []
        kinds = self._routes.get(pair, set())
        return [route for route in ROUTES if route in kinds]

    def demote(self, src, dst, route: str) -> None:
        """Stop offering `route` from `src` to `dst`: it failed, and will not be tried again this run."""
        pair = (_key(wizard_names.cached_name(src)), _key(wizard_names.cached_name(dst)))
        self._routes.get(pair, set()).discard(route)

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
    input goes to its own window -- then closed. The Group tab is only read when the friends
    lists leave a pair unconnected, since it costs a social-window visit per client; `states`
    skips even that when pre-run grouping has just read them.
    """
    if len(clients) < 2:
        return PortMap()
    if names is None:
        with run_trace.span("port_survey.resolve_names"):
            names = await wizard_names.resolve_names(clients)
    friends = list(
        await asyncio.gather(*(run_trace.traced(_online_friends(c), "port_survey.friends", client=c) for c in clients))
    )
    no_rosters = [() for _ in clients]
    if PortMap.from_survey(names, friends, no_rosters).friends_everywhere(names):
        return PortMap.from_survey(names, friends, no_rosters)
    if states is None:
        states = await group_manager.read_group_states(clients)
    return PortMap.from_survey(names, friends, [state.roster for state in states])


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

    Each wave sends every waiting client with a route to someone who has arrived, friends list
    first; whoever lands counts as arrived for the next wave, which is how a client with no
    route to the first arrival still gets there through another. Stops when nobody is waiting
    or a wave moves nobody. `clients` is only for naming players by number in the log.
    """
    waiting, arrived = list(waiting), list(arrived)

    def number(client) -> int:
        return next((i + 1 for i, c in enumerate(clients) if c is client), 0)

    while waiting:
        batches: dict[tuple[int, str], tuple[object, list]] = {}
        for client in waiting:
            for route in ROUTES:
                target = next((a for a in arrived if route in port_map.routes(client, a)), None)
                if target is not None:
                    batches.setdefault((id(target), route), (target, []))[1].append(client)
                    break
        if not batches:
            break
        landed = await asyncio.gather(
            *(
                _port_batch(machine, movers, target, route, port_map, number)
                for (_, route), (target, movers) in batches.items()
            )
        )
        moved = [client for batch in landed for client in batch]
        if not moved:
            break
        arrived += moved
        waiting = [c for c in waiting if not any(c is m for m in moved)]
    return waiting


async def _port_batch(machine, movers: list, target, route: str, port_map: PortMap, number) -> list:
    """Port `movers` to `target` along `route`, and return the ones that landed with it.

    Landing is the target's zone *and* its realm, the second told by the two seeing each
    other (#130): a port that fails silently can leave a mover in the right zone on the
    wrong realm, which would enter the next dungeon or battle on its own.
    """
    who, to = [number(c) for c in movers], number(target)
    machine.log(f"Porting player(s) {who} to player {to} through the {'friends list' if route == FRIEND else 'group'}")
    start = node_helpers.port_to_friend if route == FRIEND else group_manager.port_to_group_member
    try:
        result = await start(movers, (wizard_names.cached_name(target) or "").lower())
    except ValueError:
        # The message names the player it could not find, so it stays out of the log.
        machine.log(f"The {route} route from player(s) {who} to player {to} could not start", logging.WARNING)
        for client in movers:
            port_map.demote(client, target, route)
        return []
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
    if not result.refusal:
        # A refusal is the moment (a friend busy), not the route; anything else that did not land is.
        for client in movers:
            if not any(client is c for c in landed):
                port_map.demote(client, target, route)
    return landed
