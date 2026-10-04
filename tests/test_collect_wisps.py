"""Unit tests for the collect_wisps node."""

from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import XYZ

import bartlebot.nodes.combat.collect_wisps as cw
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
    with patch.object(cw, "WISP_COLLECT_DELAY", 0):
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


def _refill_on_teleport(client: FakeClient, health: float | None = None, mana: float | None = None) -> None:
    """Make a teleport raise the client's ratios, as landing on a wisp would."""

    async def _teleport(_location):
        if health is not None:
            client.calc_health_ratio.return_value = health
        if mana is not None:
            client.calc_mana_ratio.return_value = mana

    client.teleport.side_effect = _teleport


def _serve_once(mock: AsyncMock, entities: list) -> None:
    """Answer the first scan with `entities` and every later one with nothing."""
    mock.side_effect = [entities] + [[]] * 50


def _teleport_targets(client: FakeClient) -> list[tuple[float, float, float]]:
    """Where each teleport went, as tuples: XYZ compares by identity, not by value."""
    return [(c.args[0].x, c.args[0].y, c.args[0].z) for c in client.teleport.call_args_list]


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
    client.teleport.assert_not_called()


async def test_zero_collect_threshold_skips_that_resource():
    """if_health_below=0.0 → health wisps are never wanted, however low health is."""
    client = _client(health=0.1)
    client.get_health_wisps.return_value = [_entity(100)]

    params = {**_DEFAULT_PARAMS, "if_health_below": 0.0, "fail_if_health_below": 0.0}
    result = await cw.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.get_health_wisps.assert_not_called()
    client.teleport.assert_not_called()


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------


async def test_collects_health_wisp_until_threshold():
    """Low health, one safe wisp → teleports to it, stops once full, SUCCESS."""
    client = _client(health=0.6)
    wisp = _entity(300)
    client.get_health_wisps.return_value = [wisp]
    _refill_on_teleport(client, health=1.0)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _teleport_targets(client) == [(300, 0, 0)]


