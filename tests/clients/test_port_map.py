"""Tests for bartlebot.clients.port_map: who can port to whom, and porting clients to each other in waves."""

from unittest.mock import AsyncMock

import pytest
from bartleby.runtime.clients import group_manager
from bartleby.runtime.clients.group_manager import GroupState, LeaderRoster
from bartleby.runtime.game import wizard_names

from bartlebot.clients import port_map
from bartlebot.clients.port_map import PortMap
from bartlebot.nodes import node_helpers
from tests.conftest import FakeClient, FakeMachine, see_each_other
from tests.node_helper_patches import set_helper

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


def _fake_leader_port(target, *, strays=()):
    """Teleport to Leader onto `target`, leaving `strays` where they are, and a record of each call's arguments."""
    calls: list[tuple[tuple, dict]] = []

    async def port(*args, **kwargs):
        calls.append((args, kwargs))
        for mover in args[0]:
            if mover not in strays:
                mover.where = target.where
        return node_helpers.PortToFriendResult(seen_loading=set(range(len(args[0]))))

    return port, calls


def _leading(*leaders):
    """group_manager.hud_leads, with `leaders` leading and everyone else a member; and its record of reads."""
    read: list = []

    async def hud_leads(client):
        read.append(client)
        return client in leaders

    return hud_leads, read


def _no_one_leader(monkeypatch):
    """The HUD names no one leader among the clients, so there is no roster to read on the cheap."""
    monkeypatch.setattr(group_manager, "leader_roster", AsyncMock(return_value=None))


def _friends(monkeypatch, clients, lists):
    by_client = dict(zip(clients, lists))

    async def read(client):
        if not by_client[client]:
            raise ValueError("No friends online")
        return by_client[client]

    set_helper(monkeypatch, "list_online_friends", read)
    set_helper(monkeypatch, "close_friends_list", AsyncMock(return_value=True))


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


def test_a_shared_group_offers_the_leader_route_only_while_the_target_leads():
    a, b = _clients(_A, _B)

    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])

    assert pm.routes(a, b, dst_leads=True) == ["leader", "friend", "group"]
    assert pm.routes(a, b, dst_leads=False) == ["friend", "group"]
    assert pm.routes(a, b, dst_leads=None) == ["friend", "group"], "a target the HUD cannot read is not offered"
    assert pm.routes(a, b) == ["friend", "group"], "the sync answer offers only what needs no read"


def test_friends_alone_never_give_the_leader_route():
    a, b = _clients(_A, _B)

    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])

    assert pm.routes(a, b, dst_leads=True) == ["friend"]


def test_a_demoted_leader_route_leaves_the_group_route():
    a, b = _clients(_A, _B)
    pm = PortMap.from_survey([_A, _B], [[], []], [(_A, _B), (_A, _B)])

    pm.demote(a, b, "leader")

    assert pm.routes(a, b, dst_leads=True) == ["group"]
    assert pm.routes(b, a, dst_leads=True) == ["leader", "group"]


def test_set_groups_rebuilds_the_leader_routes_from_the_new_group():
    """A recovery regroup: C crashed out, B now leads A alone, and a leader route demoted before it is back."""
    a, b, c = _clients(_A, _B, _C)
    pm = PortMap.from_survey([_A, _B, _C], [[], [], []], [(_A, _B, _C)] * 3)
    pm.demote(a, b, "leader")

    pm.set_groups([_A, _B, _C], [(_A, _B), (_A, _B), ()])

    assert pm.routes(a, b, dst_leads=True) == ["leader", "group"]
    assert pm.routes(c, a, dst_leads=True) == []
    assert pm.routes(a, c, dst_leads=True) == []


def test_may_lead_says_whose_leadership_is_worth_reading():
    a, b, c = _clients(_A, _B, _C)
    pm = PortMap.from_survey([_A, _B, _C], [[_C], [], [_A]], [(_A, _B), (_A, _B), ()])

    assert pm.may_lead(a, [b, c])
    assert not pm.may_lead(c, [a, b]), "a friend route alone puts no leader in reach"
    assert not pm.may_lead(a, [a])


def test_friends_everywhere_needs_every_pair_and_every_name():
    assert PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()]).friends_everywhere([_A, _B])
    assert not PortMap.from_survey([_A, _B], [[_B], []], [(), ()]).friends_everywhere([_A, _B])
    assert not PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()]).friends_everywhere([_A, None])


