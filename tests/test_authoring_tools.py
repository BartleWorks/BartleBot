"""The authoring utilities (#81): speed, auto-combat, XYZ sync and TP to player."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from wizwalker import XYZ

from bartlebot import authoring_tools
from bartlebot.authoring_tools import (
    AuthoringAction,
    AuthoringTools,
    boosted_speed_value,
    source_index,
)
from bartlebot.restricted_zones import Restriction
from bartlebot.settings_manager import DEFAULT_AUTHORING_HOTKEYS
from tests.conftest import FakeClient


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(authoring_tools, "SPEED_REAPPLY_INTERVAL", 0.001)
    monkeypatch.setattr(authoring_tools, "POLL_INTERVAL", 0.001)


def _speed_field(client: FakeClient, value: int) -> None:
    """Give *client* a speed field that remembers what was written to it."""
    state = {"value": value}
    client.speed_state = state
    client.client_object.speed_multiplier = AsyncMock(side_effect=lambda: state["value"])
    client.client_object.write_speed_multiplier = AsyncMock(side_effect=lambda v: state.update(value=v))


def _tools(clients: list, *, multiplier: float = 5.0, config=None, on_change=None) -> AuthoringTools:
    return AuthoringTools(
        clients=lambda: clients, bot_config=lambda: config, speed_multiplier=lambda: multiplier, on_change=on_change
    )


async def _until(predicate, ticks: int = 500) -> None:
    for _ in range(ticks):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition never became true")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_every_action_has_a_default_hotkey_and_the_ids_agree():
    assert set(DEFAULT_AUTHORING_HOTKEYS) == {a.value for a in AuthoringAction}


def test_the_default_hotkeys_are_the_f_keys_in_order():
    assert DEFAULT_AUTHORING_HOTKEYS == {
        "xyz_sync": "F1",
        "tp_to_focused": "F2",
        "toggle_speed": "F3",
        "toggle_combat": "F4",
        "toggle_speed_authoring": "F5",
    }


@pytest.mark.parametrize(
    ("multiplier", "value"),
    [(1.0, 0), (2.0, 100), (5.0, 400), (0.5, 0), (1000.0, 0x7FFF)],
)
def test_the_speed_field_is_a_percentage_bonus(multiplier, value):
    assert boosted_speed_value(multiplier) == value


def test_the_source_is_the_focused_window_and_falls_back_to_player_one():
    clients = [FakeClient(), FakeClient(), FakeClient()]
    assert source_index(clients, clients[2].window_handle) == 2
    assert source_index(clients, None) == 0
    assert source_index(clients, -1) == 0


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------


async def test_speed_boosts_every_player_and_gives_back_their_own_speed():
    clients = [FakeClient(), FakeClient()]
    _speed_field(clients[0], 0)
    _speed_field(clients[1], 40)  # a mount's bonus is the player's own, and comes back
    tools = _tools(clients)

    await tools.set_speed(True)
    await _until(lambda: all(c.speed_state["value"] == 400 for c in clients))
    assert tools.speed_on

    await tools.set_speed(False)
    assert [c.speed_state["value"] for c in clients] == [0, 40]
    assert not tools.speed_on


async def test_speed_comes_back_after_the_game_rewrites_it_and_restores_the_new_value():
    client = FakeClient()
    _speed_field(client, 0)
    tools = _tools([client])
    await tools.set_speed(True)
    await _until(lambda: client.speed_state["value"] == 400)

    # A zone change: the game writes the player's real speed again, now with a mount.
    client.speed_state["value"] = 40
    await _until(lambda: client.speed_state["value"] == 400)

    await tools.set_speed(False)
    assert client.speed_state["value"] == 40


async def test_speed_off_leaves_a_value_the_game_wrote_since():
    client = FakeClient()
    _speed_field(client, 0)
    tools = _tools([client])
    await tools.set_speed(True)
    await _until(lambda: client.speed_state["value"] == 400)
    task = tools._speed_task
    task.cancel()  # freeze the loop so the game's write below is not overwritten
    await asyncio.sleep(0.005)
    client.speed_state["value"] = 25

    await tools.set_speed(False)
    assert client.speed_state["value"] == 25


async def test_speed_skips_a_player_on_a_loading_screen():
    client = FakeClient()
    _speed_field(client, 0)
    client.is_loading = AsyncMock(return_value=True)
    tools = _tools([client])
    await tools.set_speed(True)
    await asyncio.sleep(0.01)
    client.client_object.write_speed_multiplier.assert_not_awaited()
    await tools.set_speed(False)


async def test_a_released_client_gets_its_speed_back_before_it_is_unhooked():
    client = FakeClient()
    _speed_field(client, 10)
    tools = _tools([client])
    await tools.set_speed(True)
    await _until(lambda: client.speed_state["value"] == 400)

    await tools.release(client)
    assert client.speed_state["value"] == 10
    await tools.set_speed(False)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def test_a_run_switches_both_toggles_off_and_refuses_them_until_it_ends(monkeypatch):
    monkeypatch.setattr(authoring_tools, "restriction_for", AsyncMock(return_value=None))
    client = FakeClient()
    _speed_field(client, 0)
    tools = _tools([client])
    await tools.set_speed(True)
    await tools.set_auto_combat(True)

    await tools.suspend_for_run()
    assert not tools.speed_on and not tools.combat_on
    await tools.perform(AuthoringAction.TOGGLE_SPEED, None)
    await tools.perform(AuthoringAction.XYZ_SYNC, None)
    assert not tools.speed_on
    client.teleport.assert_not_awaited()

    tools.resume_after_run()
    await tools.perform(AuthoringAction.TOGGLE_SPEED, None)
    assert tools.speed_on
    await tools.shutdown()


# ---------------------------------------------------------------------------
# Auto-combat
# ---------------------------------------------------------------------------


async def test_auto_combat_fights_with_each_players_resolved_strategy_once_per_battle(monkeypatch):
    clients = [FakeClient(), FakeClient()]
    clients[1].in_battle = AsyncMock(return_value=True)
    fight_started = asyncio.Event()
    finish = asyncio.Event()

    async def fight(client, dsl):
        fight_started.set()
        await finish.wait()

    run_combat = AsyncMock(side_effect=fight)
    monkeypatch.setattr(authoring_tools, "_run_combat", run_combat)
    monkeypatch.setattr(authoring_tools, "restriction_for", AsyncMock(return_value=None))
    config = object()
    resolve = MagicMock(return_value=(["p1 dsl", "p2 dsl"], [], "test"))
    monkeypatch.setattr(authoring_tools, "resolve_combat", resolve)
    tools = _tools(clients, config=config)

    await tools.set_auto_combat(True)
    await asyncio.wait_for(fight_started.wait(), 1)
    await asyncio.sleep(0.01)  # several polls while the fight is still going

    run_combat.assert_awaited_once_with(clients[1], "p2 dsl")
    stand_in, use_default, count = resolve.call_args.args
    assert stand_in.bot_config is config and use_default is True and count == 2
    finish.set()
    await tools.set_auto_combat(False)


async def test_auto_combat_refuses_pvp_and_switches_itself_off(monkeypatch):
    client = FakeClient()
    client.in_battle = AsyncMock(return_value=True)
    run_combat = AsyncMock()
    monkeypatch.setattr(authoring_tools, "_run_combat", run_combat)
    hit = MagicMock(spec=Restriction)
    hit.describe.return_value = "a PvP duel"
    monkeypatch.setattr(authoring_tools, "restriction_for", AsyncMock(return_value=hit))
    changes = []
    tools = _tools([client], on_change=lambda: changes.append(tools.combat_on))

    await tools.set_auto_combat(True)
    await _until(lambda: not tools.combat_on)

    run_combat.assert_not_awaited()
    assert changes[-1] is False


async def test_switching_auto_combat_off_stops_a_fight_in_progress(monkeypatch):
    client = FakeClient()
    client.in_battle = AsyncMock(return_value=True)
    cancelled = asyncio.Event()

    async def fight(client, dsl):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(authoring_tools, "_run_combat", fight)
    monkeypatch.setattr(authoring_tools, "restriction_for", AsyncMock(return_value=None))
    monkeypatch.setattr(authoring_tools, "resolve_combat", MagicMock(return_value=(["dsl"], [], "test")))
    tools = _tools([client])

    await tools.set_auto_combat(True)
    await _until(lambda: client.window_handle in tools._fights)
    await tools.set_auto_combat(False)
    assert cancelled.is_set()


# ---------------------------------------------------------------------------
# XYZ sync
# ---------------------------------------------------------------------------


def _placed(client: FakeClient, zone: str, xyz: XYZ, yaw: float = 0.0) -> FakeClient:
    client.zone_name = AsyncMock(return_value=zone)
    client.body.position = AsyncMock(return_value=xyz)
    client.body.yaw = AsyncMock(return_value=yaw)
    return client


async def test_xyz_sync_moves_the_others_in_the_zone_onto_the_focused_player():
    here = XYZ(1.0, 2.0, 3.0)
    p1 = _placed(FakeClient(), "Zone/A", XYZ(0.0, 0.0, 0.0))
    p2 = _placed(FakeClient(), "Zone/A", here, yaw=1.5)
    p3 = _placed(FakeClient(), "Zone/B", XYZ(9.0, 9.0, 9.0))
    tools = _tools([p1, p2, p3])

    await tools.perform(AuthoringAction.XYZ_SYNC, p2.window_handle)

    p1.teleport.assert_awaited_once()
    assert p1.teleport.await_args.args == (here, 1.5)
    p2.teleport.assert_not_awaited()
    p3.teleport.assert_not_awaited()  # a position in another zone means nothing


async def test_xyz_sync_with_one_player_moves_nobody():
    p1 = _placed(FakeClient(), "Zone/A", XYZ(0.0, 0.0, 0.0))
    await _tools([p1]).xyz_sync(None)
    p1.teleport.assert_not_awaited()


async def test_a_second_action_while_one_is_in_flight_is_ignored():
    p1 = _placed(FakeClient(), "Zone/A", XYZ(0.0, 0.0, 0.0))
    p2 = _placed(FakeClient(), "Zone/A", XYZ(1.0, 0.0, 0.0))
    release = asyncio.Event()

    async def slow_teleport(*_args, **_kwargs):
        await release.wait()

    p2.teleport = AsyncMock(side_effect=slow_teleport)
    tools = _tools([p1, p2])

    first = asyncio.ensure_future(tools.xyz_sync(p1.window_handle))
    await _until(lambda: p2.teleport.await_count == 1)
    assert tools.busy
    await tools.xyz_sync(p1.window_handle)
    release.set()
    await first

    assert p2.teleport.await_count == 1
    assert not tools.busy


# ---------------------------------------------------------------------------
# TP to player
# ---------------------------------------------------------------------------


async def test_tp_ports_everyone_to_the_focused_player_by_its_detected_name(monkeypatch):
    clients = [FakeClient(), FakeClient()]
    monkeypatch.setattr(authoring_tools.wizard_names, "cached_name", lambda c: "Luke SkullHunter")
    gather = AsyncMock(return_value=True)
    monkeypatch.setattr(authoring_tools.node_helpers, "gather_at_player", gather)

    await _tools(clients).perform(AuthoringAction.TP_TO_FOCUSED, clients[1].window_handle)

    _log, passed_clients, index, name = gather.await_args.args
    assert passed_clients == clients and index == 1 and name == "Luke SkullHunter"


async def test_tp_does_nothing_until_the_players_name_is_known(monkeypatch):
    clients = [FakeClient(), FakeClient()]
    monkeypatch.setattr(authoring_tools.wizard_names, "cached_name", lambda c: None)
    gather = AsyncMock(return_value=True)
    monkeypatch.setattr(authoring_tools.node_helpers, "gather_at_player", gather)

    await _tools(clients).tp_to_player(None)
    gather.assert_not_awaited()


async def test_a_failing_on_change_does_not_stop_a_toggle():
    client = FakeClient()
    _speed_field(client, 0)

    def boom():
        raise RuntimeError("gui")

    tools = _tools([client], on_change=boom)
    await tools.set_speed(True)
    await _until(lambda: client.speed_state["value"] == 400)
    await tools.set_speed(False)
    assert client.speed_state["value"] == 0
