"""Tests for node_helpers.port_clients and gather_at_player: what counts as a port having happened.

A port shows a loading screen only when it changes zone or realm. Within one zone and realm the
player's position jumps onto the target instead, and a port onto someone already beside you shows
nothing at all (#14, #69).
"""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.node_helpers as nh
from bartlebot.game import wizard_names
from tests.conftest import FakeMachine
from tests.port_sim import PortSim

_NAME = "Luke SkullHunter"
_AT = (100.0, 200.0, 0.0)
_BESIDE = (103.0, 200.0, 0.0)
_FAR = (900.0, -400.0, 0.0)


@pytest.fixture(autouse=True)
def _no_dialogs_and_no_names_left_over():
    with patch.object(nh, "resolve_modal", new=AsyncMock(return_value=nh.ModalOutcome(answered=False))):
        yield
    wizard_names.clear_cache()


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(nh, "TRIGGER_TIMEOUT", 0.05)
    monkeypatch.setattr(nh, "PORT_QUIET_GRACE", 0.05)


def _sim() -> PortSim:
    sim = PortSim()
    sim.target = sim.player("Dst", _AT, name=_NAME)
    return sim


async def _port(sim: PortSim, movers: list, **kwargs) -> nh.PortToFriendResult:
    kwargs.setdefault("trigger_timeout", 0.05)
    kwargs.setdefault("ceiling", 5.0)
    kwargs.setdefault("poll_interval", 0.001)
    kwargs.setdefault("quiet_grace", 0.05)
    return await nh.port_clients(movers, lambda c: sim.teleport(c, _NAME.lower()), **kwargs)


# ---------------------------------------------------------------------------
# port_clients
# ---------------------------------------------------------------------------


async def test_a_loading_screen_is_a_port():
    sim = _sim()
    mover = sim.player("Src", _FAR)

    result = await _port(sim, [mover])

    assert result.seen_loading == {0} and not result.jumped and not result.presumed
    assert result.missing(1) == []
    assert sim.times_ported(mover) == 1


async def test_a_jump_with_no_loading_screen_is_a_port():
    """#69: the same zone and realm never loads, and was re-ported until the node gave up."""
    sim = _sim()
    mover = sim.player("Dst", _FAR)

    result = await _port(sim, [mover])

    assert result.jumped == {0} and not result.seen_loading
    assert sim.times_ported(mover) == 1


async def test_a_client_that_may_be_there_is_reissued_once_then_presumed():
    sim = _sim()
    mover = sim.player("Dst", _BESIDE)

    result = await _port(sim, [mover], maybe_there={0})

    assert result.presumed == {0}
    assert result.missing(1) == []
    assert sim.times_ported(mover) == 2, "the first port, then one re-issue in case it never registered"


async def test_a_client_that_may_be_there_but_loads_is_not_presumed():
    """Beside the target on another realm: the same coordinates, but the port still loads."""
    sim = _sim()
    mover = sim.player("Dst", _AT, realm="elsewhere")

    result = await _port(sim, [mover], maybe_there={0})

    assert result.seen_loading == {0} and not result.presumed
    assert sim.times_ported(mover) == 1


async def test_a_quiet_client_not_flagged_is_never_presumed_and_is_reissued_once_per_window():
    """The retry used to fire on every poll once the first window had passed."""
    sim = _sim()
    mover = sim.player("Src", _FAR, port="ignored")

    result = await _port(sim, [mover], ceiling=0.3)

    assert result.missing(1) == [0]
    assert 2 <= sim.times_ported(mover) <= 1 + 0.3 / 0.05 + 2


async def test_an_unreadable_position_is_not_mistaken_for_a_jump():
    sim = _sim()
    mover = sim.player("Dst", _FAR, port="ignored")
    mover.body.position = AsyncMock(side_effect=RuntimeError("body not readable"))

    result = await _port(sim, [mover], ceiling=0.1)

    assert result.missing(1) == [0]


async def test_with_a_target_a_quiet_client_is_presumed_only_once_seen():
    sim = _sim()
    mover = sim.player("Dst", _BESIDE)

    result = await _port(sim, [mover], maybe_there={0}, target=sim.target)

    assert result.presumed == {0}


async def test_with_a_target_a_quiet_client_on_another_realm_is_never_presumed():
    """Beside the target on another realm, with a port that never registers (#130).

    Standing beside it is the zone and the spot; only being seen is the realm. Without the
    target this is presumed there, and the player goes on to fight or enter alone.
    """
    sim = _sim()
    mover = sim.player("Dst", _BESIDE, realm="elsewhere", port="ignored")

    blind = await _port(sim, [mover], maybe_there={0}, ceiling=0.3)
    seen = await _port(sim, [mover], maybe_there={0}, ceiling=0.3, target=sim.target)

    assert blind.presumed == {0}
    assert not seen.presumed and seen.missing(1) == [0]
    assert sim.times_ported(mover) > 4, "not presumed, so re-issued once per window like any other"


