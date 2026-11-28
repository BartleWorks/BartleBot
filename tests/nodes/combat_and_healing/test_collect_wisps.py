"""Unit tests for the collect_wisps node."""

import asyncio
import logging
import time
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import XYZ

import bartlebot.nodes.combat_and_healing.collect_wisps as cw
from bartlebot.nodes import node_helpers
from tests.conftest import FakeClient, FakeEntity, FakeMachine

# Default params: collect if not at full health/mana, fail if below 50%.
_DEFAULT_PARAMS = {
    "if_health_below": 1.0,
    "if_mana_below": 1.0,
    "fail_if_health_below": 0.5,
    "fail_if_mana_below": 0.5,
}

_ids = count(1)


@pytest.fixture(autouse=True)
def _no_delay():
    with (
        patch.object(cw, "WISP_COLLECT_DELAY", 0),
        patch.object(cw, "POLL_INTERVAL", 0.002),
        patch.object(cw, "WISP_TAKE_WAIT", 0),
        patch.object(cw, "WISP_LINGER", 0),
        patch.object(cw, "WISP_LISTED_EVERY", float("inf")),
    ):
        yield


def _entity(x: float, y: float = 0.0) -> FakeEntity:
    """A wisp or mob at (x, y), with an id of its own."""
    entity = FakeEntity()
    entity.location = AsyncMock(return_value=XYZ(x, y, 0.0))
    entity.global_id_full = AsyncMock(return_value=next(_ids))
    return entity


def _client(health: float = 1.0, mana: float = 1.0) -> FakeClient:
    client = FakeClient()
    client.calc_health_ratio.return_value = health
    client.calc_mana_ratio.return_value = mana
    return client


def _refill_on_walk(client: FakeClient, health: float | None = None, mana: float | None = None) -> None:
    """Make a walk raise the client's ratios, as reaching a wisp would."""

    async def _walk(_location):
        if health is not None:
            client.calc_health_ratio.return_value = health
        if mana is not None:
            client.calc_mana_ratio.return_value = mana

    client.walk_to.side_effect = _walk


def _serve_once(mock: AsyncMock, entities: list) -> None:
    """Answer the first scan with `entities` and every later one with nothing."""
    mock.side_effect = [entities] + [[]] * 50


def _walk_targets(client: FakeClient) -> list[tuple[float, float, float]]:
    """Where each walk went, as tuples: XYZ compares by identity, not by value."""
    return [(c.args[0].x, c.args[0].y, c.args[0].z) for c in client.walk_to.call_args_list]


# ---------------------------------------------------------------------------
# Entry: is collection needed at all
# ---------------------------------------------------------------------------


async def test_full_health_mana_moves_nobody():
    """Everyone full → no scan, no teleport, SUCCESS."""
    client = _client()
    client.get_health_wisps.return_value = [_entity(100)]

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    client.get_health_wisps.assert_not_called()
    client.walk_to.assert_not_called()


async def test_zero_collect_threshold_skips_that_resource():
    """if_health_below=0.0 → health wisps are never wanted, however low health is."""
    client = _client(health=0.1)
    client.get_health_wisps.return_value = [_entity(100)]

    params = {**_DEFAULT_PARAMS, "if_health_below": 0.0, "fail_if_health_below": 0.0}
    result = await cw.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.get_health_wisps.assert_not_called()
    client.walk_to.assert_not_called()


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------


async def test_collects_health_wisp_until_threshold():
    """Low health, one safe wisp → teleports to it, stops once full, SUCCESS."""
    client = _client(health=0.6)
    wisp = _entity(300)
    client.get_health_wisps.return_value = [wisp]
    _refill_on_walk(client, health=1.0)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _walk_targets(client) == [(300, 0, 0)]


