"""Whether our clients share a zone, realm and instance, told from their entity lists.

A client only loads the players in its own zone, realm and instance, and only nearby
(``tools/probe_same_place.py`` measured it). So one client holding another's global id in
its entity list *proves* they share all three, and, up close, not holding it proves they do
not. That is the only realm check that costs no spellbook: about 5 ms a client.

This module is the pure half, so the rules are testable without a game:

* :func:`classify` answers one pair from two :class:`Reading` s: Together, Apart or Unknown.
* :class:`Sightings` is one check over every client, with the groups it proves.
* :class:`TogetherRecord` remembers which groups have been seen together, until one of
  their members passes through a loading screen -- the only way a realm can change.

``node_helpers.read_sightings`` takes the readings from the game. ``BotMachine`` owns the
record for a run.

Global ids identify characters, so none of this ever logs one.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass

#: How close two clients on one realm have to be for "not seen" to prove anything.
#: Measured: everything within ~3800 units was loaded, loading starts at ~3850-4300 and
#: unloading at ~4400-5900. 3000 leaves headroom; every check that has to be definite is
#: made with the players within ~1200 of each other anyway (a sigil, a leader, a mob).
SIGHT_RANGE = 3000.0


class Sight(enum.Enum):
    """What one check says about a pair of clients."""

    #: One of them has the other loaded: the same zone, realm and instance.
    TOGETHER = "together"
    #: Different zones; or the same zone, close enough to see each other, and neither does.
    APART = "apart"
    #: Too far apart to tell, someone is loading, or something could not be read.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Reading:
    """One client's half of a check. Any field the game would not give is None."""

    gid: int | None
    zone: str | None
    position: object | None  # XYZ-shaped: .x, .y, .z
    loading: bool
    #: The global ids in this client's entity list, or None when the list was unreadable.
    seen: frozenset[int] | None


def _distance(a, b) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _sees(viewer: Reading, other: Reading) -> bool:
    return viewer.seen is not None and other.gid in viewer.seen


def classify(a: Reading, b: Reading) -> Sight:
    """What *a* and *b*'s readings prove about them.

    A loading client answers Unknown whatever else is true. Its zone and position are the
    ones it is leaving, its own list is empty, and a leaver stays in the others' lists for
    about a second into its loading screen -- so even a sighting could be one it is about to
    make false.

    Seeing needs one direction only: the probe's two directions disagreed only in the unload
    band, thousands of units out. Not seeing needs both, since a list that could not be read
    says nothing.
    """
    if a.gid is None or b.gid is None or a.loading or b.loading:
        return Sight.UNKNOWN
    if _sees(a, b) or _sees(b, a):
        return Sight.TOGETHER
    if a.zone is not None and b.zone is not None and a.zone != b.zone:
        return Sight.APART
    if a.seen is None or b.seen is None or a.position is None or b.position is None or a.zone is None:
        return Sight.UNKNOWN
    if _distance(a.position, b.position) <= SIGHT_RANGE:
        return Sight.APART
    return Sight.UNKNOWN


def _components(count: int, joined: Iterable[tuple[int, int]]) -> list[list[int]]:
    """The connected components of *count* nodes under *joined*, each sorted, in order of
    their lowest member."""
    parent = list(range(count))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in joined:
        parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(count):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


