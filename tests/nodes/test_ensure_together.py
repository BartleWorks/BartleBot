"""Bringing a Together bot's players onto one realm: choose_leader and ensure_together (#130).

A player not seen with the leader ports to it and is confirmed by sight, and the node fails
if that port fails. A Separate bot (#132) is never gathered.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.node_helpers as nh
from bartlebot.clients.sightings import Reading, Sightings, choose_leader
from bartlebot.game import wizard_names
from tests.conftest import FakeClient, FakeMachine, see_each_other

# Synthetic names from tools/name_key_samples.json -- never a real player's.
_NAMES = ("Luke SkullHunter", "Misha SkullSeer", "Michelle HistoryBond", "Naomi HexPaint")


@pytest.fixture(autouse=True)
def _forget_names():
    yield
    wizard_names.clear_cache()


def _named(count: int) -> list:
    clients = [FakeClient() for _ in range(count)]
    for client, name in zip(clients, _NAMES):
        wizard_names.remember(client, name)
    return clients


def _check(*groups: tuple[int, ...]) -> Sightings:
    """A check in which each group sees itself and nobody else."""
    count = sum(len(g) for g in groups)
    readings: list = [None] * count
    for group in groups:
        for i in group:
            readings[i] = Reading(100 + i, "Z", None, False, frozenset(100 + j for j in group))
    return Sightings(list(range(count)), readings)


# ---------------------------------------------------------------------------
# choose_leader
# ---------------------------------------------------------------------------


def test_the_leader_is_in_the_largest_group():
    assert choose_leader(_check((0,), (1, 2, 3))) == 1


def test_a_tie_goes_to_player_order():
    assert choose_leader(_check((2, 3), (0, 1))) == 0
    assert choose_leader(_check((0,), (1,), (2,))) == 0


# ---------------------------------------------------------------------------
# ensure_together
# ---------------------------------------------------------------------------


async def test_players_who_see_each_other_are_left_alone():
    clients = _named(3)
    gather = AsyncMock()
    with patch.object(nh, "gather_at_player", gather):
        assert await nh.ensure_together(FakeMachine(), clients, where="here") is nh.TogetherOutcome.ALREADY
    gather.assert_not_awaited()


async def test_the_record_answers_without_a_check():
    clients = _named(2)
    machine = FakeMachine()
    await nh.read_sightings(clients, machine)
    for client in clients:
        client.get_base_entity_list.reset_mock()
    assert await nh.ensure_together(machine, clients, where="here") is nh.TogetherOutcome.ALREADY
    for client in clients:
        client.get_base_entity_list.assert_not_awaited()


async def test_a_split_ports_everyone_the_leader_does_not_see():
    a, b, c = _named(3)
    see_each_other(a)
    see_each_other(b, c)
    gather = AsyncMock(return_value=True)
    machine = FakeMachine()
    with patch.object(nh, "gather_at_player", gather):
        assert await nh.ensure_together(machine, [a, b, c], where="on the sigil") is nh.TogetherOutcome.GATHERED

    args, kwargs = gather.await_args
    assert args[1:4] == ([a, b, c], 1, _NAMES[1])  # P2 leads the larger group
    assert kwargs["only"] == [0]
    logged = " ".join(msg for msg, _ in machine.logs)
    assert "Player(s) [1] are not with P2 on the sigil" in logged
    assert not any(name in logged for name in _NAMES)


async def test_a_failed_port_to_the_leader_fails():
    a, b = _named(2)
    see_each_other(a)
    see_each_other(b)
    with patch.object(nh, "gather_at_player", AsyncMock(return_value=False)):
        assert await nh.ensure_together(FakeMachine(), [a, b], where="here") is nh.TogetherOutcome.FAILED


async def test_a_leader_whose_name_is_unknown_fails():
    a, b = FakeClient(), FakeClient()
    see_each_other(a)
    see_each_other(b)
    with patch.object(nh, "gather_at_player", AsyncMock()) as gather:
        assert await nh.ensure_together(FakeMachine(), [a, b], where="here") is nh.TogetherOutcome.FAILED
    gather.assert_not_awaited()


async def test_a_separate_bot_is_never_gathered():
    a, b = _named(2)
    see_each_other(a)
    see_each_other(b)
    machine = FakeMachine(options={"Player Realms": "Separate"})
    with patch.object(nh, "gather_at_player", AsyncMock()) as gather:
        assert await nh.ensure_together(machine, [a, b], where="here") is nh.TogetherOutcome.ALREADY
    gather.assert_not_awaited()
    a.get_base_entity_list.assert_not_awaited()


async def test_gather_moves_only_the_players_it_is_told_to(monkeypatch):
    monkeypatch.setattr(nh, "PORT_QUIET_GRACE", 0.05)
    a, b, c = _named(3)
    port = AsyncMock(return_value=nh.PortToFriendResult(seen_loading={0}))
    with patch.object(nh, "port_to_friend", port):
        assert await nh.gather_at_player(FakeMachine(), [a, b, c], 1, _NAMES[1], poll_interval=0.001, only=[0])
    assert port.await_args.args[0] == [a]
