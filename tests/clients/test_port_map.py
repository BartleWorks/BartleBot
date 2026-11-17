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
    group_manager.forget_all_groups()
    yield
    wizard_names.clear_cache()
    group_manager.forget_all_groups()


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


class _Record:
    """core's group record as port_waves sees it, faked, and a log of what was asked of it.

    `led` maps each target to the clients it is recorded leading; a target not in it has no
    record. `learned` is what learning a target finds, which becomes its record, and
    `missing` what missing_from_record names for a target. `grouped` is the HUD, True for
    anyone not in it.
    """

    def __init__(self, monkeypatch, *, led=None, learned=None, missing=None, grouped=None):
        self.led = dict(led or {})
        self.learned = dict(learned or {})
        self.missing = dict(missing or {})
        self.grouped = dict(grouped or {})
        self.checks: list = []
        self.learns: list = []
        self.forgotten: list = []
        for name in ("members_led_by", "missing_from_record", "learn_group", "hud_grouped", "forget_group"):
            monkeypatch.setattr(group_manager, name, getattr(self, name))
        monkeypatch.setattr(group_manager, "group_record", lambda client: None)

    async def members_led_by(self, target, clients):
        self.checks.append(target)
        if target not in self.led:
            return None
        return [c for c in clients if c in self.led[target]]

    async def missing_from_record(self, target, clients, proven):
        return [c for c in self.missing.get(target, ()) if c in clients]

    async def learn_group(self, target, clients):
        """A target `learned` does not name does not lead: nothing is recorded, as core does."""
        self.learns.append(target)
        if target not in self.learned:
            return []
        self.led[target] = list(self.learned[target])
        self.missing.pop(target, None)
        return [c for c in clients if c in self.led[target]]

    async def hud_grouped(self, client):
        return self.grouped.get(client, True)

    def forget_group(self, client):
        self.forgotten.append(client)
        self.led = {t: m for t, m in self.led.items() if t is not client and client not in m}


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


def test_the_leader_route_is_offered_only_to_a_mover_the_record_proves():
    a, b, c = _clients(_A, _B, _C)

    pm = PortMap.from_survey([_A, _B, _C], [[_B], [_A], []], [(_A, _B), (_A, _B), ()])

    assert pm.routes(a, b, led=[a]) == ["leader", "friend", "group"]
    assert pm.routes(a, b, led=[c]) == ["friend", "group"]
    assert pm.routes(a, b, led=[]) == ["friend", "group"]
    assert pm.routes(a, b) == ["friend", "group"], "the sync answer offers only what needs no record"


def test_the_leader_route_needs_no_survey_only_the_record():
    """A group formed by hand after the survey: nothing surveyed, and the record proves it."""
    a, b = _clients(_A, _B)

    pm = PortMap.from_survey([_A, _B], [[], []], [(), ()])

    assert pm.routes(a, b, led=[a]) == ["leader"]
    assert pm.routes(a, FakeClient(), led=[a]) == [], "names are still needed"


def test_a_failed_leader_route_is_not_offered_again_and_leaves_the_others():
    a, b = _clients(_A, _B)
    pm = PortMap.from_survey([_A, _B], [[], []], [(_A, _B), (_A, _B)])

    assert pm.demote(a, b, "leader")
    assert not pm.demote(a, b, "leader"), "a second demotion is a no-op"

    assert pm.routes(a, b, led=[a]) == ["group"]
    assert pm.routes(b, a, led=[b]) == ["leader", "group"]


def test_set_groups_offers_a_failed_leader_route_again():
    """A recovery regroup: form_group writes a new record, so the route is worth trying again."""
    a, b, c = _clients(_A, _B, _C)
    pm = PortMap.from_survey([_A, _B, _C], [[], [], []], [(_A, _B, _C)] * 3)
    pm.demote(a, b, "leader")

    pm.set_groups([_A, _B, _C], [(_A, _B), (_A, _B), ()])

    assert pm.routes(a, b, led=[a]) == ["leader", "group"]
    assert pm.routes(c, a, led=[]) == []


