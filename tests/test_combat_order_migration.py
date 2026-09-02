"""Tests for scripts/migrate_combat_order_removal.py — the pure transform core.

The disk-walking main() is not exercised here; the migration rule lives in
``migrate_bot_data``, which operates on plain dicts.
"""

from __future__ import annotations

import copy

from scripts.migrate_combat_order_removal import OPTION_NAME, migrate_bot_data


def _bot(**overrides) -> dict:
    data = {
        "name": "Test Bot",
        "version": "1.0",
        "initial": "start",
        "options": [],
        "states": [],
        "transitions": [],
    }
    data.update(overrides)
    return data


def _order_option() -> dict:
    return {
        "name": OPTION_NAME,
        "type": "int_list",
        "default": [],
        "description": "Default order clients enter combat.",
    }


def _safety_option() -> dict:
    return {"name": "Safety Delay", "type": "float", "default": 3.0}


def test_removes_bot_option():
    data = _bot(options=[_safety_option(), _order_option()])
    data, changes = migrate_bot_data(data)

    assert [o["name"] for o in data["options"]] == ["Safety Delay"]
    assert changes == [f"removed bot option {OPTION_NAME!r}"]


def test_removes_param():
    state = {
        "id": "state_5",
        "node_type": "handle_combat",
        "params": {"enemy": "Boss", "combat_order": [1, 2]},
    }
    data, changes = migrate_bot_data(_bot(states=[state]))

    assert data["states"][0]["params"] == {"enemy": "Boss"}
    assert len(changes) == 1


def test_leaves_other_legacy_combat_fields_alone():
    """The v2 DSL leftovers are a separate cleanup — this migration must not eat them."""
    state = {
        "id": "state_5",
        "node_type": "handle_combat",
        "params": {"use_bot_default": False, "combat_config": "### p1\nmass", "combat_order": [1]},
    }
    data = _bot(states=[state], use_bartlebot_default_combat_config=True, default_combat_config="### p1\nmass")
    data, _ = migrate_bot_data(data)

    params = data["states"][0]["params"]
    assert params == {"use_bot_default": False, "combat_config": "### p1\nmass"}
    assert data["use_bartlebot_default_combat_config"] is True
    assert data["default_combat_config"] == "### p1\nmass"


def test_idempotent_second_run_is_a_no_op():
    data = _bot(
        options=[_safety_option(), _order_option()],
        states=[
            {
                "id": "state_1",
                "node_type": "handle_combat",
                "params": {"combat_order": [1, 2]},
            }
        ],
    )
    once, first_changes = migrate_bot_data(copy.deepcopy(data))
    twice, second_changes = migrate_bot_data(copy.deepcopy(once))

    assert first_changes
    assert second_changes == []
    assert twice == once


def test_clean_bot_reports_no_changes():
    data = _bot(
        options=[_safety_option()],
        states=[{"id": "state_1", "node_type": "handle_combat", "params": {"enemy": ""}}],
    )
    _, changes = migrate_bot_data(data)

    assert changes == []


def test_tolerates_missing_and_malformed_sections():
    data = {"name": "Sparse", "initial": "s", "states": [{"id": "s", "node_type": "sleep"}, "not-a-dict"]}
    _, changes = migrate_bot_data(data)

    assert changes == []
