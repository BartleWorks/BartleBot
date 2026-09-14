"""Unit tests for navigate(): one planned step at a time, World Gates and HOME included."""

from unittest.mock import AsyncMock

import pytest

from bartlebot import worlds, zone_names, zone_nav
from bartlebot.nodes import node_helpers, wizard_city, zone_navigation
from bartlebot.nodes.interaction import interact
from bartlebot.nodes.teleportation import teleport_to_world_hub
from bartlebot.nodes.utility import sleep
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list, enter_zone_via_object, enter_zone_via_ride
from tests.conftest import FakeClient, FakeMachine

_HUB, _DOOR, _S1, _S2, _ISLAND = "Alpha/Hub", "Alpha/Door", "Alpha/S1", "Alpha/S2", "Alpha/Island"
_HOME = "Alpha/Dorm"
_DELTA = "Delta/Hub"
"""Delta is another world, its World Gate in its hub."""
_LANDS_IN = {"Alpha/*": _DOOR, "Delta/*": _DELTA}
"""Where a gate step, which arrives anywhere in a world, puts the fake clients."""


def _xyz(x: float) -> dict:
    return {"x": x, "y": 0.0, "z": 0.0}


def _walk(src: str, dst: str, verified: bool = True) -> dict:
    return {"kind": "walk", "from": src, "to": dst, "xyz": _xyz(1), "reset_xyz": _xyz(2), "verified": verified}


def _nav(*links, networks=(), beta_links=()) -> zone_nav.NavMap:
    """Alpha, with HOME's dorm a walk from its hub; Beta, a side world entered from it; and Delta, with a gate."""
    return zone_nav.parse(
        {
            "home": _HOME,
            "worlds": {
                "Alpha": {
                    "hub": _HUB,
                    "door": _DOOR,
                    "zones": {z: {} for z in (_HUB, _DOOR, _S1, _S2, _ISLAND, _HOME)},
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
    """Clients whose zone the fake nodes move, and a record of every node call."""

    def __init__(self, monkeypatch, zones: list[str], results: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.clients = []
        for zone in zones:
            client = FakeClient()
            client.where = zone
            client.zone_name = AsyncMock(side_effect=lambda c=client: c.where)
            self.clients.append(client)
        results = results or {}

        def fake(name: str, arrive):
            async def run(machine, clients, params):
                self.calls.append((name, params))
                if results.get(name, "SUCCESS") == "SUCCESS" and arrive is not None:
                    for c in clients:
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
        monkeypatch.setattr(worlds, "display_names", lambda world: [world, world.lower()])
        go_home = fake("home", lambda p: _HOME)
        monkeypatch.setattr(
            wizard_city,
            "teleport_home_and_verify_dorm",
            lambda machine, clients: _succeeded(go_home(machine, clients, {})),
        )

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
    teleporter = {"kind": "object", "from": _HUB, "to": _ISLAND, "interact_xyz": _xyz(3), "reset_xyz": _xyz(4)}

    await _go(game, _nav({**teleporter, "sequential": True, "object_use_delay": 8.0, "verified": True}), _ISLAND)

    [(name, params)] = game.calls
    assert name == "object"
    assert params["sequential"] is True
    assert params["object_use_delay"] == 8.0


async def test_avoided_kinds_are_left_out_of_the_route(monkeypatch):
    game = _Game(monkeypatch, [_HUB])
    teleporter = {"kind": "object", "from": _HUB, "to": _S2, "interact_xyz": _xyz(3), "reset_xyz": _xyz(4)}
    nav = _nav({**teleporter, "verified": True}, _walk(_HUB, _S1), _walk(_S1, _S2))

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


async def test_a_failed_step_stops_the_run(monkeypatch):
    game = _Game(monkeypatch, [_HUB], results={"walk": "FAILURE"})

    outcome = await _go(game, _nav(_walk(_HUB, _S1), _walk(_S1, _S2)), _S2)

    assert not outcome.success
    assert game.names == ["walk"]
    assert outcome.taken == []


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
    teleporter = {"kind": "object", "from": _S2, "to": _HUB, "interact_xyz": _xyz(3), "reset_xyz": _xyz(4)}

    await _go(game, _nav({**teleporter, "verified": True}), _HUB)

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
