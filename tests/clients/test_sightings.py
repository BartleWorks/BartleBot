"""Tests for telling whether clients share a zone, realm and instance (#130).

The rules are pure (bartleby.runtime.clients.sightings), read_sightings takes the readings from FakeClients,
BotMachine owns the record, and ClientHealthMonitor's loading watcher is what clears it.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from bartleby.runtime.clients.sightings import (
    SIGHT_RANGE,
    Sight,
)
from wizwalker import XYZ

import bartlebot.bot.client_health_monitor as health_module
from bartlebot.bot.client_health_monitor import ClientHealthMonitor
from bartlebot.bot.machine import BotMachine
from bartlebot.nodes import node_helpers
from tests.conftest import FakeClient, FakeGidEntity, FakeMachine, see_each_other

HERE = XYZ(0.0, 0.0, 0.0)
NEAR = XYZ(SIGHT_RANGE - 1.0, 0.0, 0.0)
FAR = XYZ(SIGHT_RANGE + 1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sightings
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TogetherRecord
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# read_sightings
# ---------------------------------------------------------------------------


async def test_fake_clients_in_one_test_see_each_other_by_default():
    clients = [FakeClient(), FakeClient(), FakeClient()]
    machine = FakeMachine()
    result = await node_helpers.read_sightings(clients, machine)
    assert result.all_together()
    assert machine.are_together(clients)


async def test_a_split_group_reads_apart_and_updates_the_record():
    a, b, c = FakeClient(), FakeClient(), FakeClient()
    machine = FakeMachine()
    await node_helpers.read_sightings([a, b, c], machine)
    see_each_other(a, b)
    see_each_other(c)
    result = await node_helpers.read_sightings([a, b, c], machine)
    assert result.groups() == [[0, 1], [2]]
    assert result.pair(0, 2) is Sight.APART
    assert machine.are_together([a, b])
    assert not machine.are_together([a, c])


async def test_a_failing_read_makes_the_pair_unknown_rather_than_raising():
    a, b = FakeClient(), FakeClient()
    see_each_other(a)
    see_each_other(b)
    a.get_base_entity_list.side_effect = RuntimeError("list race")
    b.client_object.global_id_full.side_effect = RuntimeError("unreadable")
    result = await node_helpers.read_sightings([a, b])
    assert result.pair(0, 1) is Sight.UNKNOWN


async def test_a_loading_client_is_unknown():
    a, b = FakeClient(), FakeClient()
    b.is_loading.return_value = True
    result = await node_helpers.read_sightings([a, b])
    assert result.pair(0, 1) is Sight.UNKNOWN


async def test_far_apart_and_unseen_reads_unknown():
    a, b = FakeClient(), FakeClient()
    see_each_other(a)
    see_each_other(b)
    b.body.position = AsyncMock(return_value=FAR)
    result = await node_helpers.read_sightings([a, b])
    assert result.pair(0, 1) is Sight.UNKNOWN


# ---------------------------------------------------------------------------
# count_strangers (#279)
# ---------------------------------------------------------------------------


async def test_fake_clients_in_one_test_are_no_strangers_to_each_other():
    assert await node_helpers.count_strangers([FakeClient(), FakeClient()]) == 0


async def test_a_player_loaded_who_is_not_ours_is_a_stranger():
    a, b = FakeClient(), FakeClient()
    a.get_base_entity_list = AsyncMock(return_value=[FakeGidEntity(a.gid), FakeGidEntity(b.gid), FakeGidEntity(7)])
    assert await node_helpers.count_strangers([a, b]) == 1


async def test_an_npc_is_no_stranger():
    a = FakeClient()
    a.get_base_entity_list = AsyncMock(return_value=[FakeGidEntity(a.gid), FakeGidEntity(7, template="WC-NPC01")])
    assert await node_helpers.count_strangers([a]) == 0


async def test_a_failing_read_counts_nobody_rather_than_raising():
    a, b = FakeClient(), FakeClient()
    a.get_base_entity_list = AsyncMock(return_value=[FakeGidEntity(7)])
    a.client_object.object_name.side_effect = RuntimeError("unreadable")
    b.get_base_entity_list.side_effect = RuntimeError("list race")
    assert await node_helpers.count_strangers([a, b]) == 0


# ---------------------------------------------------------------------------
# BotMachine and the loading watcher
# ---------------------------------------------------------------------------


def _machine(clients) -> BotMachine:
    config = {
        "initial": "s",
        "states": [{"id": "s", "node_type": "mock", "params": {}}],
        "transitions": [{"trigger": "advance", "source": "s", "dest": "s"}],
    }
    return BotMachine(clients=clients, config=config, node_registry={"mock": AsyncMock()})


async def test_a_machine_outside_a_run_keeps_only_the_latest_check():
    a, b = FakeClient(), FakeClient()
    machine = _machine([a, b])
    await node_helpers.read_sightings([a, b], machine)
    assert machine.are_together([a, b])
    b.body.position = AsyncMock(return_value=FAR)
    see_each_other(a)
    see_each_other(b)
    await node_helpers.read_sightings([a, b], machine)
    assert not machine.are_together([a, b])


async def test_forget_sightings_and_set_clients_drop_a_client():
    a, b, c = FakeClient(), FakeClient(), FakeClient()
    machine = _machine([a, b, c])
    await node_helpers.read_sightings([a, b, c], machine)
    machine.forget_sightings(a)
    assert machine.are_together([b, c]) and not machine.are_together([a, b])
    machine.set_clients([b])
    assert not machine.are_together([b, c])
    assert machine.together_with(b, [b, c]) == [b]


async def _run_loading_watcher(monitor: ClientHealthMonitor) -> None:
    with patch.object(health_module, "POLL_INTERVAL", 0.0):
        task = asyncio.create_task(monitor._loading_screen_watcher())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_the_loading_watcher_reports_loading_clients_even_when_flagged():
    # A relog flags its clients, and a relog is exactly when a realm can change.
    loading, flagged_loading, idle, dead = FakeClient(), FakeClient(), FakeClient(), FakeClient()
    loading.is_loading.return_value = True
    flagged_loading.is_loading.return_value = True
    dead.is_loading.side_effect = RuntimeError("gone")
    reported: list = []
    monitor = ClientHealthMonitor(
        clients_ref=lambda: [loading, flagged_loading, idle, dead],
        stop_flag=asyncio.Event(),
        on_pause=lambda: None,
        on_loading=reported.append,
    )
    monitor.flag(flagged_loading.window_handle)
    await _run_loading_watcher(monitor)
    assert {id(c) for c in reported} == {id(loading), id(flagged_loading), id(dead)}


async def test_a_raising_on_loading_does_not_stop_the_watcher():
    client = FakeClient()
    client.is_loading.return_value = True
    calls: list = []

    def boom(c):
        calls.append(c)
        raise RuntimeError("gui")

    monitor = ClientHealthMonitor(
        clients_ref=lambda: [client], stop_flag=asyncio.Event(), on_pause=lambda: None, on_loading=boom
    )
    await _run_loading_watcher(monitor)
    assert len(calls) > 1
