"""Tests for structured drop entries: the schema's back-compat and the migration script."""

import json

from bartlebot.bot.schema import BotConfig, DropModel
from scripts.migrate_drop_entries import migrate_drops

_MINIMAL = {
    "name": "Test Bot",
    "initial": "start",
    "states": [{"id": "start", "node_type": "wait", "params": {}}],
    "transitions": [],
}


def _config(drops) -> BotConfig:
    return BotConfig.model_validate({**_MINIMAL, "drops": drops})


# ---------------------------------------------------------------------------
# Schema back-compat
# ---------------------------------------------------------------------------


def test_bare_names_load_as_uncategorized_entries():
    config = _config(["Amber", "Boots of Dawn"])
    assert config.drops == [DropModel(name="Amber"), DropModel(name="Boots of Dawn")]


def test_entry_form_loads_with_its_category():
    config = _config([{"name": "Amber", "category": "Reagents"}])
    assert config.drops == [DropModel(name="Amber", category="Reagents")]


def test_mixed_forms_load():
    config = _config(["Amber", {"name": "Skyscream", "category": "Hats"}])
    assert [d.name for d in config.drops] == ["Amber", "Skyscream"]
    assert [d.category for d in config.drops] == [None, "Hats"]


def test_bare_names_survive_a_dump_validate_round_trip():
    """UndoManager round-trips the config on every edit — this is that path."""
    config = _config(["Amber"])
    again = BotConfig.model_validate(config.model_dump())
    assert again.drops == config.drops


def test_uncategorized_drops_serialize_without_a_null_category():
    # BotConfig.save() uses exclude_none=True; the file should not gain "category": null.
    dumped = _config(["Amber"]).model_dump(exclude_none=True)
    assert dumped["drops"] == [{"name": "Amber"}]


def test_drop_order_is_preserved():
    names = ["Cloak", "Amber", "Boots"]
    assert [d.name for d in _config(names).drops] == names


def test_empty_drops_is_still_valid():
    assert _config([]).drops == []


# ---------------------------------------------------------------------------
# Migration script
# ---------------------------------------------------------------------------


def test_migration_converts_bare_names():
    data = {"drops": ["Amber", "Boots of Dawn"]}
    assert migrate_drops(data) == 2
    assert data["drops"] == [{"name": "Amber"}, {"name": "Boots of Dawn"}]


def test_migration_is_idempotent():
    data = {"drops": ["Amber"]}
    migrate_drops(data)
    assert migrate_drops(data) == 0


def test_migration_leaves_existing_entries_alone():
    data = {"drops": [{"name": "Amber", "category": "Reagents"}]}
    assert migrate_drops(data) == 0
    assert data["drops"] == [{"name": "Amber", "category": "Reagents"}]


def test_migration_preserves_order_in_a_mixed_list():
    data = {"drops": ["Cloak", {"name": "Amber", "category": "Reagents"}, "Boots"]}
    assert migrate_drops(data) == 2
    assert [d["name"] for d in data["drops"]] == ["Cloak", "Amber", "Boots"]


def test_migration_ignores_a_bot_with_no_drops():
    data = {"name": "x"}
    assert migrate_drops(data) == 0
    assert "drops" not in data


def test_migrated_output_still_loads():
    data = {**_MINIMAL, "drops": ["Amber"]}
    migrate_drops(data)
    config = BotConfig.model_validate(json.loads(json.dumps(data)))
    assert config.drops == [DropModel(name="Amber")]
