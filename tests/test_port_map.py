"""Tests for bartlebot.port_map: who can port to whom, and porting clients to each other in waves."""

from unittest.mock import AsyncMock

import pytest

from bartlebot import group_manager, port_map, wizard_names
from bartlebot.group_manager import GroupState
from bartlebot.nodes import node_helpers
from bartlebot.port_map import PortMap
from tests.conftest import FakeClient, FakeMachine, see_each_other

# Synthetic names from tools/name_key_samples.json -- never a real player's.
_A, _B, _C, _D = "Luke SkullHunter", "Misha SkullSeer", "Michelle HistoryBond", "Naomi HexPaint"


@pytest.fixture(autouse=True)
def _fresh_names():
    wizard_names.clear_cache()
    yield
    wizard_names.clear_cache()


def _clients(*names: str, zones: list[str] | None = None) -> list:
    clients = []
    for i, name in enumerate(names):
        client = FakeClient()
        wizard_names.remember(client, name)
        client.where = zones[i] if zones else "Alpha/Hub"
        client.zone_name = AsyncMock(side_effect=lambda c=client: c.where)
        clients.append(client)
    return clients


def _fake_port(clients: list):
    """A port that moves the movers to the named target, and a record of each (movers, name) call."""
    by_name = {wizard_names.cached_name(c).lower(): c for c in clients}
    calls: list[tuple[list, str]] = []

    async def port(movers, name):
        calls.append((list(movers), name))
        for mover in movers:
            mover.where = by_name[name].where
        return node_helpers.PortToFriendResult(seen_loading=set(range(len(movers))))

    return port, calls


# ── The map ──────────────────────────────────────────────────────────────────


def test_friends_become_routes_between_our_own_clients_only():
    a, b = _clients(_A, _B)

    pm = PortMap.from_survey([_A, _B], [[_B.upper(), _D], [_A]], [(), ()])

    assert pm.routes(a, b) == ["friend"]
    assert pm.routes(b, a) == ["friend"]


def test_an_unreadable_friends_list_gives_that_client_no_friend_route():
    a, b = _clients(_A, _B)

    pm = PortMap.from_survey([_A, _B], [None, [_A]], [(), ()])

    assert pm.routes(a, b) == []
    assert pm.routes(b, a) == ["friend"]


def test_a_shared_group_is_a_route_between_clients_who_are_not_friends():
    a, b = _clients(_A, _B)

    pm = PortMap.from_survey([_A, _B], [[], []], [(_A, _B), (_A, _B)])

    assert pm.routes(a, b) == ["group"]


def test_the_friends_list_is_preferred_over_the_group():
    a, b = _clients(_A, _B)

    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])

    assert pm.routes(a, b) == ["friend", "group"]


def test_a_client_whose_name_is_unknown_has_no_routes():
    a, b = _clients(_A, _B)
    unnamed = FakeClient()

    pm = PortMap.from_survey([_A, None], [[_B], [_A]], [(), ()])

    assert pm.routes(a, b) == []
    assert pm.routes(a, unnamed) == []


def test_a_demoted_route_is_not_offered_again():
    a, b = _clients(_A, _B)
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])

    pm.demote(a, b, "friend")

    assert pm.routes(a, b) == ["group"]
    assert pm.routes(b, a) == ["friend", "group"]


def test_set_groups_replaces_the_group_routes_and_keeps_the_friend_routes():
    """A recovery regroup: C crashed out of the group, and the group now holds A and B only."""
    a, b, c = _clients(_A, _B, _C)
    pm = PortMap.from_survey([_A, _B, _C], [[_C], [], [_A]], [(_A, _B, _C)] * 3)
    pm.demote(a, c, "friend")

    pm.set_groups([_A, _B, _C], [(_A, _B), (_A, _B), ()])

    assert pm.routes(a, b) == ["group"]
    assert pm.routes(a, c) == [], "a demoted friend route came back, or a stale group route stayed"
    assert pm.routes(c, a) == ["friend"]
    assert pm.routes(c, b) == []


def test_set_groups_restores_a_group_route_demoted_against_the_old_group():
    a, b = _clients(_A, _B)
    pm = PortMap.from_survey([_A, _B], [[], []], [(_A, _B), (_A, _B)])
    pm.demote(a, b, "group")

    pm.set_groups([_A, _B], [(_A, _B), (_A, _B)])

    assert pm.routes(a, b) == ["group"]


def test_reach_counts_a_client_that_gets_there_through_another():
    """C is friends only with B, and B with A: C still reaches A, by porting to B once B is there."""
    a, b, c, d = _clients(_A, _B, _C, _D)

    pm = PortMap.from_survey([_A, _B, _C, _D], [[_B], [_A, _C], [_B], []], [(), (), (), ()])

    assert pm.reach(a, [a, b, c, d]) == [b, c]


def test_friends_everywhere_needs_every_pair_and_every_name():
    assert PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()]).friends_everywhere([_A, _B])
    assert not PortMap.from_survey([_A, _B], [[_B], []], [(), ()]).friends_everywhere([_A, _B])
    assert not PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()]).friends_everywhere([_A, None])


