"""Tests for node_helpers.port_clients and gather_at_player: what counts as a port having happened.

A port shows a loading screen only when it changes zone or realm. Within one zone and realm the
player's position jumps onto the target instead, and a port onto someone already beside you shows
nothing at all (#14, #69).
"""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest
from bartleby.runtime.game import wizard_names
from wizwalker import XYZ

import bartlebot.nodes.node_helpers as nh
from tests.conftest import FakeMachine
from tests.node_helper_patches import patch_helper, set_helper
from tests.port_sim import PortSim

_NAME = "Luke SkullHunter"
_AT = (100.0, 200.0, 0.0)
_BESIDE = (103.0, 200.0, 0.0)
_FAR = (900.0, -400.0, 0.0)


@pytest.fixture(autouse=True)
def _no_dialogs_and_no_names_left_over():
    with patch_helper("resolve_modal", new=AsyncMock(return_value=nh.ModalOutcome(answered=False))):
        yield
    wizard_names.clear_cache()


@pytest.fixture
def fast(monkeypatch):
    set_helper(monkeypatch, "TRIGGER_TIMEOUT", 0.05)
    set_helper(monkeypatch, "PORT_QUIET_GRACE", 0.05)


def _sim() -> PortSim:
    sim = PortSim()
    sim.target = sim.player("Dst", _AT, name=_NAME)
    return sim


# ---------------------------------------------------------------------------
# port_clients
# ---------------------------------------------------------------------------


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

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
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
    set_helper(monkeypatch, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _sim()
    lost = sim.player("Src", _FAR, port="lost")
    machine = FakeMachine()

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert not await nh.gather_at_player(machine, [sim.target, lost], 0, _NAME, poll_interval=0.001)

    assert any("not every player ended up with P1" in msg for msg, _ in machine.logs)


async def test_gather_says_why_a_player_is_not_with_the_target_once_while_it_waits(fast, monkeypatch):
    set_helper(monkeypatch, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _sim()
    lost = sim.player("Src", _FAR, port="lost")
    machine = FakeMachine()

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert not await nh.gather_at_player(machine, [sim.target, lost], 0, _NAME, poll_interval=0.001)

    waits = [msg for msg, _ in machine.logs if msg.startswith("Not yet with P1")]
    assert waits == ["Not yet with P1: P2 is in Else, not Dst"], "said once, not every poll"


async def test_gather_counts_players_with_a_target_walked_off_after_the_port(fast):
    """TP to player leaves the target to the user, who walks it away once the others appear."""
    sim = _sim()
    mover = sim.player("Src", _FAR)
    machine = FakeMachine()

    async def then_walk_off(client, name_lower):
        await sim.teleport(client, name_lower)
        x, y, z = _AT
        sim.target.where = ("Dst", XYZ(x + 900.0, y, z))

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=then_walk_off)):
        assert await nh.gather_at_player(machine, [sim.target, mover], 0, _NAME, poll_interval=0.001)


async def test_gather_gives_up_on_a_loading_screen_that_never_clears(fast, monkeypatch):
    """The wait for loading had no ceiling, and kept TP to player's button disabled for good."""
    set_helper(monkeypatch, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _sim()
    mover = sim.player("Src", _FAR)
    machine = FakeMachine()

    async def stuck(client, name_lower):
        await sim.teleport(client, name_lower)
        client.loading = 10**9

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=stuck)):
        gathered = await asyncio.wait_for(
            nh.gather_at_player(machine, [sim.target, mover], 0, _NAME, poll_interval=0.001), 5
        )

    assert not gathered
    assert any("still on a loading screen" in msg for msg, level in machine.logs if level == logging.WARNING)


async def test_gather_fails_when_a_player_beside_the_target_is_on_another_realm(fast, monkeypatch):
    set_helper(monkeypatch, "ZONE_CHANGE_TIMEOUT", 0.3)
    sim = _sim()
    other_realm = sim.player("Dst", _BESIDE, realm="elsewhere", port="ignored")
    machine = FakeMachine()

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert not await nh.gather_at_player(machine, [sim.target, other_realm], 0, _NAME, poll_interval=0.001)


async def test_gather_records_the_players_it_saw_together(fast):
    sim = _sim()
    mover = sim.player("Src", _FAR)
    machine = FakeMachine()

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert await nh.gather_at_player(machine, [sim.target, mover], 0, _NAME, poll_interval=0.001)

    assert machine.are_together([sim.target, mover])


async def test_gather_fails_without_naming_a_friend_it_cannot_find(fast):
    sim = _sim()
    sim.fail_from_call = 1
    mover = sim.player("Src", _FAR)
    machine = FakeMachine()

    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)):
        assert not await nh.gather_at_player(machine, [sim.target, mover], 0, _NAME, poll_interval=0.001)

    assert not any(_NAME.casefold() in msg.casefold() for msg, _ in machine.logs)


async def test_gather_with_nobody_to_port_is_done():
    sim = _sim()
    with patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)) as tp:
        assert await nh.gather_at_player(FakeMachine(), [sim.target], 0, _NAME)
    tp.assert_not_awaited()
