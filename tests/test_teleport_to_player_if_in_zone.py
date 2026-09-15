"""Unit tests for the teleport_to_player_if_in_zone node."""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.teleportation.teleport_to_player_if_in_zone as tpz
from bartlebot import wizard_names
from bartlebot.nodes.paths import MESSAGE_BOX_RIGHT
from tests.conftest import FakeClient, FakeMachine, message_box_patches
from tests.port_sim import PortSim

_ZONE = "DungeonZone"
_ELSEWHERE = "HubZone"
_TARGET_NAME = "Luke SkullHunter"
_AT_TARGET = (100.0, 200.0, 0.0)
_FAR = (900.0, -400.0, 0.0)

_PARAMS = {"target_zone": _ZONE}


@pytest.fixture(autouse=True)
def _forget_names():
    yield
    wizard_names.clear_cache()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(tpz, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(nh, "TRIGGER_TIMEOUT", 0.05)
    monkeypatch.setattr(nh, "PORT_QUIET_GRACE", 0.05)


def _sim() -> PortSim:
    sim = PortSim()
    sim.target = sim.player(_ZONE, _AT_TARGET, name=_TARGET_NAME)
    return sim


async def _run(sim: PortSim, clients: list, params: dict = _PARAMS) -> tuple[str, FakeMachine]:
    machine = FakeMachine()
    with (
        patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patch.object(nh, "resolve_modal", new=AsyncMock(return_value=nh.ModalOutcome(answered=False))),
    ):
        return await tpz.run(machine, clients, params), machine


# ---------------------------------------------------------------------------
# No target
# ---------------------------------------------------------------------------


async def test_no_client_in_zone_returns_failure():
    """FAILURE when no hooked client is in target_zone; nothing is touched."""
    sim = PortSim()
    clients = [sim.player(_ELSEWHERE), sim.player(_ELSEWHERE)]

    result, _ = await _run(sim, clients)

    assert result == "FAILURE"
    assert sim.ported == []


async def test_empty_target_zone_returns_failure():
    result = await tpz.run(FakeMachine(), [FakeClient()], {"target_zone": ""})
    assert result == "FAILURE"


async def test_target_name_not_cached_returns_failure():
    """FAILURE when the client found in target_zone has no cached wizard name."""
    sim = PortSim()
    unnamed = sim.player(_ZONE, _AT_TARGET)
    mover = sim.player(_ELSEWHERE)

    result, _ = await _run(sim, [unnamed, mover])

    assert result == "FAILURE"
    assert sim.ported == []


# ---------------------------------------------------------------------------
# Who ports, and how the port shows itself
# ---------------------------------------------------------------------------


async def test_single_target_no_other_clients_returns_success():
    sim = _sim()

    result, _ = await _run(sim, [sim.target])

    assert result == "SUCCESS"
    assert sim.ported == []


async def test_a_player_in_another_zone_loads_in():
    sim = _sim()
    mover = sim.player(_ELSEWHERE, _FAR)

    result, _ = await _run(sim, [sim.target, mover])

    assert result == "SUCCESS"
    assert sim.ported == [mover]
    assert mover.where == sim.target.where


async def test_a_player_in_the_same_zone_ports_without_a_loading_screen():
    """#69: the port happened, never loaded, and the node kept re-porting until it gave up."""
    sim = _sim()
    mover = sim.player(_ZONE, _FAR)

    result, _ = await _run(sim, [sim.target, mover])

    assert result == "SUCCESS"
    assert sim.ported == [mover], "ported once, and not again"
    assert mover.where == sim.target.where


async def test_a_player_already_beside_the_target_is_presumed_there_and_logged():
    sim = _sim()
    beside = sim.player(_ZONE, _AT_TARGET)

    result, machine = await _run(sim, [sim.target, beside])

    assert result == "SUCCESS"
    assert any("P2" in msg and "already beside P1" in msg for msg, _ in machine.logs)


async def test_a_player_beside_the_target_on_another_realm_still_ports():
    """The same zone and coordinates on another realm read exactly like standing beside the target."""
    sim = _sim()
    other_realm = sim.player(_ZONE, _AT_TARGET, realm="elsewhere")

    result, _ = await _run(sim, [sim.target, other_realm])

    assert result == "SUCCESS"
    assert sim.times_ported(other_realm) == 1
    assert other_realm.realm == sim.target.realm


async def test_first_in_player_order_is_the_target_and_everyone_else_ports():
    """Being in Target Zone no longer excuses a player: it says nothing about realm or position."""
    sim = _sim()
    also_in_zone = sim.player(_ZONE, _FAR, name="Second Wizard")
    elsewhere = sim.player(_ELSEWHERE, _FAR)

    result, _ = await _run(sim, [sim.target, also_in_zone, elsewhere])

    assert result == "SUCCESS"
    assert sim.times_ported(sim.target) == 0
    assert sim.times_ported(also_in_zone) == 1 and sim.times_ported(elsewhere) == 1


async def test_an_old_bot_file_still_carrying_skip_extra_clients_in_zone_runs():
    sim = _sim()
    mover = sim.player(_ZONE, _FAR)

    result, _ = await _run(sim, [sim.target, mover], {"target_zone": _ZONE, "skip_extra_clients_in_zone": True})

    assert result == "SUCCESS"
    assert sim.ported == [mover]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_a_port_that_lands_somewhere_else_fails(monkeypatch):
    monkeypatch.setattr(nh, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _sim()
    lost = sim.player(_ELSEWHERE, _FAR, port="lost")

    result, _ = await _run(sim, [sim.target, lost])

    assert result == "FAILURE"


async def test_a_port_that_never_shows_itself_fails(monkeypatch):
    """A player in another zone is never presumed there: its port has to load."""
    monkeypatch.setattr(nh, "ZONE_CHANGE_TIMEOUT", 0.3)
    sim = _sim()
    stuck = sim.player(_ELSEWHERE, _FAR, port="ignored")

    result, machine = await _run(sim, [sim.target, stuck])

    assert result == "FAILURE"
    assert any("showed no sign of porting" in msg for msg, _ in machine.logs)


async def test_friend_busy_dialog_returns_failure():
    sim = _sim()
    mover = sim.player(_ELSEWHERE, _FAR, port="ignored")
    patches, clicked = message_box_patches((MESSAGE_BOX_RIGHT,))

    with (
        patch.object(nh, "teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patches[0],
        patches[1],
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="Your friend is busy right now.")),
    ):
        result = await tpz.run(FakeMachine(), [sim.target, mover], _PARAMS)

    assert result == "FAILURE"
    assert clicked == [MESSAGE_BOX_RIGHT]


async def test_friend_not_found_returns_failure_without_naming_them():
    sim = _sim()
    sim.fail_from_call = 1
    mover = sim.player(_ELSEWHERE, _FAR)

    result, machine = await _run(sim, [sim.target, mover])

    assert result == "FAILURE"
    assert not any(_TARGET_NAME.casefold() in msg.casefold() for msg, level in machine.logs if level > 10)