# ── The survey ───────────────────────────────────────────────────────────────


async def test_the_survey_skips_the_group_tab_when_friends_connect_everyone(monkeypatch):
    """No one leader on the HUD to read a roster from, and the friends lists need no group."""
    clients = _clients(_A, _B)
    friends = {clients[0]: [_B], clients[1]: [_A]}
    set_helper(monkeypatch, "list_online_friends", AsyncMock(side_effect=lambda c: friends[c]))
    close = AsyncMock(return_value=True)
    set_helper(monkeypatch, "close_friends_list", close)
    _no_one_leader(monkeypatch)
    states = AsyncMock()
    monkeypatch.setattr(group_manager, "read_group_states", states)

    pm = await port_map.survey(clients, names=[_A, _B])

    assert pm.routes(clients[0], clients[1], dst_leads=True) == ["friend"]
    states.assert_not_awaited()
    assert close.await_count == 2


@pytest.mark.parametrize("friends", [[[_B, _C], [_A, _C], [_A, _B]], [[], [], []]], ids=["friends", "strangers"])
async def test_the_survey_reads_only_the_one_leaders_roster(monkeypatch, friends):
    """Everyone grouped and one leading, by the HUD: one social-window visit puts all three in its group."""
    clients = _clients(_A, _B, _C)
    _friends(monkeypatch, clients, friends)
    found = AsyncMock(return_value=LeaderRoster(1, GroupState(True, (_B, _A.upper(), _C), leads=True)))
    monkeypatch.setattr(group_manager, "leader_roster", found)
    states = AsyncMock()
    monkeypatch.setattr(group_manager, "read_group_states", states)

    pm = await port_map.survey(clients, names=[_A, _B, _C])

    found.assert_awaited_once_with(clients)
    states.assert_not_awaited()
    assert pm.routes(clients[0], clients[1], dst_leads=True)[0] == "leader"
    assert pm.routes(clients[2], clients[1], dst_leads=True)[0] == "leader"
    assert "group" in pm.routes(clients[0], clients[2])


async def test_a_client_the_leaders_roster_leaves_out_has_no_group_route(monkeypatch):
    """C is grouped somewhere else: with the friends lists connecting everyone, nothing more is read."""
    clients = _clients(_A, _B, _C)
    _friends(monkeypatch, clients, [[_B, _C], [_A, _C], [_A, _B]])
    found = LeaderRoster(0, GroupState(True, (_A, _B), leads=True))
    monkeypatch.setattr(group_manager, "leader_roster", AsyncMock(return_value=found))
    states = AsyncMock()
    monkeypatch.setattr(group_manager, "read_group_states", states)

    pm = await port_map.survey(clients, names=[_A, _B, _C])

    assert pm.routes(clients[1], clients[0], dst_leads=True) == ["leader", "friend", "group"]
    assert pm.routes(clients[2], clients[0], dst_leads=True) == ["friend"]
    states.assert_not_awaited()


@pytest.mark.parametrize(
    "found",
    [None, LeaderRoster(0, GroupState(True, (_B,))), LeaderRoster(0, GroupState(True, (_A,)))],
    ids=["no one leader", "a roster without its leader", "a roster without one of ours"],
)
async def test_the_survey_reads_every_group_tab_when_one_roster_cannot_vouch_for_everyone(monkeypatch, found):
    clients = _clients(_A, _B)
    _friends(monkeypatch, clients, [[], []])
    monkeypatch.setattr(group_manager, "leader_roster", AsyncMock(return_value=found))
    states = AsyncMock(return_value=[GroupState(True, (_A, _B))] * 2)
    monkeypatch.setattr(group_manager, "read_group_states", states)

    pm = await port_map.survey(clients, names=[_A, _B])

    states.assert_awaited_once()
    assert pm.routes(clients[0], clients[1]) == ["group"]


async def test_the_survey_reads_each_friends_list_inside_the_mouse_handler(monkeypatch):
    """Opening the list is a click; outside the handler it raises HookNotActive (#139)."""
    clients = _clients(_A, _B)
    held: list[bool] = []

    async def read(client):
        entered, exited = client.mouse_handler.__aenter__.await_count, client.mouse_handler.__aexit__.await_count
        held.append(entered == exited + 1)
        return [_B] if client is clients[0] else [_A]

    set_helper(monkeypatch, "list_online_friends", read)
    set_helper(monkeypatch, "close_friends_list", AsyncMock(return_value=True))

    pm = await port_map.survey(clients, names=[_A, _B])

    assert held == [True, True]
    assert pm.routes(clients[0], clients[1]) == ["friend"]


