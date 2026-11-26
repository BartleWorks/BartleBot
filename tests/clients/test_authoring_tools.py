"""The authoring utilities (#81): speed, auto-combat, auto-dialogue, TP to player and a walk by hand."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from bartleby.runtime.actions import helpers

from bartlebot.clients import authoring_tools
from bartlebot.clients.authoring_tools import (
    AuthoringAction,
    AuthoringTools,
    shown_actions,
    shown_hotkeys,
    source_index,
)
from bartlebot.game.restricted_zones import Restriction
from bartlebot.settings_manager import DEFAULT_AUTHORING_HOTKEYS
from tests.conftest import FakeClient


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(helpers, "SPEED_REAPPLY_INTERVAL", 0.001)
    monkeypatch.setattr(authoring_tools, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(authoring_tools, "DIALOG_FAST_POLL_INTERVAL", 0.001)


def _speed_field(client: FakeClient, value: int) -> None:
    """Give *client* a speed field that remembers what was written to it."""
    state = {"value": value}
    client.speed_state = state
    client.client_object.speed_multiplier = AsyncMock(side_effect=lambda: state["value"])
    client.client_object.write_speed_multiplier = AsyncMock(side_effect=lambda v: state.update(value=v))
    client.client_object.maybe_read_type_name = AsyncMock(return_value="WizClientObject")


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
    """F1 went with XYZ sync (Stage 7 of #322); the rest kept their keys."""
    assert DEFAULT_AUTHORING_HOTKEYS == {
        "tp_to_focused": "F2",
        "toggle_speed": "F3",
        "toggle_combat": "F4",
        "toggle_dialogue": "F5",
        "toggle_speed_authoring": "F6",
    }


def test_the_source_is_the_focused_window_and_falls_back_to_player_one():
    clients = [FakeClient(), FakeClient(), FakeClient()]
    assert source_index(clients, clients[2].window_handle) == 2
    assert source_index(clients, None) == 0
    assert source_index(clients, -1) == 0


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("speed_enabled")
async def test_speed_boosts_every_player_and_gives_back_their_own_speed():
    clients = [FakeClient(), FakeClient()]
    _speed_field(clients[0], 0)
    _speed_field(clients[1], 40)  # a mount's bonus is the player's own, and comes back
    tools = _tools(clients)

    await tools.set_speed(True)
    await _until(lambda: all(c.speed_state["value"] == 100 for c in clients))
    assert tools.speed_on

    await tools.set_speed(False)
    assert [c.speed_state["value"] for c in clients] == [0, 40]
    assert not tools.speed_on


@pytest.mark.usefixtures("speed_enabled")
async def test_a_released_client_gets_its_speed_back_before_it_is_unhooked():
    client = FakeClient()
    _speed_field(client, 10)
    tools = _tools([client])
    await tools.set_speed(True)
    await _until(lambda: client.speed_state["value"] == 100)

    await tools.release(client)
    assert client.speed_state["value"] == 10
    await tools.set_speed(False)


# ---------------------------------------------------------------------------
# Speed switched off (Stage 0 of #322)
# ---------------------------------------------------------------------------


def test_speed_is_switched_off_until_the_users_go_ahead():
    assert authoring_tools.SPEED_TOGGLE_ENABLED is False


async def test_switched_off_speed_turns_nothing_on_and_says_so(caplog):
    client = FakeClient()
    _speed_field(client, 0)
    tools = _tools([client])
    with caplog.at_level("INFO", logger=authoring_tools.__name__):
        await tools.set_speed(True)
    await asyncio.sleep(0.01)
    assert not tools.speed_on
    client.client_object.write_speed_multiplier.assert_not_awaited()
    assert "Speed is switched off for now" in caplog.text


async def test_switched_off_speed_is_not_written_by_the_action_or_any_lifecycle_step():
    """The button and the hotkey both arrive as ``perform``; a run, an unhook and a quit follow."""
    client = FakeClient()
    _speed_field(client, 0)
    tools = _tools([client])
    await tools.perform(AuthoringAction.TOGGLE_SPEED, None)
    await tools.perform(AuthoringAction.TOGGLE_SPEED, None)
    await tools.suspend_for_run()
    tools.resume_after_run()
    await tools.perform(AuthoringAction.TOGGLE_SPEED, None)
    await tools.release(client)
    await tools.shutdown()
    await asyncio.sleep(0.01)
    assert not tools.speed_on
    client.client_object.write_speed_multiplier.assert_not_awaited()


def test_switched_off_speed_is_left_out_of_the_actions_and_their_hotkeys():
    assert AuthoringAction.TOGGLE_SPEED not in shown_actions()
    assert [a for a in AuthoringAction if a is not AuthoringAction.TOGGLE_SPEED] == list(shown_actions())
    assert shown_hotkeys(DEFAULT_AUTHORING_HOTKEYS) == {
        k: v for k, v in DEFAULT_AUTHORING_HOTKEYS.items() if k != "toggle_speed"
    }


@pytest.mark.usefixtures("speed_enabled")
def test_switched_on_speed_is_one_of_the_actions_and_keeps_its_hotkey():
    assert shown_actions() == tuple(AuthoringAction)
    assert shown_hotkeys(DEFAULT_AUTHORING_HOTKEYS) == DEFAULT_AUTHORING_HOTKEYS


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("speed_enabled")
async def test_a_run_switches_the_toggles_off_and_refuses_them_until_it_ends(monkeypatch):
    monkeypatch.setattr(authoring_tools, "restriction_for", AsyncMock(return_value=None))
    monkeypatch.setattr(authoring_tools.node_helpers, "advance_dialog_once", AsyncMock(return_value=False))
    client = FakeClient()
    _speed_field(client, 0)
    tools = _tools([client])
    await tools.set_speed(True)
    await tools.set_auto_combat(True)
    await tools.set_auto_dialogue(True)

    await tools.suspend_for_run()
    assert not tools.speed_on and not tools.combat_on and not tools.dialogue_on
    await tools.perform(AuthoringAction.TOGGLE_DIALOGUE, None)
    assert not tools.dialogue_on
    await tools.perform(AuthoringAction.TOGGLE_SPEED, None)
    assert await tools.walk_to([client], (1.0, 2.0, 3.0)) is False
    assert not tools.speed_on
    client.walk_to.assert_not_awaited()

    tools.resume_after_run()
    await tools.perform(AuthoringAction.TOGGLE_SPEED, None)
    assert tools.speed_on
    await tools.shutdown()


# ---------------------------------------------------------------------------
# Auto-dialogue
# ---------------------------------------------------------------------------


async def test_auto_dialogue_advances_every_players_dialogue_until_switched_off(monkeypatch):
    """Each player goes through the shared helper, which is what declines a side quest."""
    advanced: list[int] = []

    async def advance(client):
        advanced.append(client.window_handle)
        return False

    monkeypatch.setattr(authoring_tools.node_helpers, "advance_dialog_once", advance)
    clients = [FakeClient(), FakeClient()]
    changes: list[bool] = []
    tools = _tools(clients, on_change=lambda: changes.append(tools.dialogue_on))

    await tools.perform(AuthoringAction.TOGGLE_DIALOGUE, None)
    await _until(lambda: {c.window_handle for c in clients} <= set(advanced))
    await tools.perform(AuthoringAction.TOGGLE_DIALOGUE, None)
    seen = len(advanced)
    await asyncio.sleep(0.01)

    assert len(advanced) == seen
    assert changes == [True, False]


async def test_auto_dialogue_survives_a_client_that_cannot_be_read(monkeypatch):
    good = FakeClient()
    broken = FakeClient()
    advanced: list[int] = []

    async def advance(client):
        if client is broken:
            raise RuntimeError("mid zone change")
        advanced.append(client.window_handle)
        return False

    monkeypatch.setattr(authoring_tools.node_helpers, "advance_dialog_once", advance)
    tools = _tools([broken, good])
    await tools.set_auto_dialogue(True)
    await _until(lambda: len(advanced) >= 3)
    assert tools.dialogue_on
    await tools.shutdown()
    assert not tools.dialogue_on


# ---------------------------------------------------------------------------
# Auto-combat
# ---------------------------------------------------------------------------


async def test_auto_combat_fights_with_each_players_resolved_strategy_once_per_battle(monkeypatch):
    clients = [FakeClient(), FakeClient()]
    clients[1].in_battle = AsyncMock(return_value=True)
    fight_started = asyncio.Event()
    finish = asyncio.Event()

    async def fight(client, dsl, number=None):
        fight_started.set()
        await finish.wait()

    run_combat = AsyncMock(side_effect=fight)
    monkeypatch.setattr(authoring_tools, "_run_combat", run_combat)
    monkeypatch.setattr(authoring_tools, "restriction_for", AsyncMock(return_value=None))
    config = object()
    resolve = MagicMock(return_value=(["p1 dsl", "p2 dsl"], [], "test", None))
    monkeypatch.setattr(authoring_tools, "resolve_combat", resolve)
    tools = _tools(clients, config=config)

    await tools.set_auto_combat(True)
    await asyncio.wait_for(fight_started.wait(), 1)
    await asyncio.sleep(0.01)  # several polls while the fight is still going

    run_combat.assert_awaited_once_with(clients[1], "p2 dsl", number=2)
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

    async def fight(client, dsl, number=None):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(authoring_tools, "_run_combat", fight)
    monkeypatch.setattr(authoring_tools, "restriction_for", AsyncMock(return_value=None))
    monkeypatch.setattr(authoring_tools, "resolve_combat", MagicMock(return_value=(["dsl"], [], "test", None)))
    tools = _tools([client])

    await tools.set_auto_combat(True)
    await _until(lambda: client.window_handle in tools._fights)
    await tools.set_auto_combat(False)
    assert cancelled.is_set()


# ---------------------------------------------------------------------------
# A walk by hand: the go-to buttons (Stage 7 of #322)
# ---------------------------------------------------------------------------


def test_xyz_sync_is_gone():
    """It wrote a position, and nothing but walking.hop does."""
    assert "xyz_sync" not in {a.value for a in AuthoringAction}
    assert not hasattr(AuthoringTools, "xyz_sync")


def test_the_go_to_buttons_find_the_tools_the_app_made():
    tools = _tools([])
    assert authoring_tools.current() is tools


async def test_a_walk_by_hand_walks_every_player_and_writes_no_position():
    clients = [FakeClient(), FakeClient()]

    assert await _tools(clients).walk_to(clients, (10.0, 20.0, 30.0)) is True

    for c in clients:
        assert tuple(c.walk_to.await_args.args[0]) == (10.0, 20.0, 30.0)
        c.teleport.assert_not_awaited()


async def test_a_walk_by_hand_that_does_not_arrive_says_so():
    from bartleby.runtime.movement.walk import WalkEnd  # noqa: PLC0415

    client = FakeClient()
    client.walk_to.return_value = WalkEnd.NO_ROUTE

    assert await _tools([client]).walk_to([client], (1.0, 2.0, 3.0)) is False


async def test_a_walk_by_hand_takes_the_open_bots_avoid_mobs(monkeypatch):
    from bartlebot.nodes import walking  # noqa: PLC0415

    seen = []

    async def walk(machine, client, number, goal, avoid, *args, **kwargs):
        seen.append(avoid)
        return walking.Walked(walking._not_walked(walking.WalkEnd.ARRIVED, "Z", (0, 0, 0), goal, ""))

    monkeypatch.setattr(walking, "walk_one", walk)
    monkeypatch.setattr(authoring_tools.walking, "walk_one", walk)
    config = MagicMock()
    monkeypatch.setattr("bartlebot.options.option_values.option_value", lambda cfg, name, default=None: False)
    client = FakeClient()

    await _tools([client], config=config).walk_to([client], (1.0, 2.0, 3.0))

    assert seen == [False]


async def test_a_walk_by_hand_with_no_bot_option_takes_the_app_setting(monkeypatch):
    """A bot without the option, or none open, falls to the user's own Avoid Mobs."""
    from bartlebot.nodes import walking  # noqa: PLC0415
    from bartlebot.options import option_values  # noqa: PLC0415

    seen = []

    async def walk(machine, client, number, goal, avoid, *args, **kwargs):
        seen.append(avoid)
        return walking.Walked(walking._not_walked(walking.WalkEnd.ARRIVED, "Z", (0, 0, 0), goal, ""))

    monkeypatch.setattr(walking, "walk_one", walk)
    monkeypatch.setattr(option_values, "option_value", lambda cfg, name, default=None: default)
    monkeypatch.setattr(option_values, "app_value", lambda name: False)
    client = FakeClient()

    await _tools([client], config=MagicMock()).walk_to([client], (1.0, 2.0, 3.0))
    await _tools([client]).walk_to([client], (1.0, 2.0, 3.0))

    assert seen == [False, False]


async def test_a_second_action_while_one_is_in_flight_is_ignored(monkeypatch):
    clients = [FakeClient(), FakeClient()]
    monkeypatch.setattr(authoring_tools.wizard_names, "cached_name", lambda c: "Someone")
    release = asyncio.Event()

    async def slow_gather(*_args, **_kwargs):
        await release.wait()
        return True

    gather = AsyncMock(side_effect=slow_gather)
    monkeypatch.setattr(authoring_tools.node_helpers, "gather_at_player", gather)
    tools = _tools(clients)

    first = asyncio.ensure_future(tools.tp_to_player(clients[0].window_handle))
    await _until(lambda: gather.await_count == 1)
    assert tools.busy
    await tools.tp_to_player(clients[0].window_handle)
    release.set()
    await first

    assert gather.await_count == 1
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


@pytest.mark.usefixtures("speed_enabled")
async def test_a_failing_on_change_does_not_stop_a_toggle():
    client = FakeClient()
    _speed_field(client, 0)

    def boom():
        raise RuntimeError("gui")

    tools = _tools([client], on_change=boom)
    await tools.set_speed(True)
    await _until(lambda: client.speed_state["value"] == 100)
    await tools.set_speed(False)
    assert client.speed_state["value"] == 0
