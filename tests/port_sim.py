"""A stand-in for players porting to one another, for the port tests.

A port changes a client's zone, position and loading screen at once, and which of those it
changes depends on where the client started. Scripting every read with side_effect lists ties a
test to how many times the code under test happens to look, so PortSim keeps each player's state
instead and applies a port to it: a test says what kind of port happens, not what each read returns.
"""

from unittest.mock import AsyncMock

from wizwalker import XYZ

from bartlebot.clients.sightings import SIGHT_RANGE
from bartlebot.game import wizard_names
from tests.conftest import FakeClient, FakeGidEntity

LOAD_READS = 2
"""How many is_loading() reads a loading screen stays up for."""

HOME = "home"


class PortSim:
    """Players, and where a port puts them.

    A port lands on `target` (a hooked player) when one is set, else on `friend`: a
    (zone, xyz, realm) triple for a friend who is not one of ours. It shows a loading screen when
    the zone or realm changes and nothing otherwise, which is the game's behavior.

    Each player's `port` says how its ports go:

    * ``"lands"`` -- the port happens.
    * ``"ignored"`` -- never registers: no loading screen, no movement.
    * ``"ignored_once"`` -- the first does not register, the next does.
    * ``"lost"`` -- loads, and lands somewhere other than the target.

    Each player's entity list follows the simulation too, as the game's does (#130): it holds
    every player in its zone and realm within SIGHT_RANGE, and nobody while it is loading.
    """

    def __init__(self, friend: tuple[str, tuple, str] | None = None) -> None:
        self.friend = friend
        self.target: FakeClient | None = None
        self.ported: list[FakeClient] = []
        self.fail_from_call: int | None = None
        self.players: list[FakeClient] = []

    def player(self, zone: str, xyz: tuple = (0.0, 0.0, 0.0), *, realm: str = HOME, port: str = "lands", name=None):
        c = FakeClient()
        c.where = (zone, XYZ(*xyz))
        c.realm = realm
        c.port = port
        c.loading = 0

        async def zone_name():
            return c.where[0]

        async def position():
            return c.where[1]

        async def is_loading():
            if c.loading:
                c.loading -= 1
                return True
            return False

        c.zone_name = AsyncMock(side_effect=zone_name)
        c.body.position = AsyncMock(side_effect=position)
        c.is_loading = AsyncMock(side_effect=is_loading)
        c.get_base_entity_list = AsyncMock(side_effect=lambda: self._loaded_by(c))
        self.players.append(c)
        if name:
            wizard_names.remember(c, name)
        return c

    def _loaded_by(self, viewer) -> list:
        if viewer.loading:
            return []
        zone, xyz = viewer.where
        return [
            FakeGidEntity(other.gid)
            for other in self.players
            if other.where[0] == zone and other.realm == viewer.realm and xyz.distance(other.where[1]) <= SIGHT_RANGE
        ]

    def destination(self) -> tuple[str, XYZ, str]:
        if self.target is not None:
            return (*self.target.where, self.target.realm)
        zone, xyz, realm = self.friend
        return zone, XYZ(*xyz), realm

    async def teleport(self, client, _name_lower: str) -> None:
        """Stands in for node_helpers.teleport_via_friends_list."""
        self.ported.append(client)
        if self.fail_from_call is not None and len(self.ported) >= self.fail_from_call:
            raise ValueError("Could not find friend matching the name")
        if client.port == "ignored":
            return
        if client.port == "ignored_once":
            client.port = "lands"
            return
        zone, xyz, realm = self.destination()
        if client.port == "lost":
            zone = "Somewhere/Else"
        if zone != client.where[0] or realm != client.realm:
            client.loading = LOAD_READS
        client.where = (zone, xyz)
        client.realm = realm

    def times_ported(self, client) -> int:
        return sum(1 for c in self.ported if c is client)
