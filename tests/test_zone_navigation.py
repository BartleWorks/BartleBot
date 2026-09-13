"""Unit tests for navigate(): the world first, then one planned step at a time."""

from unittest.mock import AsyncMock

import pytest

from bartlebot import zone_names, zone_nav
from bartlebot.nodes import node_helpers, zone_navigation
from bartlebot.nodes.teleportation import go_to_world, teleport_to_world_hub
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list, enter_zone_via_object
from tests.conftest import FakeClient, FakeMachine

_HUB, _DOOR, _S1, _S2, _ISLAND = "Alpha/Hub", "Alpha/Door", "Alpha/S1", "Alpha/S2", "Alpha/Island"


def _xyz(x: float) -> dict:
    return {"x": x, "y": 0.0, "z": 0.0}


def _walk(src: str, dst: str, verified: bool = True) -> dict:
    return {"kind": "walk", "from": src, "to": dst, "xyz": _xyz(1), "reset_xyz": _xyz(2), "verified": verified}


def _nav(*links, networks=(), beta_links=()) -> zone_nav.NavMap:
    return zone_nav.parse(
        {
            "worlds": {
                "Alpha": {
                    "hub": _HUB,
                    "door": _DOOR,
                    "zones": {z: {} for z in (_HUB, _DOOR, _S1, _S2, _ISLAND)},
                    "links": list(links),
                    "list_networks": list(networks),
                },
                "Beta": {
                    "hub": "Beta/Hub",
                    "entered_from": "Alpha",
                    "zones": {"Beta/Hub": {}},
                    "links": list(beta_links),
                },
            }
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
                if results.get(name, "SUCCESS") == "SUCCESS":
                    for c in clients:
                        c.where = arrive(params)
                return results.get(name, "SUCCESS")

            return run

        monkeypatch.setattr(enter_zone, "run", fake("walk", lambda p: p["to_zone"]))
        monkeypatch.setattr(enter_zone_via_object, "run", fake("object", lambda p: p["to_zone"]))
        monkeypatch.setattr(enter_zone_via_list, "run", fake("list", lambda p: p["to_zone"]))
        monkeypatch.setattr(teleport_to_world_hub, "run", fake("hub", lambda p: p["zone"]))
        monkeypatch.setattr(go_to_world, "run", fake("go_to_world", lambda p: _DOOR))
        monkeypatch.setattr(node_helpers, "is_enabled_by_path", AsyncMock(return_value=True))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


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


async def test_another_world_goes_through_the_spiral_door_then_the_hub_button(monkeypatch):
    game = _Game(monkeypatch, ["Gamma/Somewhere"])

    outcome = await _go(game, _nav(_walk(_HUB, _S1)), _S1)

    assert outcome.success
    assert game.calls[0] == ("go_to_world", {"world": "Alpha"})
    assert game.names[1:] == ["hub", "walk"]


async def test_a_side_world_is_reached_through_its_parents_door(monkeypatch):
    game = _Game(monkeypatch, ["Gamma/Somewhere"])

    await _go(game, _nav(_walk(_HUB, _S1), beta_links=[_walk(_S1, "Beta/Hub")]), "Beta/Hub")

    assert game.calls[0] == ("go_to_world", {"world": "Alpha"})
    assert game.names[-1] == "walk"


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


async def test_a_list_step_without_a_known_name_fails_before_moving(monkeypatch):
    game = _Game(monkeypatch, [_HUB])
    monkeypatch.setattr(zone_names, "display_names", lambda zone: [])
    spot = {"interact_xyz": _xyz(5), "reset_xyz": _xyz(6)}

    outcome = await _go(game, _nav(networks=[{"zones": {_HUB: spot, _ISLAND: spot}, "verified": True}]), _ISLAND)

    assert not outcome.success
    assert "display name" in outcome.reason
    assert game.calls == []


async def test_a_ride_cannot_run_yet(monkeypatch):
    game = _Game(monkeypatch, [_S1])
    ride = {"kind": "ride", "from": _S1, "to": _ISLAND, "interact_xyz": _xyz(1), "reset_xyz": _xyz(2)}

    outcome = await _go(game, _nav({**ride, "ride_zone": "Alpha/Boat", "verified": True}), _ISLAND)

    assert not outcome.success
    assert "ride helper" in outcome.reason


async def test_no_route_tries_the_door_once_then_says_so(monkeypatch):
    game = _Game(monkeypatch, [_HUB])

    outcome = await _go(game, _nav(), _ISLAND)

    assert not outcome.success
    assert "no route" in outcome.reason
    assert game.names == ["go_to_world"]


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


async def test_a_cooling_hub_button_is_walked_around(monkeypatch):
    game = _Game(monkeypatch, [_S2])
    monkeypatch.setattr(node_helpers, "is_enabled_by_path", AsyncMock(return_value=False))

    await _go(game, _nav(_walk(_S2, _S1), _walk(_S1, _HUB)), _HUB)

    assert game.names == ["walk", "walk"]


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