def test_leader_candidates_leave_out_failed_pairs_unknown_names_and_the_target():
    a, b, c = _clients(_A, _B, _C)
    unnamed = FakeClient()
    pm = PortMap()
    pm.demote(c, a, "leader")

    assert pm.leader_candidates(a, [a, b, c, unnamed]) == [b]


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

    assert pm.routes(clients[0], clients[1]) == ["friend"]
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
    assert "group" in pm.routes(clients[0], clients[1])
    assert "group" in pm.routes(clients[2], clients[1])
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

    assert pm.routes(clients[1], clients[0]) == ["friend", "group"]
    assert pm.routes(clients[2], clients[0]) == ["friend"]
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
    """The states cost nothing more, and they give the group route."""
    clients = _clients(_A, _B)
    _friends(monkeypatch, clients, [[_B], [_A]])
    roster = AsyncMock()
    monkeypatch.setattr(group_manager, "leader_roster", roster)

    pm = await port_map.survey(clients, names=[_A, _B], states=[GroupState(True, (_A, _B))] * 2)

    assert pm.routes(clients[1], clients[0]) == ["friend", "group"]
    roster.assert_not_awaited()


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
    _Record(monkeypatch, grouped={a: False})
    friend = AsyncMock()
    set_helper(monkeypatch, "port_to_friend", friend)
    port, _ = _fake_port([a, b])
    monkeypatch.setattr(group_manager, "port_to_group_member", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    friend.assert_not_awaited()


async def test_teleport_to_leader_is_preferred_for_a_mover_the_record_proves(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    record = _Record(monkeypatch, led={a: [b]})
    port, calls = _fake_leader_port(a)
    monkeypatch.setattr(group_manager, "port_to_leader", port)
    friend = AsyncMock()
    set_helper(monkeypatch, "port_to_friend", friend)
    machine = FakeMachine()

    assert await port_map.port_waves(machine, [a, b], [b], [a], pm) == []
    assert calls == [(([b],), {})], "Teleport to Leader takes the movers and no name"
    friend.assert_not_awaited()
    assert record.learns == [], "a valid record is enough"
    assert any(msg == "Porting player(s) [2] to player 1 by Teleport to Leader" for msg, _ in machine.logs)


@pytest.mark.parametrize(
    "led, grouped",
    [({}, {}), ({}, {"target": False}), ({}, {"mover": False}), ({"target": []}, {})],
    ids=["learned-not-leading", "target-ungrouped", "mover-ungrouped", "record-proves-no-one"],
)
async def test_teleport_to_leader_is_not_offered_to_a_mover_the_record_does_not_prove(monkeypatch, led, grouped):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    who = {"target": a, "mover": b}
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    _Record(
        monkeypatch,
        led={who[k]: v for k, v in led.items()},
        grouped={who[k]: v for k, v in grouped.items()},
    )
    leader = AsyncMock()
    monkeypatch.setattr(group_manager, "port_to_leader", leader)
    port, calls = _fake_port([a, b])
    set_helper(monkeypatch, "port_to_friend", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    leader.assert_not_awaited()
    assert calls == [([b], _A.lower())]


async def test_a_group_with_no_record_is_learned_once_and_then_used(monkeypatch):
    """Formed by hand: the target and the mover read grouped, and learning the target proves the mover."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    record = _Record(monkeypatch, learned={a: [b]})
    port, calls = _fake_leader_port(a)
    monkeypatch.setattr(group_manager, "port_to_leader", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    assert record.learns == [a]
    assert calls == [(([b],), {})]


@pytest.mark.parametrize("ungrouped", ["target", "mover"])
async def test_nothing_is_learned_unless_the_target_and_a_mover_read_grouped(monkeypatch, ungrouped):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    record = _Record(monkeypatch, learned={a: [b]}, grouped={a if ungrouped == "target" else b: False})
    set_helper(monkeypatch, "port_to_friend", _fake_port([a, b])[0])

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    assert record.learns == []


async def test_a_record_that_misses_a_grouped_mover_is_learned_again(monkeypatch):
    """The player added B to the group by hand after the record was written."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    record = _Record(monkeypatch, led={a: []}, missing={a: [b]}, learned={a: [b]})
    port, calls = _fake_leader_port(a)
    monkeypatch.setattr(group_manager, "port_to_leader", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    assert record.learns == [a]
    assert calls == [(([b],), {})]


async def test_a_target_is_learned_once_per_call_however_many_waves(monkeypatch):
    """A is learned in wave 1 and leads no one; C reaches A through B in wave 2, and A is not learned again."""
    a, b, c = _clients(_A, _B, _C, zones=["Alpha/S1", "Beta/Hub", "Gamma/Hub"])
    pm = PortMap.from_survey([_A, _B, _C], [[], [_A], [_B]], [(), (), ()])
    record = _Record(monkeypatch)
    set_helper(monkeypatch, "port_to_friend", _fake_port([a, b, c])[0])

    assert await port_map.port_waves(FakeMachine(), [a, b, c], [b, c], [a], pm) == []
    assert record.learns == [a, b], "A in wave 1; A again in wave 2 was skipped, B new"


async def test_a_member_of_a_recorded_group_is_not_learned(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(), ()])
    record = _Record(monkeypatch)
    monkeypatch.setattr(group_manager, "group_record", lambda client: object())
    set_helper(monkeypatch, "port_to_friend", _fake_port([a, b])[0])

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    assert record.learns == []


async def test_the_record_is_checked_once_per_target_per_wave_and_only_where_it_can_matter(monkeypatch):
    """Wave 1: A is checked once for B and C. C's port goes astray, which throws A's record away
    and fails C's leader route to A. Wave 2: only B, a new target for C, is checked; A is not."""
    a, b, c = _clients(_A, _B, _C, zones=["Alpha/S1", "Beta/Hub", "Gamma/Hub"])
    roster = (_A, _B, _C)
    pm = PortMap.from_survey([_A, _B, _C], [[], [], []], [roster, roster, roster])
    record = _Record(monkeypatch, led={a: [b, c]}, grouped={b: False})
    port, calls = _fake_leader_port(a, strays=[c])
    monkeypatch.setattr(group_manager, "port_to_leader", port)
    group, group_calls = _fake_port([a, b, c])
    monkeypatch.setattr(group_manager, "port_to_group_member", group)

    assert await port_map.port_waves(FakeMachine(), [a, b, c], [b, c], [a], pm) == []
    assert record.checks == [a, b]
    assert calls == [(([b, c],), {})]
    assert record.forgotten == [a], "a leader-route mover that landed elsewhere throws the record away"
    assert pm.routes(c, a, led=[c]) == ["group"], "the leader route that went astray is demoted"
    assert group_calls == [([c], _A.lower())]


async def test_a_failed_leader_port_falls_back_to_the_friends_list_in_the_same_call(monkeypatch):
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    record = _Record(monkeypatch, led={a: [b]})
    leader = AsyncMock(side_effect=ValueError("the group menu did not open"))
    monkeypatch.setattr(group_manager, "port_to_leader", leader)
    port, calls = _fake_port([a, b])
    set_helper(monkeypatch, "port_to_friend", port)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == []
    leader.assert_awaited_once()
    assert calls == [([b], _A.lower())]
    assert record.forgotten == [a]
    assert pm.routes(b, a, led=[b]) == ["friend", "group"], "the leader route stays demoted"


async def test_a_port_that_does_not_land_tries_the_next_route(monkeypatch):
    """Not only a port that cannot start: one that goes nowhere demotes its route too, and the next is tried."""
    a, b = _clients(_A, _B, zones=["Alpha/S1", "Beta/Hub"])
    pm = PortMap.from_survey([_A, _B], [[_B], [_A]], [(_A, _B), (_A, _B)])
    _Record(monkeypatch)
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
    _Record(monkeypatch)
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
    _Record(monkeypatch, led={a: [b]})
    leader = AsyncMock(side_effect=ValueError("the group menu did not open"))
    monkeypatch.setattr(group_manager, "port_to_leader", leader)
    friend = AsyncMock(side_effect=ValueError("not on the list"))
    set_helper(monkeypatch, "port_to_friend", friend)
    group = AsyncMock(side_effect=ValueError("not on the roster"))
    monkeypatch.setattr(group_manager, "port_to_group_member", group)

    assert await port_map.port_waves(FakeMachine(), [a, b], [b], [a], pm) == [b]
    assert (leader.await_count, friend.await_count, group.await_count) == (1, 1, 1)
    assert pm.routes(b, a, led=[b]) == []


async def test_a_client_with_no_routes_left_stays_waiting_while_the_others_arrive(monkeypatch):
    """B's only route, the group, fails and C's friend port lands: the wave after has nothing for B."""
    a, b, c = _clients(_A, _B, _C, zones=["Alpha/S1", "Beta/Hub", "Gamma/Hub"])
    pm = PortMap.from_survey([_A, _B, _C], [[], [], [_A]], [(_A, _B), (_A, _B), ()])
    _Record(monkeypatch)
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