# ── The survey ───────────────────────────────────────────────────────────────


async def test_the_survey_skips_the_group_tab_when_friends_connect_everyone(monkeypatch):
    clients = _clients(_A, _B)
    friends = {clients[0]: [_B], clients[1]: [_A]}
    monkeypatch.setattr(node_helpers, "list_online_friends", AsyncMock(side_effect=lambda c: friends[c]))
    close = AsyncMock(return_value=True)
    monkeypatch.setattr(node_helpers, "close_friends_list", close)
    states = AsyncMock()
    monkeypatch.setattr(group_manager, "read_group_states", states)

    pm = await port_map.survey(clients, names=[_A, _B])

    assert pm.routes(clients[0], clients[1]) == ["friend"]
    states.assert_not_awaited()
    assert close.await_count == 2


async def test_the_survey_reads_the_group_tab_when_a_pair_is_not_friends(monkeypatch):
    clients = _clients(_A, _B)
    monkeypatch.setattr(node_helpers, "list_online_friends", AsyncMock(side_effect=ValueError("No friends online")))
    monkeypatch.setattr(node_helpers, "close_friends_list", AsyncMock(return_value=True))
    monkeypatch.setattr(group_manager, "read_group_states", AsyncMock(return_value=[GroupState(True, (_A, _B))] * 2))

    pm = await port_map.survey(clients, names=[_A, _B])

    assert pm.routes(clients[0], clients[1]) == ["group"]


async def test_the_survey_reuses_group_states_it_is_given(monkeypatch):
    clients = _clients(_A, _B)
    monkeypatch.setattr(node_helpers, "list_online_friends", AsyncMock(return_value=[]))
    monkeypatch.setattr(node_helpers, "close_friends_list", AsyncMock(return_value=True))
    read = AsyncMock()
    monkeypatch.setattr(group_manager, "read_group_states", read)

    pm = await port_map.survey(clients, names=[_A, _B], states=[GroupState(True, (_A, _B))] * 2)

    assert pm.routes(clients[1], clients[0]) == ["group"]
    read.assert_not_awaited()


# ── Porting in waves ─────────────────────────────────────────────────────────


async def test_waves_port_through_whoever_has_already_arrived(monkeypatch):
    a, b, c = _clients(_A, _B, _C, zones=["Alpha/S1", "Beta/Hub", "Gamma/Hub"])
    pm = PortMap.from_survey([_A, _B, _C], [[_B], [_A, _C], [_B]], [(), (), ()])
    port, calls = _fake_port([a, b, c])
    monkeypatch.setattr(node_helpers, "port_to_friend", port)

    left = await port_map.port_waves(FakeMachine(), [a, b, c], [b, c], [a], pm)

    assert left == []
    assert b.where == c.where == "Alpha/S1"
    assert calls == [([b], _A.lower()), ([c], _B.lower())]


async def test_the_group_route_is_taken_when_there_is_no_friend_route(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[], []], [(_A, _B), (_A, _B)])
    friend = AsyncMock()
    monkeypatch.setattr(node_helpers, "port_to_friend", friend)
    port, _ = _fake_port([a, b])
    monkeypatch.setattr(group_manager, "port_to_group_member", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    friend.assert_not_awaited()


async def test_a_route_that_cannot_start_is_demoted_and_never_names_the_player(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    monkeypatch.setattr(
        node_helpers, "port_to_friend", AsyncMock(side_effect=ValueError(f"Could not find friend matching {_A!r}"))
    )
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == [b]
    assert pm.routes(b, a) == []
    logged = " ".join(msg for msg, _ in machine.logs).casefold()
    assert _A.casefold() not in logged and _B.casefold() not in logged


async def test_a_port_that_does_not_land_is_demoted(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    monkeypatch.setattr(
        node_helpers, "port_to_friend", AsyncMock(return_value=node_helpers.PortToFriendResult(seen_loading=set()))
    )

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == [b]
    assert pm.routes(b, a) == []


async def test_a_port_that_lands_in_the_zone_but_not_the_realm_is_demoted(monkeypatch):
    """The zone matches and nobody is seen: the port left the mover on its own realm (#130)."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    see_each_other(a)
    see_each_other(b)
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    port, _ = _fake_port([a, b])
    monkeypatch.setattr(node_helpers, "port_to_friend", port)
    monkeypatch.setattr(port_map, "SIGHTING_SETTLE_TIMEOUT", 0.05)
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == [b]
    assert b.where == "Alpha/S1"
    assert pm.routes(b, a) == []
    assert any("not its realm" in msg for msg, _ in machine.logs)


async def test_a_port_that_lands_with_the_target_is_recorded_together(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    port, _ = _fake_port([a, b])
    monkeypatch.setattr(node_helpers, "port_to_friend", port)
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == []
    assert machine.are_together([a, b])


async def test_a_client_with_no_route_is_left_without_an_attempt(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    port = AsyncMock()
    monkeypatch.setattr(node_helpers, "port_to_friend", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], PortMap()) == [b]
    port.assert_not_awaited()