async def test_only_wanted_kind_is_scanned():
    """Only mana is low → mana wisps are scanned, health wisps are not."""
    client = _client(mana=0.3)
    _serve_once(client.get_mana_wisps, [_entity(200)])
    _refill_on_teleport(client, mana=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    client.get_health_wisps.assert_not_called()
    assert _teleport_targets(client) == [(200, 0, 0)]


async def test_health_and_mana_candidates_are_merged():
    """Both wanted → the nearest wisp of either kind is taken first."""
    client = _client(health=0.6, mana=0.6)
    _serve_once(client.get_health_wisps, [_entity(900)])
    _serve_once(client.get_mana_wisps, [_entity(400)])

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert _teleport_targets(client)[0] == (400, 0, 0)


async def test_visited_wisp_is_excluded_from_the_next_scan():
    """Each hop scans afresh, passing the ids already visited so none is picked twice."""
    client = _client(health=0.2)  # never rises
    first, second = _entity(100), _entity(200)
    client.get_health_wisps.side_effect = [[first], [second], []]

    params = {**_DEFAULT_PARAMS, "fail_if_health_below": 0.0}
    await cw.run(FakeMachine(), [client], params)

    first_id = await first.global_id_full()
    second_id = await second.global_id_full()
    scanned_with = [c.args[0] for c in client.get_health_wisps.call_args_list]
    # The node keeps adding to one set, so every call shows its final contents.
    assert all(ids == {first_id, second_id} for ids in scanned_with)
    assert client.get_health_wisps.call_count == 3
    assert _teleport_targets(client) == [(100, 0, 0), (200, 0, 0)]


async def test_no_wisps_above_fail_threshold_succeeds():
    """Below the collect threshold but no wisps; above the fail threshold → SUCCESS."""
    client = _client(health=0.8)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    client.get_health_wisps.assert_called_once()
    client.teleport.assert_not_called()


async def test_no_wisps_below_fail_threshold_fails():
    """No wisps and still below the fail threshold → FAILURE, routing to potions."""
    client = _client(health=0.3)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_collected_but_still_below_fail_threshold_fails():
    """A wisp is collected but health stays under the fail threshold → FAILURE."""
    client = _client(health=0.3)
    _serve_once(client.get_health_wisps, [_entity(100)])
    _refill_on_teleport(client, health=0.4)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_called_once()


async def test_mana_failure_fails():
    """Mana still under its fail threshold → FAILURE."""
    client = _client(mana=0.2)

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Safety radius
# ---------------------------------------------------------------------------


async def test_wisp_near_an_enemy_is_skipped():
    """The nearer wisp is inside the radius of a mob → the farther, safe one is taken."""
    client = _client(health=0.6)
    near, far = _entity(100), _entity(5000)
    client.get_health_wisps.return_value = [near, far]
    client.get_mobs.return_value = [_entity(100 + node_helpers.SAFE_RADIUS - 1)]
    _refill_on_teleport(client, health=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert _teleport_targets(client) == [(5000, 0, 0)]


async def test_radius_is_wider_than_the_measured_trigger_distance():
    """A mob 886 units from a wisp (the furthest trigger measured in #117) makes it unsafe."""
    assert node_helpers.SAFE_RADIUS > 886


async def test_wisp_exactly_at_the_radius_is_safe():
    client = _client(health=0.6)
    client.get_health_wisps.return_value = [_entity(0)]
    client.get_mobs.return_value = [_entity(node_helpers.SAFE_RADIUS)]
    _refill_on_teleport(client, health=1.0)

    await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    client.teleport.assert_called_once()


async def test_no_safe_wisp_moves_nobody():
    """Every wisp is near an enemy → nobody moves, and the fail thresholds decide."""
    client = _client(health=0.3)
    client.get_health_wisps.return_value = [_entity(100), _entity(200)]
    client.get_mobs.return_value = [_entity(150)]

    result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_mobs_are_rescanned_every_hop():
    """A mob that walks up between hops rules out a wisp that was safe on the last scan."""
    client = _client(health=0.2)
    first, second = _entity(0), _entity(10_000)
    client.get_health_wisps.side_effect = [[first, second], [second], [second]]
    client.get_mobs.side_effect = [[], [_entity(10_100)], [_entity(10_100)]]

    params = {**_DEFAULT_PARAMS, "fail_if_health_below": 0.0}
    await cw.run(FakeMachine(), [client], params)

    assert _teleport_targets(client) == [(0, 0, 0)]
    assert client.get_mobs.call_count == 2


async def test_only_safe_off_takes_wisps_near_enemies():
    client = _client(health=0.6)
    client.get_health_wisps.return_value = [_entity(100)]
    client.get_mobs.return_value = [_entity(110)]
    _refill_on_teleport(client, health=1.0)

    await cw.run(FakeMachine(), [client], {**_DEFAULT_PARAMS, "only_safe": False})

    client.get_mobs.assert_not_called()
    assert _teleport_targets(client) == [(100, 0, 0)]


# ---------------------------------------------------------------------------
# Moving as a group
# ---------------------------------------------------------------------------


async def test_full_client_moves_with_the_group():
    """One client needs health, the other is full → both land on the same wisp."""
    needy, full = _client(health=0.6), _client()
    wisp = _entity(300)
    needy.get_health_wisps.return_value = [wisp]
    _refill_on_teleport(needy, health=1.0)

    result = await cw.run(FakeMachine(), [needy, full], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert _teleport_targets(needy) == _teleport_targets(full) == [(300, 0, 0)]


async def test_scan_is_read_from_the_first_client():
    """Every client shares the zone, so one entity list serves the group."""
    lead, other = _client(health=0.6), _client(health=0.6)
    lead.get_health_wisps.return_value = [_entity(300)]
    _refill_on_teleport(lead, health=1.0)
    _refill_on_teleport(other, health=1.0)

    await cw.run(FakeMachine(), [lead, other], _DEFAULT_PARAMS)

    other.get_health_wisps.assert_not_called()
    other.get_mobs.assert_not_called()


async def test_nearest_wisp_is_measured_from_the_whole_group():
    """Two clients apart → the wisp with the least total distance wins, not the lead's nearest."""
    lead, other = _client(health=0.6), _client()
    lead.body.position.return_value = XYZ(0, 0, 0)
    other.body.position.return_value = XYZ(1000, 0, 0)
    # Beside the lead but far from the other (total 1200), vs. midway (total 1000).
    lead.get_health_wisps.return_value = [_entity(-100), _entity(500)]
    _refill_on_teleport(lead, health=1.0)

    await cw.run(FakeMachine(), [lead, other], _DEFAULT_PARAMS)

    assert _teleport_targets(lead) == [(500, 0, 0)]


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


async def test_timeout_stops_collection():
    """Health never rises and wisps keep appearing → the timeout ends the loop."""
    client = _client(health=0.1)
    client.get_health_wisps.side_effect = lambda _visited: [_entity(100)]

    params = {**_DEFAULT_PARAMS, "timeout": 0.05, "fail_if_health_below": 0.0}
    result = await cw.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    assert client.teleport.call_count > 0


async def test_zero_timeout_does_not_limit_collection():
    """timeout=0 means no limit; collection still stops once wisps run out."""
    client = _client(health=0.6)
    _serve_once(client.get_health_wisps, [_entity(100)])

    await cw.run(FakeMachine(), [client], {**_DEFAULT_PARAMS, "timeout": 0.0})

    client.teleport.assert_called_once()


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