async def test_the_survey_reads_the_group_tab_when_a_pair_is_not_friends(monkeypatch):
    clients = _clients(_A, _B)
    set_helper(monkeypatch, "list_online_friends", AsyncMock(side_effect=ValueError("No friends online")))
    set_helper(monkeypatch, "close_friends_list", AsyncMock(return_value=True))
    _no_one_leader(monkeypatch)
    monkeypatch.setattr(group_manager, "read_group_states", AsyncMock(return_value=[GroupState(True, (_A, _B))] * 2))

    pm = await port_map.survey(clients, names=[_A, _B])

    assert pm.routes(clients[0], clients[1]) == ["group"]


async def test_the_survey_reuses_group_states_it_is_given(monkeypatch):
    clients = _clients(_A, _B)
    set_helper(monkeypatch, "list_online_friends", AsyncMock(return_value=[]))
    set_helper(monkeypatch, "close_friends_list", AsyncMock(return_value=True))
    read = AsyncMock()
    monkeypatch.setattr(group_manager, "read_group_states", read)

    pm = await port_map.survey(clients, names=[_A, _B], states=[GroupState(True, (_A, _B))] * 2)

    assert pm.routes(clients[1], clients[0]) == ["group"]
    read.assert_not_awaited()


async def test_the_survey_keeps_the_group_it_is_given_even_when_friends_connect_everyone(monkeypatch):
    """The states cost nothing more, and they are what puts the leader in reach."""
    clients = _clients(_A, _B)
    _friends(monkeypatch, clients, [[_B], [_A]])
    hud = AsyncMock()
    monkeypatch.setattr(group_manager, "hud_leads", hud)

    pm = await port_map.survey(clients, names=[_A, _B], states=[GroupState(True, (_A, _B))] * 2)

    assert pm.routes(clients[1], clients[0], dst_leads=True) == ["leader", "friend", "group"]
    hud.assert_not_awaited()


# ── Porting in waves ─────────────────────────────────────────────────────────


async def test_waves_port_through_whoever_has_already_arrived(monkeypatch):
    a, b, c = _clients(_A, _B, _C, zones=["Alpha/S1", "Beta/Hub", "Gamma/Hub"])
    pm = PortMap.from_survey([_A, _B, _C], [[_B], [_A, _C], [_B]], [(), (), ()])
    port, calls = _fake_port([a, b, c])
    set_helper(monkeypatch, "port_to_friend", port)

    left = await port_map.port_waves(FakeMachine(), [a, b, c], [b, c], [a], pm)

    assert left == []
    assert b.where == c.where == "Alpha/S1"
    assert calls == [([b], _A.lower()), ([c], _B.lower())]


