"""Unit tests for navigate(): one planned step at a time, World Gates and HOME included."""

from unittest.mock import AsyncMock

import pytest
from bartleby.runtime.game import wizard_names

from bartlebot.clients import port_map
from bartlebot.game import worlds, zone_names, zone_nav
from bartlebot.nodes import node_helpers, wizard_city, zone_navigation
from bartlebot.nodes.interaction import interact
from bartlebot.nodes.teleportation import teleport_to_world_hub
from bartlebot.nodes.utility import change_realm, realm_list, sleep
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list, enter_zone_via_object, enter_zone_via_ride
from tests.conftest import FakeClient, FakeMachine, see_each_other

_HUB, _DOOR, _S1, _S2, _ISLAND = "Alpha/Hub", "Alpha/Door", "Alpha/S1", "Alpha/S2", "Alpha/Island"
_HOME = "Alpha/Dorm"
_DELTA = "Delta/Hub"
"""Delta is another world, its World Gate in its hub."""
_LANDS_IN = {"Alpha/*": _DOOR, "Delta/*": _DELTA}
"""Where a gate step, which arrives anywhere in a world, puts the fake clients."""
_NAMES = ["Luke SkullHunter", "Misha SkullSeer", "Michelle HistoryBond", "Naomi HexPaint"]
"""Synthetic names from tools/name_key_samples.json -- never a real player's."""


def _xyz(x: float) -> dict:
    return {"x": x, "y": 0.0, "z": 0.0}


def _walk(src: str, dst: str, verified: bool = True) -> dict:
    return {"kind": "walk", "from": src, "to": dst, "xyz": _xyz(1), "reset_xyz": _xyz(2), "verified": verified}


def _teleporter(src: str, dst: str) -> dict:
    return {"kind": "object", "from": src, "to": dst, "interact_xyz": _xyz(3), "reset_xyz": _xyz(4), "verified": True}


def _nav(*links, networks=(), beta_links=(), no_realm_change=()) -> zone_nav.NavMap:
    """Alpha, with HOME's dorm a walk from its hub; Beta, a side world entered from it; and Delta, with a gate.

    `no_realm_change` names Alpha zones that refuse a realm change, as Eerem Palace does.
    """
    return zone_nav.parse(
        {
            "home": _HOME,
            "worlds": {
                "Alpha": {
                    "hub": _HUB,
                    "door": _DOOR,
                    "zones": {
                        z: ({"realm_change": False} if z in no_realm_change else {})
                        for z in (_HUB, _DOOR, _S1, _S2, _ISLAND, _HOME)
                    },
                    "links": [*links, _walk(_HOME, _HUB)],
                    "list_networks": list(networks),
                },
                "Beta": {
                    "hub": "Beta/Hub",
                    "entered_from": "Alpha",
                    "zones": {"Beta/Hub": {}},
                    "links": list(beta_links),
                },
                "Delta": {
                    "hub": _DELTA,
                    "door": _DELTA,
                    "gate": {"interact_xyz": _xyz(8), "reset_xyz": _xyz(9), "verified": True},
                    "zones": {_DELTA: {}},
                },
            },
        }
    )


