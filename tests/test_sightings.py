"""Tests for telling whether clients share a zone, realm and instance (#130).

The rules are pure (bartlebot.sightings), read_sightings takes the readings from FakeClients,
BotMachine owns the record, and ClientHealthMonitor's loading watcher is what clears it.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from wizwalker import XYZ

import bartlebot.bot.client_health_monitor as health_module
from bartlebot.bot.client_health_monitor import ClientHealthMonitor
from bartlebot.bot.machine import BotMachine
from bartlebot.nodes import node_helpers
from bartlebot.sightings import SIGHT_RANGE, Reading, Sight, Sightings, TogetherRecord, classify
from tests.conftest import FakeClient, FakeMachine, see_each_other

HERE = XYZ(0.0, 0.0, 0.0)
NEAR = XYZ(SIGHT_RANGE - 1.0, 0.0, 0.0)
FAR = XYZ(SIGHT_RANGE + 1.0, 0.0, 0.0)


def reading(gid, *, sees=(), zone="Z", position=HERE, loading=False, seen_readable=True) -> Reading:
    return Reading(
        gid=gid,
        zone=zone,
        position=position,
        loading=loading,
        seen=frozenset({gid, *sees}) if seen_readable else None,
    )


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_seeing_the_other_is_together():
    assert classify(reading(1, sees={2}), reading(2)) is Sight.TOGETHER


def test_one_direction_is_enough_even_far_apart():
    # The two directions disagree only in the unload band; a sighting is proof either way.
    assert classify(reading(1), reading(2, sees={1}, position=FAR)) is Sight.TOGETHER


def test_close_and_unseen_both_ways_is_apart():
    assert classify(reading(1), reading(2, position=NEAR)) is Sight.APART


def test_far_and_unseen_is_unknown():
    assert classify(reading(1), reading(2, position=FAR)) is Sight.UNKNOWN


def test_different_zones_are_apart_at_any_distance():
    assert classify(reading(1, zone="A"), reading(2, zone="B", position=FAR)) is Sight.APART


def test_anyone_loading_is_unknown_even_when_seen():
    # A leaver stays in the others' lists about a second into its loading screen.
    assert classify(reading(1, sees={2}), reading(2, loading=True)) is Sight.UNKNOWN
    assert classify(reading(1, zone="A", loading=True), reading(2, zone="B")) is Sight.UNKNOWN


def test_an_unreadable_list_cannot_prove_apart():
    assert classify(reading(1, seen_readable=False), reading(2)) is Sight.UNKNOWN


def test_an_unreadable_list_can_still_be_seen_from_the_other_side():
    assert classify(reading(1, seen_readable=False), reading(2, sees={1})) is Sight.TOGETHER


def test_an_unreadable_id_or_position_is_unknown():
    assert classify(reading(None), reading(2)) is Sight.UNKNOWN
    assert classify(reading(1, position=None), reading(2)) is Sight.UNKNOWN


# ---------------------------------------------------------------------------
# Sightings
# ---------------------------------------------------------------------------


def test_groups_are_transitive():
    # 0 sees 1 and 1 sees 2, but 0 and 2 are too far apart to see each other.
    readings = [
        reading(10, sees={11}),
        reading(11, sees={10, 12}, position=NEAR),
        reading(12, sees={11}, position=XYZ(2 * SIGHT_RANGE, 0.0, 0.0)),
    ]
    result = Sightings(["a", "b", "c"], readings)
    assert result.pair(0, 2) is Sight.UNKNOWN
    assert result.groups() == [[0, 1, 2]]
    assert result.all_together()


def test_split_groups_and_apart_pairs():
    readings = [reading(10, sees={11}), reading(11, sees={10}), reading(12), reading(13, sees={12})]
    result = Sightings(["a", "b", "c", "d"], readings)
    assert result.groups() == [[0, 1], [2, 3]]
    assert result.group_of(3) == [2, 3]
    assert not result.all_together()
    assert set(result.apart()) == {(0, 2), (0, 3), (1, 2), (1, 3)}


# ---------------------------------------------------------------------------
# TogetherRecord
# ---------------------------------------------------------------------------


def _check(clients, readings) -> Sightings:
    return Sightings(clients, readings)


def test_record_remembers_a_group_after_it_walks_out_of_sight():
    record = TogetherRecord()
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2, sees={1})]))
    record.note(_check(["a", "b"], [reading(1), reading(2, position=FAR)]))  # unknown now
    assert record.are_together(["a", "b"])


def test_record_merges_groups_seen_in_separate_checks():
    record = TogetherRecord()
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2)]))
    record.note(_check(["b", "c"], [reading(2, sees={3}), reading(3)]))
    assert record.are_together(["a", "b", "c"])


def test_forgetting_a_member_leaves_the_rest_together():
    record = TogetherRecord()
    record.note(_check(["a", "b", "c"], [reading(1, sees={2, 3}), reading(2), reading(3)]))
    record.forget("a")
    assert record.are_together(["b", "c"])
    assert not record.are_together(["a", "b"])


def test_forgetting_one_of_a_pair_dissolves_it():
    record = TogetherRecord()
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2)]))
    record.forget("b")
    assert not record.are_together(["a", "b"])
    assert record.together_with("a", ["a", "b"]) == ["a"]


def test_a_fresh_apart_overrides_the_record():
    record = TogetherRecord()
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2)]))
    record.note(_check(["a", "b"], [reading(1), reading(2, position=NEAR)]))
    assert not record.are_together(["a", "b"])


def test_the_record_follows_the_id_not_the_client_object():
    # A relog hands back a new client object for the same character.
    record = TogetherRecord()
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2)]))
    record.note(_check(["a2"], [reading(1)]))
    assert record.are_together(["a2", "b"])


def test_a_client_no_check_has_read_is_never_together():
    record = TogetherRecord()
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2)]))
    assert not record.are_together(["a", "stranger"])
    assert record.are_together(["a"])


def test_without_retain_only_the_latest_check_counts():
    record = TogetherRecord(retain=False)
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2)]))
    record.note(_check(["a", "b"], [reading(1), reading(2, position=FAR)]))
    assert not record.are_together(["a", "b"])


def test_drop_clients_forgets_their_ids():
    record = TogetherRecord()
    record.note(_check(["a", "b"], [reading(1, sees={2}), reading(2)]))
    record.drop_clients(["a"])
    assert not record.are_together(["a", "b"])


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
