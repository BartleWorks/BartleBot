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
    assert migrate_drops(data) == (2, 0)
    assert data["drops"] == [{"name": "Amber"}, {"name": "Boots of Dawn"}]


def test_migration_is_idempotent():
    data = {"drops": ["Amber"]}
    migrate_drops(data)
    assert migrate_drops(data) == (0, 0)


def test_migration_leaves_existing_entries_alone():
    data = {"drops": [{"name": "Amber", "category": "Reagents"}]}
    assert migrate_drops(data) == (0, 0)
    assert data["drops"] == [{"name": "Amber", "category": "Reagents"}]


def test_migration_preserves_order_in_a_mixed_list():
    data = {"drops": ["Cloak", {"name": "Amber", "category": "Reagents"}, "Boots"]}
    assert migrate_drops(data) == (2, 0)
    assert [d["name"] for d in data["drops"]] == ["Cloak", "Amber", "Boots"]


def test_migration_ignores_a_bot_with_no_drops():
    data = {"name": "x"}
    assert migrate_drops(data) == (0, 0)
    assert "drops" not in data


def test_migration_fills_in_the_in_game_name():
    data = {"drops": ["Balance Crushing Pin (60, Death)"]}
    assert migrate_drops(data) == (1, 1)
    assert data["drops"] == [{"name": "Balance Crushing Pin (60, Death)", "match_name": "Balance Crushing Pin (60)"}]


def test_migration_does_not_add_a_match_name_that_would_equal_the_name():
    data = {"drops": [{"name": "Amber", "category": "Reagents"}]}
    migrate_drops(data)
    assert "match_name" not in data["drops"][0]


def test_migration_leaves_a_hand_corrected_match_name_alone():
    data = {"drops": [{"name": "Balance Crushing Pin (60, Death)", "match_name": "Something Else"}]}
    assert migrate_drops(data) == (0, 0)
    assert data["drops"][0]["match_name"] == "Something Else"


def test_migration_uses_the_category_where_the_rule_needs_one():
    data = {
        "drops": [
            {"name": "Bright Critical Amethyst +44 (Ice)", "category": "Jewels"},
            {"name": "Mutate Snowman (Fire)"},
        ]
    }
    assert migrate_drops(data) == (0, 1)
    assert data["drops"][0]["match_name"] == "Bright Critical Amethyst +44"
    assert "match_name" not in data["drops"][1]


def test_migrated_output_still_loads():
    data = {**_MINIMAL, "drops": ["Amber"]}
    migrate_drops(data)
    config = BotConfig.model_validate(json.loads(json.dumps(data)))
    assert config.drops == [DropModel(name="Amber")]


# ---------------------------------------------------------------------------
# match_name / match_key
# ---------------------------------------------------------------------------


def test_match_key_falls_back_to_the_name():
    assert DropModel(name="Amber").match_key == "Amber"


def test_match_key_prefers_the_in_game_name():
    entry = DropModel(name="Balance Crushing Pin (60, Death)", match_name="Balance Crushing Pin (60)")
    assert entry.match_key == "Balance Crushing Pin (60)"


def test_a_match_name_survives_a_dump_validate_round_trip():
    entry = DropModel(name="Balance Crushing Pin (60, Death)", match_name="Balance Crushing Pin (60)")
    config = _config([entry.model_dump()])
    assert config.drops == [entry]


def test_an_absent_match_name_serializes_away():
    config = _config([{"name": "Amber", "category": "Reagents"}])
    dumped = config.model_dump(exclude_none=True)
    assert dumped["drops"] == [{"name": "Amber", "category": "Reagents"}]


def test_bare_names_still_load_without_a_match_name():
    assert _config(["Amber"]).drops == [DropModel(name="Amber")]