class _Game:
    """Clients whose zone the fake nodes move, and a record of every node call.

    `friends` makes every client a friend of every other, so they can port to one another;
    `misses` is {player number: how many moving calls it sits out}; `cannot_port` names the
    players a port never lands.
    """

    def __init__(
        self,
        monkeypatch,
        zones: list[str],
        results: dict | None = None,
        *,
        friends: bool = False,
        misses: dict[int, int] | None = None,
        cannot_port=(),
    ):
        self.calls: list[tuple[str, dict]] = []
        self.groups: list[list[int]] = []
        self.recovery: list[bool] = []
        self.waves: list[tuple[list[int], list[int]]] = []
        self.clients = []
        for zone in zones:
            client = FakeClient()
            client.where = zone
            client.zone_name = AsyncMock(side_effect=lambda c=client: c.where)
            self.clients.append(client)
        results = results or {}
        misses = dict(misses or {})

        def fake(name: str, arrive):
            async def run(machine, clients, params):
                self.calls.append((name, params))
                self.groups.append([self.number(c) for c in clients])
                self.recovery.append(node_helpers.straggler_recovery())
                if results.get(name, "SUCCESS") == "SUCCESS" and arrive is not None:
                    for c in clients:
                        if misses.get(self.number(c)):
                            misses[self.number(c)] -= 1
                            continue
                        c.where = arrive(params)
                return results.get(name, "SUCCESS")

            return run

        monkeypatch.setattr(enter_zone, "run", fake("walk", lambda p: p["to_zone"]))
        monkeypatch.setattr(enter_zone_via_object, "run", fake("object", lambda p: p["to_zone"]))
        monkeypatch.setattr(
            enter_zone_via_list, "run", fake("list", lambda p: _LANDS_IN.get(p["to_zone"], p["to_zone"]))
        )
        monkeypatch.setattr(enter_zone_via_ride, "run", fake("ride", lambda p: p["to_zone"]))
        monkeypatch.setattr(interact, "run", fake("interact", None))
        monkeypatch.setattr(sleep, "run", fake("sleep", None))
        monkeypatch.setattr(teleport_to_world_hub, "run", fake("hub", lambda p: p["zone"]))
        monkeypatch.setattr(node_helpers, "teleport_cooldown", AsyncMock(return_value=0.0))

        async def to_realm(machine, client, realm, safety_delay):
            self.calls.append(("hop", {"player": self.number(client), "realm": realm}))
            # A hop leaves the hopper alone on its new realm, out of everyone else's sight.
            see_each_other(client)
            see_each_other(*[c for c in self.clients if c is not client])
            return "SUCCESS"

        monkeypatch.setattr(change_realm, "to_realm", to_realm)
        monkeypatch.setattr(worlds, "display_names", lambda world: [world, world.lower()])
        go_home = fake("home", lambda p: _HOME)
        monkeypatch.setattr(
            wizard_city,
            "teleport_home_and_verify_dorm",
            lambda machine, clients: _succeeded(go_home(machine, clients, {})),
        )

        names = _NAMES[: len(zones)]
        if friends:
            for client, name in zip(self.clients, names):
                wizard_names.remember(client, name)
            ports = port_map.PortMap.from_survey(
                names, [[n for n in names if n != me] for me in names], [() for _ in names]
            )
        else:
            ports = port_map.PortMap()
        self.survey = AsyncMock(return_value=ports)
        monkeypatch.setattr(port_map, "survey", self.survey)

        unported = set(cannot_port)

        async def port_waves(machine, clients, waiting, arrived, pm):
            self.waves.append(([self.number(c) for c in waiting], [self.number(c) for c in arrived]))
            left = [c for c in waiting if self.number(c) in unported]
            for c in waiting:
                if self.number(c) not in unported:
                    c.where = arrived[0].where
            # A port puts the porter on the target's realm and beside it: everyone there sees everyone.
            see_each_other(*[c for c in self.clients if c.where == arrived[0].where and c not in left])
            return left

        monkeypatch.setattr(port_map, "port_waves", port_waves)

    def number(self, client) -> int:
        return next(i + 1 for i, c in enumerate(self.clients) if c is client)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


async def _succeeded(run) -> bool:
    return await run == "SUCCESS"


async def _go(game: _Game, nav, target: str, **kwargs) -> zone_navigation.Outcome:
    return await zone_navigation.navigate(FakeMachine(), game.clients, target, nav=nav, **kwargs)


async def test_already_there_does_nothing(monkeypatch):
    game = _Game(monkeypatch, [_S1])

    outcome = await _go(game, _nav(), _S1)

    assert outcome.success
    assert game.calls == []


async def test_a_zone_not_readable_yet_is_waited_for_rather_than_planned_from(monkeypatch):
    game = _Game(monkeypatch, [_HUB])
    monkeypatch.setattr(zone_navigation, "POLL_INTERVAL", 0.001)
    client = game.clients[0]
    reads = iter([None, None, None])
    client.zone_name = AsyncMock(side_effect=lambda: next(reads, client.where))

    outcome = await _go(game, _nav(_walk(_HUB, _S1)), _S1)

    assert outcome.success
    assert game.names == ["walk"]