class Sightings:
    """One check over a list of clients: each pair's :class:`Sight`, and the groups it proves.

    Indices are positions in the ``clients`` list the check was made over. Sharing a zone,
    realm and instance is transitive, so if 0 sees 1 and 1 sees 2, then 0 and 2 are in one
    group even when they are too far apart to see each other.
    """

    def __init__(self, clients: Sequence[Hashable], readings: Sequence[Reading]):
        if len(clients) != len(readings):
            raise ValueError("one reading per client")
        self.clients = list(clients)
        self.readings = list(readings)
        count = len(self.readings)
        self._pairs: dict[tuple[int, int], Sight] = {
            (i, j): classify(self.readings[i], self.readings[j]) for i in range(count) for j in range(i + 1, count)
        }
        self._groups = _components(count, (pair for pair, sight in self._pairs.items() if sight is Sight.TOGETHER))

    def __len__(self) -> int:
        return len(self.readings)

    def pair(self, i: int, j: int) -> Sight:
        """What the check says about clients *i* and *j*. A client is always with itself."""
        if i == j:
            return Sight.TOGETHER
        return self._pairs[(min(i, j), max(i, j))]

    def groups(self) -> list[list[int]]:
        """Every client's group, singletons included, each sorted, in order of lowest index."""
        return [list(g) for g in self._groups]

    def group_of(self, i: int) -> list[int]:
        """The group client *i* is in."""
        return next(list(g) for g in self._groups if i in g)

    def all_together(self) -> bool:
        """Whether this check alone proves every client is in one group."""
        return len(self._groups) <= 1

    def apart(self) -> list[tuple[int, int]]:
        """Every pair this check proves are not together."""
        return [pair for pair, sight in self._pairs.items() if sight is Sight.APART]

    def gid(self, i: int) -> int | None:
        return self.readings[i].gid


class TogetherRecord:
    """Which clients have been seen together, remembered past the moment they were.

    Once a group has seen itself, it stays on one realm until one of its members loads, so
    the record holds groups until :meth:`forget` is called for a member. That lets a check
    made on a sigil still answer after the players have walked out of each other's sight.

    Keyed by global id: a reconnect or relog hands back a new client object for the same
    character, and the id is what survives. Clients are mapped to ids by the checks that
    saw them, so a client no check has read is never together with anything.

    ``retain=False`` is for a run with nothing to call :meth:`forget`: the record then keeps
    only the latest check, since without the loading watcher nothing could say when an older
    one stopped being true.
    """

    def __init__(self, *, retain: bool = True):
        self.retain = retain
        self._group_of: dict[int, frozenset[int]] = {}
        self._gid_of: dict[Hashable, int] = {}

    def note(self, sightings: Sightings) -> None:
        """Fold a check into the record.

        A pair the check proves Apart overrides whatever the record said about either of
        them: one of them loaded without the watcher seeing it, and the fresh reading wins.
        """
        for client, reading in zip(sightings.clients, sightings.readings):
            if reading.gid is not None:
                self._gid_of[client] = reading.gid
        if not self.retain:
            self._group_of.clear()
        for i, j in sightings.apart():
            for gid in (sightings.gid(i), sightings.gid(j)):
                if gid is not None:
                    self._forget_gid(gid)
        for group in sightings.groups():
            gids = {sightings.gid(i) for i in group} - {None}
            if len(gids) < 2:
                continue
            merged = set(gids)
            for gid in gids:
                merged |= self._group_of.get(gid, frozenset())
            joined = frozenset(merged)
            for gid in joined:
                self._group_of[gid] = joined

    def forget(self, client: Hashable) -> None:
        """Take *client* out of its group: it has loaded, or stopped answering."""
        gid = self._gid_of.get(client)
        if gid is not None:
            self._forget_gid(gid)

    def _forget_gid(self, gid: int) -> None:
        group = self._group_of.pop(gid, None)
        if group is None:
            return
        rest = group - {gid}
        for other in rest:
            if len(rest) < 2:
                self._group_of.pop(other, None)
            else:
                self._group_of[other] = rest

    def are_together(self, clients: Iterable[Hashable]) -> bool:
        """Whether every one of *clients* is in one remembered group. True for one client."""
        gids = [self._gid_of.get(c) for c in clients]
        if any(g is None for g in gids):
            return False
        if len(set(gids)) <= 1:
            return True
        group = self._group_of.get(gids[0])
        return group is not None and all(g in group for g in gids)

    def together_with(self, client: Hashable, clients: Iterable[Hashable]) -> list[Hashable]:
        """Those of *clients* remembered in *client*'s group, *client* itself included."""
        return [c for c in clients if c is client or self.are_together((client, c))]

    def drop_clients(self, keep: Iterable[Hashable]) -> None:
        """Forget the id mapping of every client not in *keep*. Groups, keyed by id, stay."""
        kept = set(keep)
        self._gid_of = {c: g for c, g in self._gid_of.items() if c in kept}