def test_missing_lists_only_clients_that_showed_nothing():
    result = nh.PortToFriendResult({0}, jumped=frozenset({1}), presumed=frozenset({2}))
    assert result.missing(4) == [3]


async def test_one_port_that_cannot_start_leaves_the_others_to_land():
    """The first error used to end the whole start, and a player who had ported was
    counted as not having started, then sent to travel there with the rest."""
    sim = _sim()
    landed, failed, also_landed = (
        sim.player("Src", _FAR),
        sim.player("Src", _FAR, port="fails"),
        sim.player("Src", _FAR),
    )

    result = await _port(sim, [landed, failed, also_landed])

    assert result.seen_loading == {0, 2}
    assert result.unstarted == {1}
    assert result.missing(3) == [1]
    assert landed.where[0] == "Dst" and also_landed.where[0] == "Dst"


async def test_no_port_that_can_start_raises():
    sim = _sim()
    movers = [sim.player("Src", _FAR, port="fails"), sim.player("Src", _FAR, port="fails")]

    with pytest.raises(ValueError):
        await _port(sim, movers)


async def test_a_reissue_that_cannot_start_is_not_tried_again(fast):
    sim = _sim()
    straggler = sim.player("Src", _FAR, port="ignored")
    other = sim.player("Src", _FAR)
    sim.fail_from_call = 3  # both first ports start; the straggler's re-issue does not

    result = await _port(sim, [straggler, other], ceiling=0.5)

    assert result.unstarted == {0}
    assert result.seen_loading == {1}
    assert sim.times_ported(straggler) == 2


# ---------------------------------------------------------------------------
# gather_at_player
# ---------------------------------------------------------------------------


async def test_gather_ports_everyone_but_the_target_wherever_they_start(fast):
    sim = _sim()
    elsewhere = sim.player("Src", _FAR)
    same_zone = sim.player("Dst", _FAR)
    beside = sim.player("Dst", _BESIDE)
    other_realm = sim.player("Dst", _AT, realm="elsewhere")
    clients = [sim.target, elsewhere, same_zone, beside, other_realm]
    machine = FakeMachine()

    with patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert await nh.gather_at_player(machine, clients, 0, _NAME, poll_interval=0.001)

    assert sim.times_ported(sim.target) == 0
    assert all(sim.times_ported(c) >= 1 for c in clients[1:]), "nobody is skipped for sharing the target's zone"
    assert all(
        c.where[0] == "Dst" and nh.distance(c.where[1], sim.target.where[1]) <= nh.POSITION_MARGIN for c in clients
    )
    logs = [msg for msg, _ in machine.logs]
    assert any("P4" in msg and "already beside" in msg for msg in logs)
    assert not any(_NAME.casefold() in msg.casefold() for msg in logs)


async def test_gather_fails_when_a_player_lands_somewhere_else(fast, monkeypatch):
    monkeypatch.setattr(nh, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _sim()
    lost = sim.player("Src", _FAR, port="lost")
    machine = FakeMachine()

    with patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert not await nh.gather_at_player(machine, [sim.target, lost], 0, _NAME, poll_interval=0.001)

    assert any("not every player ended up with P1" in msg for msg, _ in machine.logs)


async def test_gather_fails_when_a_player_beside_the_target_is_on_another_realm(fast, monkeypatch):
    monkeypatch.setattr(nh, "ZONE_CHANGE_TIMEOUT", 0.3)
    sim = _sim()
    other_realm = sim.player("Dst", _BESIDE, realm="elsewhere", port="ignored")
    machine = FakeMachine()

    with patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert not await nh.gather_at_player(machine, [sim.target, other_realm], 0, _NAME, poll_interval=0.001)


async def test_gather_records_the_players_it_saw_together(fast):
    sim = _sim()
    mover = sim.player("Src", _FAR)
    machine = FakeMachine()

    with patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert await nh.gather_at_player(machine, [sim.target, mover], 0, _NAME, poll_interval=0.001)

    assert machine.are_together([sim.target, mover])


async def test_gather_fails_without_naming_a_friend_it_cannot_find(fast):
    sim = _sim()
    sim.fail_from_call = 1
    mover = sim.player("Src", _FAR)
    machine = FakeMachine()

    with patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert not await nh.gather_at_player(machine, [sim.target, mover], 0, _NAME, poll_interval=0.001)

    assert not any(_NAME.casefold() in msg.casefold() for msg, _ in machine.logs)


async def test_gather_with_nobody_to_port_is_done():
    sim = _sim()
    with patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)) as tp:
        assert await nh.gather_at_player(FakeMachine(), [sim.target], 0, _NAME)
    tp.assert_not_awaited()
