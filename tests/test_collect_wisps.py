"""Unit tests for the collect_wisps node."""

from unittest.mock import patch

import bartlebot.nodes.combat.collect_wisps as cw
from tests.conftest import FakeClient, FakeMachine

# Default params: collect if not at full health/mana, fail if below 50%.
_DEFAULT_PARAMS = {
    "if_health_below": 1.0,
    "if_mana_below": 1.0,
    "fail_if_health_below": 0.5,
    "fail_if_mana_below": 0.5,
}


async def test_full_health_mana_skips_collection():
    """Both ratios at 1.0 → no wisps collected, returns SUCCESS."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 1.0
    client.calc_mana_ratio.return_value = 1.0

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    client.tp_to_closest_health_wisp.assert_not_called()
    client.tp_to_closest_mana_wisp.assert_not_called()


async def test_collects_health_wisps_until_threshold():
    """Health starts low; wisps available → collects until above threshold, SUCCESS."""
    client = FakeClient()
    # Collection loop calls: 0.5 (enter), 0.8 (enter), 1.0 (exit).
    # _get_ratios post-collection calls once more → supply a 4th value.
    client.calc_health_ratio.side_effect = [0.5, 0.8, 1.0, 1.0]
    client.calc_mana_ratio.return_value = 1.0
    client.tp_to_closest_health_wisp.return_value = True

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert client.tp_to_closest_health_wisp.call_count == 2


async def test_collects_mana_wisps_until_threshold():
    """Mana starts low; wisps available → collects until above threshold, SUCCESS."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 1.0
    # Collection loop calls: 0.3, 0.7, 1.0. _get_ratios calls once more.
    client.calc_mana_ratio.side_effect = [0.3, 0.7, 1.0, 1.0]
    client.tp_to_closest_mana_wisp.return_value = True

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "SUCCESS"
    assert client.tp_to_closest_mana_wisp.call_count == 2


async def test_no_wisps_above_fail_threshold_succeeds():
    """Below collect threshold but no wisps; final ratio above fail threshold → SUCCESS."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.8  # < 1.0 collect threshold
    client.calc_mana_ratio.return_value = 1.0
    client.tp_to_closest_health_wisp.return_value = False  # no wisps

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)  # fail threshold = 0.5

    assert result == "SUCCESS"
    client.tp_to_closest_health_wisp.assert_called_once()


async def test_no_wisps_below_fail_threshold_fails():
    """Below collect threshold; no wisps; final ratio below fail threshold → FAILURE."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.3  # < both collect (1.0) and fail (0.5) thresholds
    client.calc_mana_ratio.return_value = 1.0
    client.tp_to_closest_health_wisp.return_value = False  # no wisps

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    client.tp_to_closest_health_wisp.assert_called_once()


async def test_multiple_clients_one_fails():
    """Two clients: one above fail threshold, one below → FAILURE."""
    good = FakeClient()
    # 0.9 < 1.0 collect threshold → tries one wisp tp (returns False), stops.
    # 0.9 >= 0.5 fail threshold → good passes.
    good.calc_health_ratio.return_value = 0.9
    good.calc_mana_ratio.return_value = 1.0

    bad = FakeClient()
    bad.calc_health_ratio.return_value = 0.3  # stays low, no wisps, below 0.5 fail threshold
    bad.calc_mana_ratio.return_value = 1.0
    bad.tp_to_closest_health_wisp.return_value = False

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [good, bad], _DEFAULT_PARAMS)

    assert result == "FAILURE"
    good.tp_to_closest_health_wisp.assert_called_once()  # tried once, no wisps, 0.9 >= 0.5 → ok


async def test_both_collect_and_fail_check():
    """Collects one wisp but still below fail threshold → FAILURE."""
    client = FakeClient()
    # Collection loop: ratio 0.3 → tp True; ratio 0.4 → tp False → break.
    # _get_ratios post-collection: one more calc_health_ratio call → 3rd value.
    client.calc_health_ratio.side_effect = [0.3, 0.4, 0.4]
    client.calc_mana_ratio.return_value = 1.0
    client.tp_to_closest_health_wisp.side_effect = [True, False]

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [client], _DEFAULT_PARAMS)  # fail threshold = 0.5

    assert result == "FAILURE"
    assert client.tp_to_closest_health_wisp.call_count == 2


async def test_zero_collect_threshold_skips_collection():
    """if_health_below=0.0 → health wisp loop is entirely skipped."""
    client = FakeClient()
    client.calc_health_ratio.return_value = 0.1  # would normally trigger collection
    client.calc_mana_ratio.return_value = 1.0

    params = {**_DEFAULT_PARAMS, "if_health_below": 0.0, "fail_if_health_below": 0.0}

    with patch.object(cw, "_WISP_COLLECT_DELAY", 0):
        result = await cw.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"
    client.tp_to_closest_health_wisp.assert_not_called()