async def test_the_group_route_is_taken_when_there_is_no_friend_route(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[], []], [(_A, _B), (_A, _B)])
    monkeypatch.setattr(group_manager, "hud_leads", AsyncMock(return_value=False))
    friend = AsyncMock()
    set_helper(monkeypatch, "port_to_friend", friend)
    port, _ = _fake_port([a, b])
    monkeypatch.setattr(group_manager, "port_to_group_member", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    friend.assert_not_awaited()


async def test_teleport_to_leader_is_preferred_when_the_target_leads(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    hud_leads, _ = _leading(a)
    monkeypatch.setattr(group_manager, "hud_leads", hud_leads)
    port, calls = _fake_leader_port(a)
    monkeypatch.setattr(group_manager, "port_to_leader", port)
    friend = AsyncMock()
    set_helper(monkeypatch, "port_to_friend", friend)
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == []
    assert calls == [(([b],), {})], "Teleport to Leader takes the movers and no name"
    friend.assert_not_awaited()
    assert any(msg == "Porting player(s) [2] to player 1 by Teleport to Leader" for msg, _ in machine.logs)


@pytest.mark.parametrize("leads", [False, None], ids=["a member", "unreadable"])
async def test_teleport_to_leader_is_not_offered_to_a_target_that_does_not_lead(monkeypatch, leads):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    monkeypatch.setattr(group_manager, "hud_leads", AsyncMock(return_value=leads))
    leader = AsyncMock()
    monkeypatch.setattr(group_manager, "port_to_leader", leader)
    port, calls = _fake_port([a, b])
    set_helper(monkeypatch, "port_to_friend", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    leader.assert_not_awaited()
    assert calls == [([b], _A.lower())]


async def test_leadership_is_read_once_per_target_per_wave_and_only_where_it_can_matter(monkeypatch):
    """Wave 1: B and C share A's group, so A is read once for both; D, only A's friend, adds no read.
    C's port goes astray. Wave 2: C's leader route to A is demoted, so only B, who shares its group, is read."""
    a, b, c, d = _clients(_A, _B, _C, _D, zones=["Alpha/S1", "Beta/Hub", "Gamma/Hub", "Delta/Hub"])
    roster = (_A, _B, _C)
    pm = PortMap.from_survey([_A, _B, _C, _D], [[_D], [], [], [_A]], [roster, roster, roster, ()])
    hud_leads, read = _leading(a)
    monkeypatch.setattr(group_manager, "hud_leads", hud_leads)
    port, calls = _fake_leader_port(a, strays=[c])
    monkeypatch.setattr(group_manager, "port_to_leader", port)
    friend, _ = _fake_port([a, b, c, d])
    set_helper(monkeypatch, "port_to_friend", friend)
    group, group_calls = _fake_port([a, b, c, d])
    monkeypatch.setattr(group_manager, "port_to_group_member", group)

    assert await port_map.port_waves(FakeMachine(), [a, b, c, d], [b, c, d], [a], pm) == []
    assert read == [a, b]
    assert calls == [(([b, c],), {})]
    assert pm.routes(c, a, dst_leads=True) == ["group"], "the leader route that went astray is demoted"
    assert group_calls == [([c], _A.lower())]


async def test_a_failed_leader_port_falls_back_to_the_friends_list_in_the_same_call(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    monkeypatch.setattr(group_manager, "hud_leads", AsyncMock(return_value=True))
    leader = AsyncMock(side_effect=ValueError("the group menu did not open"))
    monkeypatch.setattr(group_manager, "port_to_leader", leader)
    port, calls = _fake_port([a, b])
    set_helper(monkeypatch, "port_to_friend", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    leader.assert_awaited_once()
    assert calls == [([b], _A.lower())]
    assert pm.routes(b, a, dst_leads=True) == ["friend", "group"], "the leader route stays demoted"


async def test_a_port_that_does_not_land_tries_the_next_route(monkeypatch):
    """Not only a port that cannot start: one that goes nowhere demotes its route too, and the next is tried."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    monkeypatch.setattr(group_manager, "hud_leads", AsyncMock(return_value=False))
    set_helper(
        monkeypatch, "port_to_friend", AsyncMock(return_value=node_helpers.PortToFriendResult(seen_loading=set()))
    )
    group, calls = _fake_port([a, b])
    monkeypatch.setattr(group_manager, "port_to_group_member", group)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    assert calls == [([b], _A.lower())]


async def test_a_refused_port_is_not_retried(monkeypatch):
    """A refusal is the moment, not the route: nothing is demoted, so no wave follows."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    monkeypatch.setattr(group_manager, "hud_leads", AsyncMock(return_value=False))
    refused = AsyncMock(return_value=node_helpers.PortToFriendResult(seen_loading=set(), refusal="busy"))
    set_helper(monkeypatch, "port_to_friend", refused)
    group = AsyncMock()
    monkeypatch.setattr(group_manager, "port_to_group_member", group)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == [b]
    refused.assert_awaited_once()
    group.assert_not_awaited()
    assert pm.routes(b, a) == ["friend", "group"]


async def test_a_pair_whose_every_route_fails_ends_with_the_mover_still_waiting(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    monkeypatch.setattr(group_manager, "hud_leads", AsyncMock(return_value=True))
    leader = AsyncMock(side_effect=ValueError("the group menu did not open"))
    monkeypatch.setattr(group_manager, "port_to_leader", leader)
    friend = AsyncMock(side_effect=ValueError("not on the list"))
    set_helper(monkeypatch, "port_to_friend", friend)
    group = AsyncMock(side_effect=ValueError("not on the roster"))
    monkeypatch.setattr(group_manager, "port_to_group_member", group)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == [b]
    assert (leader.await_count, friend.await_count, group.await_count) == (1, 1, 1)
    assert pm.routes(b, a, dst_leads=True) == []


async def test_a_client_with_no_routes_left_stays_waiting_while_the_others_arrive(monkeypatch):
    """B's only route, the group, fails and C's friend port lands: the wave after has nothing for B."""
    a, b, c = _clients(_A, _B, _C, zones=["Alpha/S1", "Beta/Hub", "Gamma/Hub"])
    pm = PortMap.from_survey([_A, _B, _C], [[], [], [_A]], [(_A, _B), (_A, _B), ()])
    monkeypatch.setattr(group_manager, "hud_leads", AsyncMock(return_value=False))
    group = AsyncMock(side_effect=ValueError("not on the roster"))
    monkeypatch.setattr(group_manager, "port_to_group_member", group)
    port, calls = _fake_port([a, b, c])
    set_helper(monkeypatch, "port_to_friend", port)

    assert await port_map.port_waves(FakeMachine(), [a, b, c], [b, c], [a], pm) == [b]
    group.assert_awaited_once()
    assert calls == [([c], _A.lower())]
    assert c.where == "Alpha/S1"


async def test_a_route_that_cannot_start_is_demoted_and_never_names_the_player(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    set_helper(
        monkeypatch, "port_to_friend", AsyncMock(side_effect=ValueError(f"Could not find friend matching {_A!r}"))
    )
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == [b]
    assert pm.routes(b, a) == []
    logged = " ".join(msg for msg, _ in machine.logs).casefold()
    assert _A.casefold() not in logged and _B.casefold() not in logged


async def test_a_port_that_does_not_land_is_demoted(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    set_helper(
        monkeypatch, "port_to_friend", AsyncMock(return_value=node_helpers.PortToFriendResult(seen_loading=set()))
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
    set_helper(monkeypatch, "port_to_friend", port)
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
    set_helper(monkeypatch, "port_to_friend", port)
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == []
    assert machine.are_together([a, b])


async def test_a_client_with_no_route_is_left_without_an_attempt(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    port = AsyncMock()
    set_helper(monkeypatch, "port_to_friend", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], PortMap()) == [b]
    port.assert_not_awaited()


async def test_a_port_is_not_judged_while_the_movers_zone_is_unreadable(monkeypatch):
    """The loading screen is gone but the zone is not yet readable: wait for it, don't count the port failed."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    port, _ = _fake_port([a, b])
    set_helper(monkeypatch, "port_to_friend", port)
    arrived = b.zone_name.side_effect
    b.zone_name = AsyncMock(side_effect=lambda: None if b.zone_name.await_count <= 2 else arrived())
    machine = FakeMachine()
    monkeypatch.setattr(node_helpers, "ZONE_READABLE_TIMEOUT", 10.0)  # the wait is the subject

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == []
    assert pm.routes(b, a) == ["friend"]


async def test_two_unreadable_zones_are_not_a_match(monkeypatch):
    """None == None must not count as arriving: a mover whose zone never turns readable has not landed."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    port, _ = _fake_port([a, b])
    set_helper(monkeypatch, "port_to_friend", port)
    monkeypatch.setattr(node_helpers, "read_zones", _quick(node_helpers.read_zones))
    a.zone_name = AsyncMock(return_value=None)
    b.zone_name = AsyncMock(return_value=None)
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == [b]
    assert any("did not end up in player 1's zone" in msg for msg, _ in machine.logs)


async def test_a_judged_port_logs_the_zones_it_compared(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    set_helper(monkeypatch, "port_to_friend", AsyncMock(return_value=node_helpers.PortToFriendResult(seen_loading={0})))
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == [b]
    logged = [msg for msg, _ in machine.logs]
    assert any("its zone is 'Alpha/S1'" in msg and "player 2's is 'Beta/Hub'" in msg for msg in logged)
    assert any("did not end up in player 1's zone" in msg for msg in logged)
    assert _A.casefold() not in " ".join(logged).casefold()


def _quick(read_zones):
    """read_zones with a short wait, so a zone that never turns readable does not hold the suite for ten seconds."""

    async def read(clients):
        return await read_zones(clients, 0.05, 0.01)

    return read