async def test_a_zone_that_never_becomes_readable_fails_without_moving(monkeypatch):
    game = _Game(monkeypatch, [None])
    monkeypatch.setattr(zone_navigation, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(zone_navigation, "LOADING_SCREEN_TIMEOUT", 0.05)

    outcome = await _go(game, _nav(_walk(_HUB, _S1)), _S1)

    assert not outcome.success
    assert "could not be read" in outcome.reason
    assert game.calls == []


async def test_walks_each_step_with_the_links_coordinates(monkeypatch):
    game = _Game(monkeypatch, [_HUB])

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.names == ["walk", "walk"]
    assert game.calls[0][1] == {"from_zone": _HUB, "to_zone": _S1, "xyz": _xyz(1), "reset_xyz": _xyz(2)}
    assert [s.to_zone for s in outcome.taken] == [_S1, _S2]


async def test_another_world_is_reached_through_the_gate_where_the_clients_are(monkeypatch):
    game = _Game(monkeypatch, [_DELTA])

    outcome = await _go(game, _nav(_walk(_DOOR, _S1)), _S1)

    assert outcome.success
    assert game.names == ["list", "walk"]
    assert game.calls[0][1] == {
        "zone": _DELTA,
        "to_zone": "Alpha/*",
        "interact_xyz": _xyz(8),
        "reset_xyz": _xyz(9),
        "zone_list_entry": "Alpha",
        "zone_list_aliases": ["alpha"],
    }


async def test_a_side_world_is_reached_through_its_parents_gate(monkeypatch):
    game = _Game(monkeypatch, [_DELTA])

    outcome = await _go(game, _nav(_walk(_DOOR, _S1), beta_links=[_walk(_S1, "Beta/Hub")]), "Beta/Hub")

    assert outcome.success
    assert game.names == ["list", "walk", "walk"]


async def test_a_client_on_no_map_goes_home_first(monkeypatch):
    game = _Game(monkeypatch, ["Housing/Castle"])

    outcome = await _go(game, _nav(_walk(_HUB, _S1)), _S1)

    assert outcome.success
    assert game.names == ["home", "walk", "walk"]


async def test_a_group_split_across_worlds_regroups_at_home(monkeypatch):
    game = _Game(monkeypatch, [_DELTA, _S1])

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.names == ["home", "walk", "walk", "walk"]


async def test_a_gate_step_without_a_known_world_name_fails_before_moving(monkeypatch):
    game = _Game(monkeypatch, [_DELTA])
    monkeypatch.setattr(worlds, "display_names", lambda world: [])

    outcome = await _go(game, _nav(), _DOOR)

    assert not outcome.success
    assert "display name" in outcome.reason
    assert game.calls == []


async def test_an_object_step_passes_its_teleporter_settings(monkeypatch):
    game = _Game(monkeypatch, [_HUB])
    teleporter = _teleporter(_HUB, _ISLAND)

    await _go(game, _nav({**teleporter, "sequential": True, "object_use_delay": 8.0}), _ISLAND)

    [(name, params)] = game.calls
    assert name == "object"
    assert params["sequential"] is True
    assert params["object_use_delay"] == 8.0


async def test_avoided_kinds_are_left_out_of_the_route(monkeypatch):
    game = _Game(monkeypatch, [_HUB])
    nav = _nav(_teleporter(_HUB, _S2), _walk(_HUB, _S1), _walk(_S1, _S2))

    outcome = await _go(game, nav, _S2, avoid={"object", "list"})

    assert outcome.success
    assert game.names == ["walk", "walk"]


async def test_a_list_step_matches_the_destination_in_every_locale(monkeypatch):
    game = _Game(monkeypatch, [_HUB])
    monkeypatch.setattr(zone_names, "display_names", lambda zone: ["Island", "Insel"])
    spot = {"interact_xyz": _xyz(5), "reset_xyz": _xyz(6)}
    network = {"zones": {_HUB: spot, _ISLAND: spot}, "verified": True}

    await _go(game, _nav(networks=[network]), _ISLAND)

    [(name, params)] = game.calls
    assert name == "list"
    assert (params["zone_list_entry"], params["zone_list_aliases"]) == ("Island", ["Insel"])


async def test_a_lists_own_aliases_are_added_to_the_locale_names(monkeypatch):
    """Nimbus Hanger's hub list shows "Nimbus" -- not a locale name, so it can't come from display_names()."""
    game = _Game(monkeypatch, [_HUB])
    monkeypatch.setattr(zone_names, "display_names", lambda zone: ["Island", "Insel"])
    network = {
        "zones": {
            _HUB: {"interact_xyz": _xyz(5), "reset_xyz": _xyz(6)},
            _ISLAND: {"interact_xyz": _xyz(5), "reset_xyz": _xyz(6), "list_aliases": ["Isle"]},
        },
        "verified": True,
    }

    await _go(game, _nav(networks=[network]), _ISLAND)

    [(_, params)] = game.calls
    assert params["zone_list_aliases"] == ["Insel", "Isle"]


async def test_list_aliases_only_never_tries_the_zones_own_display_name(monkeypatch):
    """Two Empyrea rows both display "Chaos Jungle" in-game; only one member is really that zone."""
    game = _Game(monkeypatch, [_HUB])
    monkeypatch.setattr(zone_names, "display_names", lambda zone: ["Chaos Jungle"])
    network = {
        "zones": {
            _HUB: {"interact_xyz": _xyz(5), "reset_xyz": _xyz(6)},
            _ISLAND: {
                "interact_xyz": _xyz(5),
                "reset_xyz": _xyz(6),
                "list_aliases": ["Mandalla"],
                "list_aliases_only": True,
            },
        },
        "verified": True,
    }

    await _go(game, _nav(networks=[network]), _ISLAND)

    [(_, params)] = game.calls
    assert (params["zone_list_entry"], params["zone_list_aliases"]) == ("Mandalla", [])


async def test_a_list_step_without_a_known_name_fails_before_moving(monkeypatch):
    game = _Game(monkeypatch, [_HUB])
    monkeypatch.setattr(zone_names, "display_names", lambda zone: [])
    spot = {"interact_xyz": _xyz(5), "reset_xyz": _xyz(6)}

    outcome = await _go(game, _nav(networks=[{"zones": {_HUB: spot, _ISLAND: spot}, "verified": True}]), _ISLAND)

    assert not outcome.success
    assert "display name" in outcome.reason
    assert game.calls == []


async def test_a_ride_boarded_by_interacting_passes_its_interact_point(monkeypatch):
    game = _Game(monkeypatch, [_S1])
    ride = {"kind": "ride", "from": _S1, "to": _ISLAND, "interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}

    outcome = await _go(game, _nav({**ride, "ride_zone": "Alpha/Dragon", "verified": True}), _ISLAND)

    assert outcome.success
    assert game.calls == [
        (
            "ride",
            {
                "from_zone": _S1,
                "to_zone": _ISLAND,
                "board": "object",
                "board_xyz": _xyz(1),
                "reset_xyz": _xyz(2),
                "ride_zone": "Alpha/Dragon",
            },
        )
    ]


async def test_a_ride_inside_its_zone_passes_no_ride_zone(monkeypatch):
    game = _Game(monkeypatch, [_S1])
    boat = {"kind": "ride", "from": _S1, "to": _ISLAND, "interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}

    await _go(game, _nav({**boat, "verified": True}), _ISLAND)

    [(_, params)] = game.calls
    assert "ride_zone" not in params


async def test_a_ride_boarded_through_a_gate_passes_the_gate(monkeypatch):
    game = _Game(monkeypatch, [_S1])
    airship = {"kind": "ride", "board": "walk", "from": _S1, "to": _ISLAND, "xyz": _xyz(7)}

    await _go(game, _nav({**airship, "ride_zone": "Alpha/Airship", "verified": True}), _ISLAND)

    [(_, params)] = game.calls
    assert (params["board"], params["board_xyz"]) == ("walk", _xyz(7))
    assert "reset_xyz" not in params


async def test_a_gated_walk_opens_its_gate_with_one_player_then_waits_for_it(monkeypatch):
    """Tomb of Storms: the gate is shared, so player 1 uses the three objects, then everyone walks."""
    game = _Game(monkeypatch, [_HUB, _HUB])
    spots = [{"interact_xyz": _xyz(n), "reset_xyz": _xyz(n + 10)} for n in (3, 4, 5)]
    gated = {**_walk(_HUB, _S1), "unlock": {"objects": spots, "opens_after": 20}}

    outcome = await _go(game, _nav(gated), _S1)

    assert outcome.success
    assert game.names == ["interact", "interact", "interact", "sleep", "walk"]
    opening = [params for _, params in game.calls[:3]]
    assert [p["interact_xyz"] for p in opening] == [_xyz(3), _xyz(4), _xyz(5)]
    assert all(p["interact_clients"] == [1] and p["has_dialogue"] is False for p in opening)
    assert game.calls[3][1] == {"duration": 20.0}


async def test_a_gate_that_will_not_open_stops_before_the_walk(monkeypatch):
    game = _Game(monkeypatch, [_HUB], results={"interact": "FAILURE"})
    spots = [{"interact_xyz": _xyz(3), "reset_xyz": _xyz(4)}]

    outcome = await _go(game, _nav({**_walk(_HUB, _S1), "unlock": {"objects": spots, "opens_after": 20}}), _S1)

    assert not outcome.success
    assert game.names == ["interact"]


async def test_a_group_left_on_a_ride_finishes_it(monkeypatch):
    """The ride zone is on no map; the ride out of it is, and it still boards from the dock."""
    game = _Game(monkeypatch, ["Alpha/Dragon"])
    ride = {"kind": "ride", "from": _S1, "to": _ISLAND, "interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}

    outcome = await _go(game, _nav({**ride, "ride_zone": "Alpha/Dragon", "verified": True}), _ISLAND)

    assert outcome.success
    [(name, params)] = game.calls
    assert (name, params["from_zone"], params["ride_zone"]) == ("ride", _S1, "Alpha/Dragon")


async def test_no_route_says_so_without_moving(monkeypatch):
    game = _Game(monkeypatch, [_HUB])

    outcome = await _go(game, _nav(), _ISLAND)

    assert not outcome.success
    assert "no route" in outcome.reason
    assert game.calls == []


async def test_a_failed_step_with_no_other_route_stops_the_run(monkeypatch):
    game = _Game(monkeypatch, [_HUB], results={"walk": "FAILURE"})

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert not outcome.success
    assert game.names == ["walk"]
    assert outcome.taken == []


async def test_a_step_nobody_makes_is_planned_around(monkeypatch):
    """A teleporter that fails for everyone -- most likely one this account has not unlocked."""
    game = _Game(monkeypatch, [_HUB], results={"object": "FAILURE"})

    outcome = await _go(game, _nav(_teleporter(_HUB, _S2), _walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.names == ["object", "walk", "walk"]


async def test_tentative_links_are_used_only_when_asked_and_reported(monkeypatch):
    nav = _nav(_walk(_HUB, _S1, verified=False))

    refused = await _go(_Game(monkeypatch, [_HUB]), nav, _S1)
    allowed = await _go(_Game(monkeypatch, [_HUB]), nav, _S1, include_unverified=True)

    assert not refused.success
    assert allowed.success
    assert [(s.from_zone, s.to_zone) for s in allowed.unverified] == [(_HUB, _S1)]


async def test_a_split_group_regroups_at_the_hub(monkeypatch):
    game = _Game(monkeypatch, [_S1, _S2])

    outcome = await _go(game, _nav(_walk(_HUB, _S1)), _S1)

    assert outcome.success
    assert game.names == ["hub", "walk"]


@pytest.mark.parametrize("left", [12.0, None])
async def test_a_cooling_hub_button_loses_a_tie_to_a_teleporter(monkeypatch, left):
    """A timer read as seconds left, or a disabled button with no timer to read."""
    game = _Game(monkeypatch, [_S2])
    monkeypatch.setattr(node_helpers, "teleport_cooldown", AsyncMock(return_value=left))

    await _go(game, _nav(_teleporter(_S2, _HUB)), _HUB)

    assert game.names == ["object"]


async def test_a_client_in_a_restricted_zone_is_refused(monkeypatch):
    game = _Game(monkeypatch, ["WizardCity/WC_Duel_Arena_New"])

    outcome = await _go(game, _nav(), _S1)

    assert not outcome.success
    assert "restricted" in outcome.reason
    assert game.calls == []


@pytest.mark.parametrize("target", ["Alpha/Nowhere", "Gamma/Hub"])
async def test_a_target_off_the_map_is_refused(monkeypatch, target):
    game = _Game(monkeypatch, [_HUB])

    outcome = await _go(game, _nav(), target)

    assert not outcome.success
    assert "not on the navigation map" in outcome.reason


async def test_a_zone_a_link_swaps_in_for_the_target_counts_as_arriving(monkeypatch):
    """Samsara Village: the quest line replaces the zone, and arriving in either is arriving."""
    game = _Game(monkeypatch, [_ISLAND])

    outcome = await _go(game, _nav({**_walk(_HUB, _S1), "also_arrives_in": [_ISLAND]}), _S1)

    assert outcome.success
    assert game.calls == []


async def test_the_step_nodes_own_straggler_recovery_is_off_while_they_run(monkeypatch):
    """Their recovery would port a straggler into a zone the rest are about to leave."""
    game = _Game(monkeypatch, [_HUB, _HUB])

    await _go(game, _nav(_walk(_HUB, _S1)), _S1)

    assert game.recovery == [False]
    assert node_helpers.straggler_recovery() is True


# ── Several clients: travelling together, or following one ─────────────────


_LONG = (_walk(_HUB, _S1), _walk(_S1, _S2), _walk(_S2, _ISLAND))
"""Three transitions: past the limit for travelling together."""


async def test_a_short_route_on_foot_is_traveled_together_without_a_survey(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True)

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.groups == [[1, 2], [1, 2]]
    assert game.waves == []
    game.survey.assert_not_awaited()


async def test_a_long_route_is_traveled_by_one_client_and_the_rest_port_to_it(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True)

    outcome = await _go(game, _nav(*_LONG), _ISLAND)

    assert outcome.success
    assert game.groups == [[1], [1], [1]]
    assert game.waves == [([2], [1])]
    assert all(c.where == _ISLAND for c in game.clients)


async def test_a_route_through_a_teleporter_is_traveled_by_one_client(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True)

    outcome = await _go(game, _nav(_teleporter(_HUB, _ISLAND)), _ISLAND)

    assert outcome.success
    assert game.groups == [[1]]
    assert game.waves == [([2], [1])]


async def test_clients_that_cannot_port_to_each_other_travel_together(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB])

    outcome = await _go(game, _nav(*_LONG), _ISLAND)

    assert outcome.success
    assert game.groups == [[1, 2], [1, 2], [1, 2]]
    assert game.waves == []


async def test_a_split_start_follows_the_client_with_the_cheapest_route(monkeypatch):
    game = _Game(monkeypatch, [_DELTA, _S1], friends=True)

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.groups == [[2]]
    assert game.calls[0][1]["from_zone"] == _S1
    assert game.waves == [([1], [2])]


async def test_a_client_already_there_is_the_one_the_others_port_to(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _S2], friends=True)

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.calls == []
    assert game.waves == [([1], [2])]


async def test_a_client_already_there_outranks_a_larger_group_elsewhere(monkeypatch):
    """Following the group would cost it a whole route and still move the client already there."""
    game = _Game(monkeypatch, [_ISLAND, _HUB, _HUB, _HUB], friends=True)
    see_each_other(game.clients[0])
    see_each_other(*game.clients[1:])

    outcome = await _go(game, _nav(*_LONG), _ISLAND)

    assert outcome.success
    assert game.calls == []
    assert game.waves == [([2, 3, 4], [1])]


async def test_a_client_already_there_that_nobody_can_port_to_does_not_lead(monkeypatch):
    game = _Game(monkeypatch, [_S2, _S1, _S1])
    see_each_other(game.clients[0])
    see_each_other(*game.clients[1:])
    names = _NAMES[:3]
    for client, name in zip(game.clients, names):
        wizard_names.remember(client, name)
    # Players 2 and 3 can port to each other, and nobody to player 1.
    game.survey.return_value = port_map.PortMap.from_survey(names, [[], [names[2]], [names[1]]], [(), (), ()])

    outcome = await _go(game, _nav(_teleporter(_S1, _S2)), _S2)

    assert outcome.success
    assert game.groups == [[2]]
    assert game.waves == [([1, 3], [2])]


async def test_a_client_that_cannot_port_travels_there_itself(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True, cannot_port={2})

    outcome = await _go(game, _nav(*_LONG), _ISLAND)

    assert outcome.success
    assert game.groups == [[1], [1], [1], [2], [2], [2]]
    assert game.waves == [([2], [1])]


async def test_a_client_that_misses_a_step_twice_waits_and_ports_at_the_end(monkeypatch):
    """No teleport and no step is run on it once it is left behind: it only ports, at the end."""
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True, misses={2: 2})

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.groups == [[1, 2], [2], [1]]
    assert game.waves == [([2], [1])]


async def test_a_client_that_makes_the_retry_carries_on_with_the_group(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True, misses={2: 1})

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert outcome.success
    assert game.groups == [[1, 2], [2], [1, 2]]
    assert game.waves == []


# ── One realm and one area (#133) ────────────────────────────────────────────


def _hops(game: _Game) -> list[dict]:
    return [params for name, params in game.calls if name == "hop"]


async def _sync(game: _Game, nav, target: str, *, machine=None, realm=None) -> zone_navigation.Outcome:
    return await zone_navigation.navigate(machine or FakeMachine(), game.clients, target, nav=nav, realm=realm)


async def test_players_together_in_the_zone_with_no_realm_to_reach_do_nothing(monkeypatch):
    game = _Game(monkeypatch, [_S2, _S2], friends=True)

    outcome = await _sync(game, _nav(_walk(_HUB, _S2)), _S2)

    assert outcome.success
    assert game.calls == [] and game.waves == []
    game.survey.assert_not_awaited()


async def test_players_in_the_zone_on_different_realms_port_to_the_larger_group(monkeypatch):
    game = _Game(monkeypatch, [_S2, _S2, _S2], friends=True)
    see_each_other(game.clients[0])
    see_each_other(game.clients[1], game.clients[2])

    outcome = await _sync(game, _nav(_walk(_HUB, _S2)), _S2)

    assert outcome.success
    assert game.calls == []
    assert game.waves == [([1], [2, 3])]


async def test_the_leader_hops_on_arrival_and_the_others_port_to_it(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True)

    outcome = await _sync(game, _nav(*_LONG), _ISLAND, realm="Balestrom")

    assert outcome.success
    assert game.names == ["walk", "walk", "walk", "hop"]
    assert _hops(game) == [{"player": 1, "realm": "Balestrom"}]
    assert game.waves == [([2], [1])]


async def test_players_already_there_still_join_the_leader_on_the_favorite_realm(monkeypatch):
    game = _Game(monkeypatch, [_S2, _S2], friends=True)

    outcome = await _sync(game, _nav(_walk(_HUB, _S2)), _S2, realm="Balestrom")

    assert outcome.success
    assert _hops(game) == [{"player": 1, "realm": "Balestrom"}]
    assert game.waves == [([2], [1])]


async def test_a_target_that_refuses_a_realm_change_is_hopped_for_on_the_way(monkeypatch):
    """Eerem Palace: the leader hops in the last zone that allows it, then walks in."""
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True)

    outcome = await _sync(game, _nav(*_LONG, no_realm_change=(_ISLAND,)), _ISLAND, realm="Balestrom")

    assert outcome.success
    assert game.names == ["walk", "walk", "hop", "walk"]
    assert game.calls[1][1]["to_zone"] == _S2


async def test_a_leader_already_where_realms_cannot_change_keeps_its_realm(monkeypatch):
    game = _Game(monkeypatch, [_S2, _S2], friends=True)
    machine = FakeMachine()

    outcome = await _sync(game, _nav(_walk(_HUB, _S2), no_realm_change=(_S2,)), _S2, machine=machine, realm="Balestrom")

    assert outcome.success
    assert _hops(game) == []
    assert any("Realms cannot be changed" in msg for msg, _ in machine.logs)


async def test_a_leader_that_cannot_hop_fails_the_sync(monkeypatch):
    game = _Game(monkeypatch, [_S2, _S2], friends=True)
    monkeypatch.setattr(change_realm, "to_realm", AsyncMock(return_value="FAILURE"))

    outcome = await _sync(game, _nav(_walk(_HUB, _S2)), _S2, realm="Balestrom")

    assert not outcome.success
    assert game.waves == []


async def test_a_player_who_cannot_port_travels_then_joins_the_leaders_realm(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True, cannot_port={2})
    see_each_other(game.clients[0])
    see_each_other(game.clients[1])
    read = AsyncMock(return_value="Wraith")
    monkeypatch.setattr(realm_list, "read_current_realm", read)

    async def to_realm(machine, client, realm, safety_delay):
        game.calls.append(("hop", {"player": game.number(client), "realm": realm}))
        see_each_other(*game.clients)
        return "SUCCESS"

    monkeypatch.setattr(change_realm, "to_realm", to_realm)

    outcome = await _sync(game, _nav(*_LONG), _ISLAND)

    assert outcome.success
    assert game.groups[-3:] == [[2], [2], [2]], "it travels the route itself"
    assert _hops(game) == [{"player": 2, "realm": "Wraith"}]
    assert read.await_args.args[1] is game.clients[0], "the realm is read from the leader's spellbook"
    game.clients[1].teleport.assert_awaited()


async def test_a_traveller_seen_beside_the_leader_needs_no_realm_change(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True, cannot_port={2})
    read = AsyncMock()
    monkeypatch.setattr(realm_list, "read_current_realm", read)

    outcome = await _sync(game, _nav(*_LONG), _ISLAND)

    assert outcome.success
    assert _hops(game) == []
    read.assert_not_awaited()


async def test_a_player_never_seen_with_the_leader_fails_the_sync(monkeypatch):
    monkeypatch.setattr(zone_navigation, "SIGHTING_SETTLE_TIMEOUT", 0.01)
    game = _Game(monkeypatch, [_S2, _S2], friends=True, cannot_port={2})
    see_each_other(game.clients[0])
    see_each_other(game.clients[1])
    monkeypatch.setattr(realm_list, "read_current_realm", AsyncMock(return_value="Wraith"))
    machine = FakeMachine()

    outcome = await _sync(game, _nav(_walk(_HUB, _S2)), _S2, machine=machine)

    assert not outcome.success
    assert "not every player is with player 1" in outcome.reason


async def test_a_leader_that_misses_a_step_is_replaced_by_one_who_made_it(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True, misses={1: 2})

    outcome = await _sync(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2, realm="Balestrom")

    assert outcome.success
    assert _hops(game) == [{"player": 2, "realm": "Balestrom"}]
    assert game.waves == [([1], [2])]


async def test_a_separate_bot_travels_everyone_and_never_ports_or_hops(monkeypatch):
    game = _Game(monkeypatch, [_HUB, _HUB], friends=True)
    see_each_other(game.clients[0])
    see_each_other(game.clients[1])
    machine = FakeMachine(options={"Player Realms": "Separate"})

    outcome = await _sync(game, _nav(*_LONG), _ISLAND, machine=machine, realm="Balestrom")

    assert outcome.success
    assert game.groups == [[1, 2], [1, 2], [1, 2]]
    assert game.waves == [] and _hops(game) == []
    game.survey.assert_not_awaited()


async def test_a_separate_bot_already_there_does_nothing(monkeypatch):
    game = _Game(monkeypatch, [_S2, _S2], friends=True)
    see_each_other(game.clients[0])
    see_each_other(game.clients[1])

    outcome = await _sync(game, _nav(_walk(_HUB, _S2)), _S2, machine=FakeMachine(options={"Player Realms": "Separate"}))

    assert outcome.success
    assert game.calls == [] and game.waves == []
