"""Tests for scripts/migrate_teamup_node.py — the pure transform core.

The disk-walking main() is not exercised here; the migration rule lives in
``migrate_bot_data``, which operates on plain dicts.
"""

from __future__ import annotations

from bartlebot.nodes.zone_entry import enter_dungeon
from scripts.migrate_teamup_node import (
    LEGACY_NODE_TYPE,
    OPTION_MINIMUM_TEAM_SIZE,
    PARAM_OFFERS_TEAM_UP,
    migrate_bot_data,
)


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


def _teamup_state(state_id: str, **params) -> dict:
    return {"id": state_id, "node_type": LEGACY_NODE_TYPE, "params": params}


def test_state_becomes_enter_dungeon_with_the_param_ticked():
    data = _bot(states=[_teamup_state("sigil", from_zone="Hub", minimum_team_size=2)])
    data, changes = migrate_bot_data(data)

    state = data["states"][0]
    assert state["node_type"] == "enter_dungeon"
    assert state["params"][PARAM_OFFERS_TEAM_UP] is True
    assert state["params"]["from_zone"] == "Hub"
    assert len(changes) == 1


def test_the_team_size_param_is_dropped_from_the_state():
    """It is the user's value now, so leaving it in the state would be dead weight the
    node never reads — the same trap the quicksell params left behind."""
    data = _bot(states=[_teamup_state("sigil", minimum_team_size=3)])
    data, _ = migrate_bot_data(data)

    assert "minimum_team_size" not in data["states"][0]["params"]


def test_a_non_default_team_size_becomes_a_declaration():
    data = _bot(states=[_teamup_state("sigil", minimum_team_size=4)])
    data, changes = migrate_bot_data(data)

    assert data["options"] == [{"name": OPTION_MINIMUM_TEAM_SIZE, "type": "int", "default": 4}]
    assert any("declared" in c for c in changes)


def test_the_default_team_size_declares_nothing():
    """A declaration equal to the node's own default says nothing, and the save-time
    prune would drop it again on the next save."""
    data = _bot(states=[_teamup_state("sigil", minimum_team_size=2)])
    data, _ = migrate_bot_data(data)

    assert data.get("options", []) == []


def test_two_sigils_keep_the_larger_team():
    """A bot cannot declare two team sizes, so the smaller one would silently shrink a
    team the author asked for."""
    data = _bot(states=[_teamup_state("a", minimum_team_size=2), _teamup_state("b", minimum_team_size=4)])
    data, _ = migrate_bot_data(data)

    assert data["options"][0]["default"] == 4


def test_an_existing_declaration_is_left_alone():
    data = _bot(
        states=[_teamup_state("sigil", minimum_team_size=4)],
        options=[{"name": OPTION_MINIMUM_TEAM_SIZE, "type": "int", "default": 3}],
    )
    data, _ = migrate_bot_data(data)

    assert data["options"] == [{"name": OPTION_MINIMUM_TEAM_SIZE, "type": "int", "default": 3}]


def test_a_bot_with_no_teamup_state_is_not_rewritten():
    data = _bot(states=[{"id": "sigil", "node_type": "enter_dungeon", "params": {"from_zone": "Hub"}}])
    before = {"id": "sigil", "node_type": "enter_dungeon", "params": {"from_zone": "Hub"}}
    data, changes = migrate_bot_data(data)

    assert changes == []
    assert data["states"][0] == before


def test_running_twice_changes_nothing_the_second_time():
    data = _bot(states=[_teamup_state("sigil", minimum_team_size=4)])
    data, _ = migrate_bot_data(data)
    once = {"states": [dict(data["states"][0])], "options": list(data["options"])}

    data, changes = migrate_bot_data(data)

    assert changes == []
    assert data["states"][0] == once["states"][0]
    assert data["options"] == once["options"]


def test_the_migration_names_the_node_the_registry_actually_has():
    """The constants here are a second copy of the node's own, and a rename would
    otherwise leave the migration writing a param nothing reads."""
    assert PARAM_OFFERS_TEAM_UP == enter_dungeon.PARAM_OFFERS_TEAM_UP
    assert OPTION_MINIMUM_TEAM_SIZE == enter_dungeon.OPTION_MINIMUM_TEAM_SIZE