async def test_only_wanted_kind_is_scanned():
    """Only mana is low → mana wisps are scanned, health wisps are not."""
    client = _client(mana=0.3)
    _serve_once(client.get_mana_wisps, [_entity(200)])
    _refill_on_walk(client, mana=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    client.get_health_wisps.assert_not_called()
    assert _walk_targets(client) == [(200, 0, 0)]


async def test_a_player_short_of_both_goes_for_the_kind_further_below_its_fail_threshold():
    """Health 0.6 is 0.1 over its fail threshold, mana 0.55 only 0.05: the mana wisp first, though
    the health one is nearer."""
    client = _client(health=0.6, mana=0.55)
    _serve_once(client.get_health_wisps, [_entity(400)])
    _serve_once(client.get_mana_wisps, [_entity(900)])

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert _walk_targets(client)[0] == (900, 0, 0)


async def test_a_player_short_of_both_by_as_much_goes_for_health():
    client = _client(health=0.6, mana=0.6)
    _serve_once(client.get_health_wisps, [_entity(900)])
    _serve_once(client.get_mana_wisps, [_entity(400)])

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert _walk_targets(client)[0] == (900, 0, 0)


async def test_a_player_short_of_both_whose_kind_has_no_wisp_goes_for_the_other():
    """Further below on mana, but only a health wisp is in sight: it walks to that, not nowhere."""
    client = _client(health=0.6, mana=0.2)
    client.get_health_wisps.return_value = [_entity(400)]
    _refill_on_walk(client, health=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert _walk_targets(client) == [(400, 0, 0)]


async def test_visited_wisp_is_excluded_from_the_next_scan():
    """Each hop scans afresh, passing the ids already visited so none is picked twice."""
    client = _client(health=0.2)
    first, second = _entity(100), _entity(200)
    client.get_health_wisps.side_effect = [[first], [second], []]

    async def _a_little(_goal):  # each wisp gives a little, so nobody closes in or is full
        client.calc_health_ratio.return_value += 0.1

    client.walk_to.side_effect = _a_little

    params = {**_DEFAULT_PARAMS, "fail_if_health_below": 0.0}
    await cw.run(FakeMachine(), [client], params)

    first_id = await first.global_id_full()
    second_id = await second.global_id_full()
    scanned_with = [c.args[0] for c in client.get_health_wisps.call_args_list]
    # The node keeps adding to one set, so every call shows its final contents.
    assert all(ids == {first_id, second_id} for ids in scanned_with)
    assert client.get_health_wisps.call_count == 3
    assert _walk_targets(client) == [(100, 0, 0), (200, 0, 0)]


async def test_no_wisps_above_fail_threshold_succeeds():
    """Below the collect threshold but no wisps; above the fail threshold → SUCCESS."""
    client = _client(health=0.8)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    client.get_health_wisps.assert_called_once()
    client.walk_to.assert_not_called()


async def test_no_wisps_below_fail_threshold_fails():
    """No wisps, even after the wait, and still below the fail threshold → FAILURE, routing to potions."""
    client = _client(health=0.3)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.walk_to.assert_not_called()


async def test_collected_but_still_below_fail_threshold_fails():
    """A wisp is collected but health stays under the fail threshold → FAILURE."""
    client = _client(health=0.3)
    _serve_once(client.get_health_wisps, [_entity(100)])
    _refill_on_walk(client, health=0.4)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.walk_to.assert_called_once()


async def test_mana_failure_fails():
    """Mana still under its fail threshold → FAILURE."""
    client = _client(mana=0.2)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Waiting for wisps to appear (#283)
# ---------------------------------------------------------------------------


def _patient(seconds: float) -> FakeMachine:
    """A machine whose Safety Delay is `seconds`."""
    return FakeMachine(options={"Safety Delay": seconds})


async def test_wisp_that_appears_late_is_collected():
    """Below the fail threshold and no wisp yet → the node scans again and takes the one that spawns."""
    client = _client(health=0.3)
    wisp = _entity(300)
    client.get_health_wisps.side_effect = [[], [], [wisp]] + [[]] * 50
    _refill_on_walk(client, health=1.0)

    result = await cw.run(_patient(2.0), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _walk_targets(client) == [(300, 0, 0)]


async def test_waits_the_safety_delay_then_fails_when_no_wisp_comes():
    """No wisp ever spawns and a player is below the fail threshold → it scans for the delay, then FAILURE."""
    client = _client(health=0.3)

    result = await cw.run(_patient(0.1), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    assert client.get_health_wisps.call_count > 2
    client.walk_to.assert_not_called()


async def test_below_full_but_above_the_fail_threshold_does_not_wait():
    """Mana at 98% wants a wisp but nobody is below a fail threshold → one scan, then SUCCESS."""
    client = _client(health=0.8, mana=0.98)

    result = await cw.run(_patient(2.0), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    client.get_health_wisps.assert_called_once()
    client.get_mana_wisps.assert_called_once()


async def test_zero_fail_threshold_never_waits():
    """A fail threshold of 0 means that resource never fails, so it never earns a wait."""
    client = _client(health=0.1)

    params = {**_DEFAULT_PARAMS, "fail_if_health_below": 0.0}
    result = await cw.run(_patient(2.0), [client], params)

    assert result == "SUCCESS"
    client.get_health_wisps.assert_called_once()


async def test_a_resource_not_collected_does_not_earn_a_wait():
    """Health is below its fail threshold but health wisps aren't wanted; mana is fine → no wait."""
    client = _client(health=0.1, mana=0.8)

    params = {**_DEFAULT_PARAMS, "if_health_below": 0.0}
    result = await cw.run(_patient(2.0), [client], params)

    assert result == "FAILURE"  # health still fails the post-collection check
    client.get_health_wisps.assert_not_called()
    client.get_mana_wisps.assert_called_once()


async def test_waits_only_before_the_first_hop():
    """Once a wisp has been collected, running out of them ends the node: no second wait."""
    client = _client(health=0.3)
    _serve_once(client.get_health_wisps, [_entity(100)])
    _refill_on_walk(client, health=0.4)  # still below the fail threshold

    result = await cw.run(_patient(0.2), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    assert client.get_health_wisps.call_count == 2


async def test_a_wait_ends_as_soon_as_a_wisp_is_in_sight():
    """The first scan finds a wisp → no polling at all."""
    client = _client(health=0.3)
    client.get_health_wisps.return_value = [_entity(300)]
    _refill_on_walk(client, health=1.0)

    await cw.run(_patient(2.0), [client], _DEFAULT_PARAMS)

    client.get_health_wisps.assert_called_once()
    client.walk_to.assert_called_once()


async def test_a_direct_call_without_fail_thresholds_never_waits():
    """The ambient watcher calls collect_as_group with neither fail thresholds nor a delay."""
    client = _client(health=0.1)

    await cw.collect_as_group(FakeMachine(), [client], 1.0, 1.0, None, True)

    client.get_health_wisps.assert_called_once()


def test_the_node_declares_safety_delay_and_avoid_mobs():
    assert [option.name for option in cw.NODE_SPEC.required_bot_options] == ["Safety Delay", "Avoid Mobs"]


# ---------------------------------------------------------------------------
# Safety radius
# ---------------------------------------------------------------------------


async def test_wisp_near_an_enemy_is_skipped():
    """The nearer wisp is inside the radius of a mob → the farther, safe one is taken."""
    client = _client(health=0.6)
    near, far = _entity(100), _entity(5000)
    client.get_health_wisps.return_value = [near, far]
    client.get_mobs.return_value = [_entity(100 + node_helpers.SAFE_RADIUS - 1)]
    _refill_on_walk(client, health=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert _walk_targets(client) == [(5000, 0, 0)]


async def test_radius_is_wider_than_the_measured_trigger_distance():
    """A mob 886 units from a wisp (the furthest trigger measured in #117) makes it unsafe."""
    assert node_helpers.SAFE_RADIUS > 886


async def test_wisp_exactly_at_the_radius_is_safe():
    client = _client(health=0.6)
    client.get_health_wisps.return_value = [_entity(0)]
    client.get_mobs.return_value = [_entity(node_helpers.SAFE_RADIUS)]
    _refill_on_walk(client, health=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    client.walk_to.assert_called_once()


async def test_no_safe_wisp_moves_nobody():
    """Every wisp is near an enemy → nobody moves, and the fail thresholds decide."""
    client = _client(health=0.3)
    client.get_health_wisps.return_value = [_entity(100), _entity(200)]
    client.get_mobs.return_value = [_entity(150)]

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.walk_to.assert_not_called()


async def test_mobs_are_rescanned_every_hop():
    """A mob that walks up between hops rules out a wisp that was safe on the last scan."""
    client = _client(health=0.2)
    first, second = _entity(0), _entity(10_000)
    client.get_health_wisps.side_effect = [[first, second], [second], [second]]
    client.get_mobs.side_effect = [[], [_entity(10_100)], [_entity(10_100)]]

    params = {**_DEFAULT_PARAMS, "fail_if_health_below": 0.0}
    await cw.run(FakeMachine(), [client], params)

    assert _walk_targets(client) == [(0, 0, 0)] * 2  # and up to it once: the ratio never rose
    assert client.get_mobs.call_count == 2


async def test_only_safe_off_takes_wisps_near_enemies():
    client = _client(health=0.6)
    client.get_health_wisps.return_value = [_entity(100)]
    client.get_mobs.return_value = [_entity(110)]
    _refill_on_walk(client, health=1.0)

    await cw.run(FakeMachine(), [client], {**_DEFAULT_PARAMS, "only_safe": False})

    client.get_mobs.assert_not_called()
    assert _walk_targets(client) == [(100, 0, 0)]


# ---------------------------------------------------------------------------
# Who walks
# ---------------------------------------------------------------------------


async def test_a_full_client_stays_still_while_the_needy_one_walks():
    """Only simultaneous arrivals share a wisp, and walking players never arrive at once; a
    player standing still keeps its ghost window after a fight."""
    needy, full = _client(health=0.6), _client()
    wisp = _entity(300)
    needy.get_health_wisps.return_value = [wisp]
    _refill_on_walk(needy, health=1.0)

    result = await cw.run(FakeMachine(), [needy, full], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _walk_targets(needy) == [(300, 0, 0)]
    assert _walk_targets(full) == []


async def test_every_client_short_of_that_kind_walks_to_the_wisp_at_once():
    low_health, low_mana, full = _client(health=0.6), _client(mana=0.6), _client()
    low_health.get_health_wisps.return_value = [_entity(300)]
    low_health.get_mana_wisps.return_value = []
    _refill_on_walk(low_health, health=1.0)
    second = _client(health=0.7)
    _refill_on_walk(second, health=1.0)

    await cw.collect_as_group(FakeMachine(), [low_health, second, low_mana, full], 1.0, 0.0, None, True)

    assert _walk_targets(low_health) == _walk_targets(second) == [(300, 0, 0)]
    assert _walk_targets(low_mana) == _walk_targets(full) == []


async def test_a_client_that_missed_a_wisp_goes_on_to_the_next():
    """It reached the wisp after the other took it: still short, so the next scan sends it on."""
    first, late = _client(health=0.6), _client(health=0.6)
    near, far = _entity(300), _entity(900)
    first.get_health_wisps.side_effect = lambda visited=frozenset(): [
        w for w in (near, far) if w.global_id_full.return_value not in visited
    ]
    _refill_on_walk(first, health=1.0)  # the first to arrive takes it; the late one gets nothing

    await cw.collect_as_group(FakeMachine(), [first, late], 1.0, 0.0, None, True)

    assert _walk_targets(first) == [(300, 0, 0)]
    # The first's rise says the near wisp was taken, so nobody walks up to it, and the late
    # one is not full; the far one, which its ratio never rises for either, it walks up to once.
    assert _walk_targets(late) == [(300, 0, 0), (900, 0, 0), (900, 0, 0)]


async def test_only_safe_skips_a_wisp_whose_walk_passes_inside_a_patrols_range(monkeypatch):
    from bartlebot.nodes import walking  # noqa: PLC0415

    client = _client(health=0.6)
    behind, open_ = _entity(300), _entity(900)
    client.get_health_wisps.side_effect = [[behind, open_], [open_], []]
    _refill_on_walk(client, health=1.0)
    asked: list = []

    async def clear(_machine, _client, goal):
        asked.append(walking.point(goal))
        return walking.point(goal)[0] != 300  # a patrol walks between the player and the near one

    monkeypatch.setattr(walking, "passes_clear_of_patrols", clear)
    machine = FakeMachine()

    await cw.collect_as_group(machine, [client], 1.0, 0.0, None, True)

    assert _walk_targets(client) == [(900, 0, 0)]
    assert asked == [(300, 0, 0), (900, 0, 0)]
    assert any("passes inside a patrol's range" in m for m, _ in machine.logs)


async def test_with_only_safe_off_no_route_is_checked_for_patrols(monkeypatch):
    from bartlebot.nodes import walking  # noqa: PLC0415

    client = _client(health=0.6)
    client.get_health_wisps.return_value = [_entity(300)]
    _refill_on_walk(client, health=1.0)
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(walking, "passes_clear_of_patrols", check)

    await cw.collect_as_group(FakeMachine(), [client], 1.0, 0.0, None, False)

    check.assert_not_awaited()
    assert _walk_targets(client) == [(300, 0, 0)]


async def test_scan_is_read_from_the_first_client():
    """Every client shares the zone, so one entity list serves the group."""
    lead, other = _client(health=0.6), _client(health=0.6)
    lead.get_health_wisps.return_value = [_entity(300)]
    _refill_on_walk(lead, health=1.0)
    _refill_on_walk(other, health=1.0)

    await cw.run(FakeMachine(), [lead, other], _DEFAULT_PARAMS)

    other.get_health_wisps.assert_not_called()
    other.get_mobs.assert_not_called()


async def test_nearest_wisp_is_measured_from_every_client_that_walks():
    """Two needy clients apart → the wisp with the least total distance wins, not the lead's nearest."""
    lead, other = _client(health=0.6), _client(health=0.6)
    lead.body.position.return_value = XYZ(0, 0, 0)
    other.body.position.return_value = XYZ(1000, 0, 0)
    # Beside the lead but far from the other (total 1200), vs. midway (total 1000).
    lead.get_health_wisps.return_value = [_entity(-100), _entity(500)]
    _refill_on_walk(lead, health=1.0)
    _refill_on_walk(other, health=1.0)

    await cw.run(FakeMachine(), [lead, other], _DEFAULT_PARAMS)

    assert _walk_targets(lead) == [(500, 0, 0)]


async def test_a_full_client_does_not_pull_the_pick_toward_itself():
    lead, full = _client(health=0.6), _client()
    lead.body.position.return_value = XYZ(0, 0, 0)
    full.body.position.return_value = XYZ(1000, 0, 0)
    lead.get_health_wisps.return_value = [_entity(-100), _entity(500)]
    _refill_on_walk(lead, health=1.0)

    await cw.run(FakeMachine(), [lead, full], _DEFAULT_PARAMS)

    assert _walk_targets(lead) == [(-100, 0, 0)]


# ---------------------------------------------------------------------------
# Two groups a round, picked by route seconds (Stage 2 of #340)
# ---------------------------------------------------------------------------


def _route_seconds(monkeypatch, seconds):
    """Cost every walk by ``seconds(client, x)``, the goal's x, instead of planning a route; it
    answers None for a goal that client has no route to. Returns the calls, as (clients, xs)."""
    from bartlebot.nodes import walking  # noqa: PLC0415

    calls: list = []

    async def fake(_machine, clients, goals):
        xs = [walking.point(g)[0] for g in goals]
        calls.append((list(clients), xs))
        return [[seconds(c, x) for x in xs] for c in clients]

    monkeypatch.setattr(walking, "seconds_to", fake)
    return calls


async def test_the_mana_walker_below_its_fail_threshold_goes_with_the_health_walkers_together():
    """Stage 2's Done when: P4 below the mana fail threshold and P1 and P3 short of health; the
    first round sends P4 to a mana wisp and P1 and P3 to a health wisp, both walks at once."""
    p1, p2, p3, p4 = _client(health=0.7), _client(), _client(health=0.8), _client(mana=0.43)
    _serve_once(p1.get_health_wisps, [_entity(300)])
    _serve_once(p1.get_mana_wisps, [_entity(-500)])
    started: list = []
    both = asyncio.Event()

    def walker(client, **ratios):
        async def walk(location):
            started.append((client, location.x))
            if len(started) == 3:
                both.set()
            await asyncio.wait_for(both.wait(), 1.0)  # only walks made together all start
            for name, value in ratios.items():
                getattr(client, f"calc_{name}_ratio").return_value = value

        client.walk_to.side_effect = walk

    walker(p1, health=1.0)
    walker(p3, health=1.0)
    walker(p4, mana=1.0)
    machine = FakeMachine()

    result = await cw.run(machine, [p1, p2, p3, p4], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert sorted((c is p4, x) for c, x in started) == [(False, 300), (False, 300), (True, -500)]
    assert _walk_targets(p2) == []
    hops = [m for m, _ in machine.logs if "hop" in m]
    assert any("health wisp" in m and "for P1, P3" in m for m in hops)
    assert any("mana wisp" in m and "for P4" in m for m in hops)


async def test_the_wisp_its_walkers_reach_soonest_in_all_wins(monkeypatch):
    """By the seconds of each walker's route, summed, not by straight line."""
    near, far = _entity(300), _entity(900)
    lead, other = _client(health=0.6), _client(health=0.6)
    lead.get_health_wisps.return_value = [near, far]
    _refill_on_walk(lead, health=1.0)
    _refill_on_walk(other, health=1.0)
    calls = _route_seconds(monkeypatch, lambda c, x: {300: 12.0, 900: 4.0}[x] + (1.0 if c is other else 0.0))
    machine = FakeMachine()

    await cw.run(machine, [lead, other], _DEFAULT_PARAMS)

    assert _walk_targets(lead) == _walk_targets(other) == [(900, 0, 0)]
    assert calls[0] == ([lead, other], [300, 900])
    assert any("planned 5.0s" in m for m, _ in machine.logs)


async def test_a_wisp_with_no_route_is_dropped_and_not_searched_for_again(monkeypatch):
    unreachable, open_ = _entity(300), _entity(900)
    client = _client(health=0.2)  # never rises
    client.get_health_wisps.side_effect = lambda visited=frozenset(): [
        w for w in (unreachable, open_) if w.global_id_full.return_value not in visited
    ]
    calls = _route_seconds(monkeypatch, lambda _c, x: None if x == 300 else 6.0)
    machine = FakeMachine()

    await cw.collect_as_group(machine, [client], 1.0, 0.0, None, True)

    assert _walk_targets(client) == [(900, 0, 0)] * 2  # and up to it once: the ratio never rose
    assert [xs for _clients, xs in calls] == [[300, 900]]
    assert any("dropping a health wisp" in m and "no route there for P1" in m for m, _ in machine.logs)


async def test_no_wisp_with_a_route_moves_nobody(monkeypatch):
    client = _client(health=0.3)
    client.get_health_wisps.return_value = [_entity(300)]
    _route_seconds(monkeypatch, lambda _c, _x: None)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.walk_to.assert_not_called()


async def test_a_zone_with_no_map_picks_by_straight_line():
    """A FakeClient's zone has no walking map, so the nearest wisp is the pick, as before."""
    client = _client(health=0.6)
    client.get_health_wisps.return_value = [_entity(900), _entity(300)]
    _refill_on_walk(client, health=1.0)
    machine = FakeMachine()

    await cw.run(machine, [client], _DEFAULT_PARAMS)

    assert _walk_targets(client) == [(300, 0, 0)]
    assert any("hop 1" in m and "by straight line" in m for m, _ in machine.logs)


async def test_near_the_timeout_a_group_nobody_in_which_is_failing_stays(monkeypatch):
    """With 10 s left, a 20 s walk is made only for the player below a fail threshold: P1's
    health is short but above its fail threshold, P2's mana below it."""
    p1, p2 = _client(health=0.8), _client(mana=0.3)
    _serve_once(p1.get_health_wisps, [_entity(300)])
    _serve_once(p1.get_mana_wisps, [_entity(900)])
    _refill_on_walk(p2, mana=1.0)
    _route_seconds(monkeypatch, lambda _c, _x: 20.0)
    machine = FakeMachine()

    result = await cw.run(machine, [p1, p2], {**_DEFAULT_PARAMS, "timeout": 10.0})

    assert result == "SUCCESS"
    assert _walk_targets(p1) == []
    assert _walk_targets(p2) == [(900, 0, 0)]
    assert any("not walking P1 to a health wisp" in m for m, _ in machine.logs)


async def test_a_walk_that_fits_the_time_left_is_made_for_anyone(monkeypatch):
    client = _client(health=0.8)
    _serve_once(client.get_health_wisps, [_entity(300)])
    _route_seconds(monkeypatch, lambda _c, _x: 3.0)

    await cw.run(FakeMachine(), [client], {**_DEFAULT_PARAMS, "timeout": 10.0})

    assert _walk_targets(client) == [(300, 0, 0)] * 2  # and up to it once: the ratio never rose


async def test_the_wait_lasts_until_the_failing_players_kind_is_in_sight():
    """A health wisp is in sight at once, but the player below a fail threshold needs mana: the
    first round waits for a mana wisp, then both groups go."""
    short_health, failing_mana = _client(health=0.8), _client(mana=0.3)
    short_health.get_health_wisps.side_effect = [[_entity(300)]] * 3 + [[]] * 50
    short_health.get_mana_wisps.side_effect = [[], [], [_entity(900)]] + [[]] * 50
    _refill_on_walk(short_health, health=1.0)
    _refill_on_walk(failing_mana, mana=1.0)

    result = await cw.run(_patient(2.0), [short_health, failing_mana], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _walk_targets(short_health) == [(300, 0, 0)]
    assert _walk_targets(failing_mana) == [(900, 0, 0)]


async def test_a_wisp_behind_a_wall_loses_to_a_farther_one_with_a_shorter_route(monkeypatch):
    """Planned for real: on a floor split by a gap joined only at its foot, the wisp 1,000 units
    away across the gap is some 3,000 units of walking, and the one 1,400 units away on the
    player's own side wins."""
    import dataclasses  # noqa: PLC0415

    from bartlebot.nodes import walking  # noqa: PLC0415
    from tests.nodes.test_walking import ZONE, split_hall  # noqa: PLC0415

    built = split_hall()

    async def load(_zone):
        return dataclasses.replace(built, passable=built.passable.copy(), closed=[], blocks=[], _took={})

    monkeypatch.setattr(walking, "has_map", lambda zone: zone == ZONE)
    monkeypatch.setattr(walking, "load_planner", load)
    client = _client(health=0.6)
    client.zone_name.return_value = ZONE
    client.body.position.return_value = XYZ(300, 1500, 0)
    across, along = _entity(1300, 1500), _entity(300, 2900)
    client.get_health_wisps.return_value = [across, along]
    _refill_on_walk(client, health=1.0)

    await cw.collect_as_group(FakeMachine(), [client], 1.0, 0.0, None, False)

    assert _walk_targets(client) == [(300, 2900, 0)]


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


async def test_timeout_stops_collection():
    """Health never rises and wisps keep coming, each gone before a full check could see it →
    the timeout ends the loop."""
    client = _client(health=0.1)
    client.get_health_wisps.side_effect = lambda _visited=None: [_entity(100)]

    params = {**_DEFAULT_PARAMS, "timeout": 0.05, "fail_if_health_below": 0.0}
    result = await cw.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    assert client.walk_to.call_count > 0


async def test_zero_timeout_does_not_limit_collection():
    """timeout=0 means no limit; collection still stops once wisps run out."""
    client = _client(health=0.6)
    _serve_once(client.get_health_wisps, [_entity(100)])

    await cw.run(FakeMachine(), [client], {**_DEFAULT_PARAMS, "timeout": 0.0})

    assert _walk_targets(client) == [(100, 0, 0)] * 2  # and up to it once: the ratio never rose


async def test_challenge_mode_reads_the_fail_threshold_against_the_scaled_max():
    """Back from a level 40 Challenge Mode dungeon at 60% of the scaled max: above the 50% fail threshold."""
    client = _client(health=0.6 * 2690 / 13413)
    bot = SimpleNamespace(
        states=[
            SimpleNamespace(
                node_type="enter_dungeon",
                params={"dungeon_zone": "Marleybone/G14_Gauntlet/MB_G14_ThroneRoomB", "offers_challenge_mode": True},
            )
        ]
    )

    result = await cw.run(FakeMachine(bot_config=bot, options={"Challenge Mode": True}), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"


# ---------------------------------------------------------------------------
# Following the wisp, and closing in (Stage 5 of #340)
# ---------------------------------------------------------------------------


def _reaches(monkeypatch, walk_double) -> list:
    """Each walk's reach as walk_clients passed it, None for the default, on the suite's double."""
    from bartlebot.nodes import walking  # noqa: PLC0415

    seen = []

    async def walk(machine, client, number, goal, avoid, *rest, reach=None, **kw):
        seen.append(reach)
        return await walk_double(machine, client, number, goal, avoid, *rest, reach=reach, **kw)

    monkeypatch.setattr(walking, "walk_one", walk)
    return seen


async def test_a_walk_goes_right_up_to_where_the_wisp_is_now(monkeypatch, walk_double):
    """To the wisp itself, at the walker's own arrival distance: a walk that stopped 300 short
    of it often took nothing in game until a second walk closed the gap."""
    reaches = _reaches(monkeypatch, walk_double)
    client = _client(health=0.6)
    wisp = _entity(300)
    wisp.location.side_effect = [XYZ(300, 0, 0)] + [XYZ(650, 40, 0)] * 10  # drifted since the scan
    _serve_once(client.get_health_wisps, [wisp])
    _refill_on_walk(client, health=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert _walk_targets(client) == [(650, 40, 0)]
    assert reaches == [None]  # no reach short of it: walk_clients' default, ARRIVED_WITHIN


async def test_nobody_rising_and_the_wisp_still_there_walks_those_who_arrived_up_to_it_once_more(
    monkeypatch, walk_double
):
    from bartleby.runtime.movement.walk import WalkEnd  # noqa: PLC0415

    reaches = _reaches(monkeypatch, walk_double)
    arrived, blocked = _client(health=0.6), _client(health=0.6)
    blocked.walk_to.return_value = WalkEnd.BLOCKED
    _serve_once(arrived.get_health_wisps, [_entity(300)])
    machine = FakeMachine()

    await cw.collect_as_group(machine, [arrived, blocked], 1.0, 0.0, None, True)

    assert _walk_targets(arrived) == [(300, 0, 0)] * 2
    assert _walk_targets(blocked) == [(300, 0, 0)]  # its walk failed: it never reached the wisp
    assert reaches == [None, None, None]  # every walk, the close-in too, goes right up to the wisp
    assert any("P1 walked to the health wisp" in m and "walking up to it once more" in m for m, _ in machine.logs)


async def test_a_rise_for_anyone_who_walked_means_the_wisp_was_taken_and_nobody_closes_in():
    taker, late = _client(health=0.6), _client(health=0.6)
    _serve_once(taker.get_health_wisps, [_entity(300)])
    _refill_on_walk(taker, health=1.0)

    await cw.collect_as_group(FakeMachine(), [taker, late], 1.0, 0.0, None, True)

    assert _walk_targets(taker) == _walk_targets(late) == [(300, 0, 0)]


async def test_a_wisp_gone_after_the_walk_is_walked_up_to_by_nobody():
    client = _client(health=0.6)
    wisp = _entity(300)
    wisp.location.side_effect = [XYZ(300, 0, 0), XYZ(300, 0, 0), RuntimeError("freed")]
    _serve_once(client.get_health_wisps, [wisp])

    await cw.collect_as_group(FakeMachine(), [client], 1.0, 0.0, None, True)

    assert _walk_targets(client) == [(300, 0, 0)]


def _pick(wisp, kind=cw.HEALTH, wisp_id=None) -> cw._Pick:
    return cw._Pick(
        wisp, wisp_id if wisp_id is not None else wisp.global_id_full.return_value, XYZ(0, 0, 0), kind, [0], None, ""
    )


async def test_a_wisp_goal_reads_where_the_wisp_is_now_each_call():
    wisp = _entity(300)
    goal = cw._WispGoal(_client(), _pick(wisp))

    first = await goal()
    wisp.location.return_value = XYZ(500, 20, 5)

    assert first == (300.0, 0.0, 0.0)
    assert await goal() == (500.0, 20.0, 5.0)


async def test_a_wisp_goal_is_gone_once_the_entity_no_longer_reads_and_stays_gone():
    wisp = _entity(300)
    goal = cw._WispGoal(_client(), _pick(wisp))
    wisp.location.side_effect = RuntimeError("freed")

    assert await goal() is None
    wisp.location.side_effect = None
    assert await goal() is None


async def test_a_wisp_goal_is_gone_once_its_memory_holds_another_entity():
    wisp = _entity(300)
    goal = cw._WispGoal(_client(), _pick(wisp))
    wisp.global_id_full.return_value += 1000

    assert await goal() is None


async def test_a_wisp_goal_is_gone_once_the_client_no_longer_lists_it(monkeypatch):
    monkeypatch.setattr(cw, "WISP_LISTED_EVERY", 0.0)
    lead = _client()
    health, mana = _entity(300), _entity(300)
    lead.get_health_wisps.return_value = [health]
    lead.get_mana_wisps.return_value = []
    listed, unlisted = cw._WispGoal(lead, _pick(health)), cw._WispGoal(lead, _pick(mana, cw.MANA))

    assert await listed() == (300.0, 0.0, 0.0)
    assert await unlisted() is None  # a freed entity reading the numbers it had
    lead.get_health_wisps.return_value = []
    assert await listed() is None


async def test_a_wisp_goal_asks_the_client_for_its_list_no_oftener_than_wisp_listed_every(monkeypatch):
    monkeypatch.setattr(cw, "WISP_LISTED_EVERY", 60.0)
    lead = _client()
    wisp = _entity(300)
    goal = cw._WispGoal(lead, _pick(wisp))

    for _ in range(5):
        assert await goal() is not None

    lead.get_health_wisps.assert_not_called()


# ---------------------------------------------------------------------------
# Full by the game's word, and a timeout that waits for progress (Stage 6 of #340)
# ---------------------------------------------------------------------------


def _staying(client: FakeClient, *wisps) -> None:
    """*wisps* stay in *client*'s list for good; a scan leaves out those it was told were visited."""
    client.get_health_wisps.side_effect = lambda visited=frozenset(): [
        w for w in wisps if w.global_id_full.return_value not in visited
    ]


def _clock(monkeypatch) -> list[float]:
    """A clock for collect_wisps alone, which only the test moves: ``clock[0]`` is now."""
    clock = [0.0]
    monkeypatch.setattr(cw, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


async def test_a_player_whose_ratio_never_rises_beside_a_wisp_that_stays_is_full_after_one():
    """The misread max: health reads 0.3, under the 0.5 fail threshold, and no wisp raises it."""
    client = _client(health=0.3)
    near, far = _entity(300), _entity(900)
    _staying(client, near, far)
    machine = FakeMachine()

    result = await cw.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _walk_targets(client) == [(300, 0, 0)] * 2  # there, and up to it once; never the far one
    assert any(
        "P1 is full of health by the game's word" in m and "stayed 0.30" in m and level == logging.DEBUG
        for m, level in machine.logs
    )
    assert any(
        "P1's health reads 0.30, under the fail threshold 0.5" in m and "counted full" in m and level == logging.INFO
        for m, level in machine.logs
    )


async def test_a_player_full_of_health_still_walks_for_mana():
    client = _client(health=0.3, mana=0.6)
    _staying(client, _entity(300))
    mana_wisp = _entity(-500)
    client.get_mana_wisps.side_effect = lambda visited=frozenset(): [
        w for w in (mana_wisp,) if w.global_id_full.return_value not in visited
    ]

    async def _mana_only(goal):
        if goal.x < 0:
            client.calc_mana_ratio.return_value = 1.0

    client.walk_to.side_effect = _mana_only

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _walk_targets(client) == [(300, 0, 0), (300, 0, 0), (-500, 0, 0)]


async def test_a_player_that_missed_a_wisp_someone_else_took_is_not_full_and_goes_on():
    """Another player took it: it reads as it did, but once the linger is over the client lists
    it no more."""
    client = _client(health=0.3)
    taken, other = _entity(300), _entity(900)
    listed = [taken, other]
    client.get_health_wisps.side_effect = lambda visited=frozenset(): [
        w for w in listed if w.global_id_full.return_value not in visited
    ]

    async def _walk(goal):
        if goal.x == 300 and len(_walk_targets(client)) == 2:  # the close-in: someone else takes it
            listed.remove(taken)
        if goal.x == 900:
            client.calc_health_ratio.return_value = 0.9

    client.walk_to.side_effect = _walk
    machine = FakeMachine()

    result = await cw.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _walk_targets(client) == [(300, 0, 0), (300, 0, 0), (900, 0, 0)]
    assert not any("full of health" in m for m, _ in machine.logs)


async def test_the_wisp_list_is_read_for_a_verdict_only_once_the_linger_is_over(monkeypatch):
    """A taken wisp stays listed 1.3 to 1.8 s, so a read any sooner would see one another took."""
    monkeypatch.setattr(cw, "WISP_LINGER", 0.05)
    client = _client(health=0.3)
    walked, listed_at = [], []

    async def _walk(_goal):
        walked.append(time.monotonic())

    def _list(visited=None):
        if visited is None:
            listed_at.append(time.monotonic())
        return [wisp]

    wisp = _entity(300)
    client.walk_to.side_effect = _walk
    client.get_health_wisps.side_effect = _list

    await cw.collect_as_group(FakeMachine(), [client], 1.0, 0.0, None, True)

    assert len(walked) == 2 and listed_at
    assert listed_at[-1] - walked[-1] >= 0.05


async def test_nobody_is_full_when_anyone_who_walked_rose_during_the_linger():
    late_riser, still = _client(health=0.6), _client(health=0.6)
    _staying(late_riser, _entity(300))
    walks = []

    async def _walk(_goal):
        walks.append(1)
        if len(walks) == 4:  # both closed in; the game's answer comes late for one of them
            late_riser.calc_health_ratio.return_value = 1.0

    late_riser.walk_to.side_effect = still.walk_to.side_effect = _walk

    full = await cw.collect_as_group(FakeMachine(), [late_riser, still], 1.0, 0.0, None, True)

    assert full.of(cw.HEALTH, (1.0, 0.6)) == set()


async def test_a_rise_after_the_verdict_withdraws_it():
    """Full of health at 0.3; then health rises to 0.4 on a mana walk, so the game did give it
    something: no longer full, and under the fail threshold, it fails the node."""
    client = _client(health=0.3, mana=0.6)
    _staying(client, _entity(300))
    mana_wisp = _entity(-500)
    client.get_mana_wisps.side_effect = lambda visited=frozenset(): [
        w for w in (mana_wisp,) if w.global_id_full.return_value not in visited
    ]

    async def _walk(goal):
        if goal.x < 0:
            client.calc_mana_ratio.return_value = 1.0
            client.calc_health_ratio.return_value = 0.4  # a health wisp drifted by

    client.walk_to.side_effect = _walk
    machine = FakeMachine()

    result = await cw.run(machine, [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    assert any("P1's health rose to 0.40: not full after all" in m for m, _ in machine.logs)


def test_full_is_withdrawn_by_a_rise_and_reads_as_full_until_then():
    full = cw._Full()
    full.add(cw.HEALTH, 1, 0.3)

    assert full.of(cw.HEALTH, (0.2, 0.3)) == {1}
    assert full.of(cw.MANA, (0.2, 0.3)) == set()
    assert full.as_read(cw.HEALTH, (0.2, 0.3)) == (0.2, 1.0)

    machine = FakeMachine()
    full.withdraw_risen(machine, {cw.HEALTH: (0.2, 0.35), cw.MANA: (1.0, 1.0)})

    assert full.of(cw.HEALTH, (0.2, 0.3)) == set()


async def test_a_15s_hop_that_raises_a_ratio_with_timeout_10_is_followed_by_the_next(monkeypatch):
    clock = _clock(monkeypatch)
    client = _client(health=0.3)
    _staying(client, _entity(300), _entity(900))

    async def _slow(_goal):
        clock[0] += 15.0
        client.calc_health_ratio.return_value += 0.3

    client.walk_to.side_effect = _slow

    await cw.run(FakeMachine(), [client], {**_DEFAULT_PARAMS, "timeout": 10.0})

    assert _walk_targets(client) == [(300, 0, 0), (900, 0, 0)]


async def test_hops_that_raise_nothing_end_the_node_10s_after_the_last_that_did(monkeypatch):
    """One hop raises, then three raise nothing, each wisp gone by the time the walk ends: the
    clock counts from the hop that raised, so a fourth would start 12 s after it, and does not."""
    clock = _clock(monkeypatch)
    client = _client(health=0.3)

    def _gone_after_its_walk(x):
        wisp = _entity(x)
        wisp.location.side_effect = [XYZ(x, 0, 0), XYZ(x, 0, 0), RuntimeError("freed")]
        return wisp

    client.get_health_wisps.side_effect = [[_gone_after_its_walk(100 * n)] for n in range(1, 10)]

    async def _walk(goal):
        clock[0] += 4.0
        if goal.x == 100:
            client.calc_health_ratio.return_value = 0.6

    client.walk_to.side_effect = _walk
    machine = FakeMachine()

    await cw.run(machine, [client], {**_DEFAULT_PARAMS, "timeout": 10.0})

    assert _walk_targets(client) == [(100, 0, 0), (200, 0, 0), (300, 0, 0), (400, 0, 0)]
    assert any("no wisp has helped anyone for 10.0s" in m for m, _ in machine.logs)


def test_the_timeout_says_it_waits_for_progress():
    (timeout,) = [p for p in cw.PARAM_SCHEMA if p.key == "timeout"]

    assert timeout.help == "The longest to go without a wisp helping anyone. 0 means no limit."
    assert timeout.default == 10.0
