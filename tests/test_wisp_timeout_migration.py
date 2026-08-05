"""Tests for scripts/migrate_wisp_timeout.py — the pure transform core.

The disk-walking main() is not exercised here; the migration rule lives in
``migrate_bot_data``, which operates on plain dicts.
"""

from __future__ import annotations

import copy

from scripts.migrate_wisp_timeout import DEFAULT_TIMEOUT, migrate_bot_data


def _bot(**overrides) -> dict:
    data = {
        "name": "Test Bot",
        "version": "1.0",
        "initial": "start",
        "states": [],
        "transitions": [],
    }
    data.update(overrides)
    return data


def _wisp_state(state_id: str, **params) -> dict:
    return {"id": state_id, "node_type": "collect_wisps", "params": params}


def test_resets_if_health_below_from_half():
    data = _bot(states=[_wisp_state("wisps", if_health_below=0.5, if_mana_below=1.0)])
    data, changes = migrate_bot_data(data)

    assert data["states"][0]["params"]["if_health_below"] == 1.0
    assert len(changes) == 2  # hp reset + timeout stamp
    assert any("if_health_below" in c for c in changes)


def test_leaves_fail_if_health_below_untouched():
    data = _bot(states=[_wisp_state("wisps", if_health_below=0.5, fail_if_health_below=0.5)])
    data, _ = migrate_bot_data(data)

    assert data["states"][0]["params"]["fail_if_health_below"] == 0.5


def test_stamps_missing_timeout():
    data = _bot(states=[_wisp_state("wisps", if_health_below=1.0)])
    data, changes = migrate_bot_data(data)

    assert data["states"][0]["params"]["timeout"] == DEFAULT_TIMEOUT
    assert len(changes) == 1
    assert "timeout" in changes[0]


def test_existing_timeout_is_left_untouched():
    data = _bot(states=[_wisp_state("wisps", if_health_below=1.0, timeout=25.0)])
    data, changes = migrate_bot_data(data)

    assert data["states"][0]["params"]["timeout"] == 25.0
    assert changes == []


def test_if_health_below_at_1_0_is_not_flagged():
    data = _bot(states=[_wisp_state("wisps", if_health_below=1.0, timeout=DEFAULT_TIMEOUT)])
    data, changes = migrate_bot_data(data)

    assert changes == []


def test_non_wisp_states_are_ignored():
    data = _bot(states=[{"id": "walk", "node_type": "enter_zone", "params": {"if_health_below": 0.5}}])
    data, changes = migrate_bot_data(data)

    assert changes == []
    assert data["states"][0]["params"] == {"if_health_below": 0.5}


def test_state_without_params_is_ignored():
    data = _bot(states=[{"id": "wisps", "node_type": "collect_wisps"}])

    assert migrate_bot_data(data)[1] == []


def test_migration_is_idempotent():
    data = _bot(states=[_wisp_state("wisps", if_health_below=0.5, if_mana_below=0.5)])

    once, first_changes = migrate_bot_data(copy.deepcopy(data))
    twice, second_changes = migrate_bot_data(copy.deepcopy(once))

    assert first_changes
    assert second_changes == []
    assert twice == once


def test_already_migrated_file_reports_no_changes():
    original = _bot(states=[_wisp_state("wisps", if_health_below=1.0, timeout=DEFAULT_TIMEOUT)])
    data, changes = migrate_bot_data(copy.deepcopy(original))

    assert changes == []
    assert data == original


def test_multiple_wisp_states_all_migrated():
    data = _bot(
        states=[
            _wisp_state("wisps_a", if_health_below=0.5),
            _wisp_state("wisps_b", if_health_below=0.5),
        ]
    )
    data, changes = migrate_bot_data(data)

    assert all(s["params"]["if_health_below"] == 1.0 for s in data["states"])
    assert all(s["params"]["timeout"] == DEFAULT_TIMEOUT for s in data["states"])
    assert len(changes) == 4
