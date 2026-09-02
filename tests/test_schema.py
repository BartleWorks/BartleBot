"""Tests for BotConfig pydantic schema validation and serialization."""

import json

import pytest
from pydantic import ValidationError

from bartlebot.bot.schema import BotConfig

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

VALID_DATA = {
    "id": "test-bot",
    "name": "Test Bot",
    "initial": "state_a",
    "recovery_state": "state_a",
    "states": [
        {
            "id": "state_a",
            "node_type": "enter_zone",
            "params": {"from_zone": "ZoneA", "to_zone": "ZoneB", "xyz": {"x": 0.0, "y": 0.0, "z": 0.0}},
        },
        {
            "id": "state_b",
            "node_type": "enter_zone",
            "params": {"from_zone": "ZoneB", "to_zone": "ZoneA", "xyz": {"x": 1.0, "y": 2.0, "z": 3.0}},
        },
    ],
    "transitions": [
        {"trigger": "advance", "source": "state_a", "dest": "state_b", "when": "SUCCESS"},
        {"trigger": "advance", "source": "state_b", "dest": "state_a", "when": "SUCCESS"},
        {"trigger": "advance", "source": "state_a", "dest": "state_a"},
        {"trigger": "advance", "source": "state_b", "dest": "state_b"},
    ],
}


# ---------------------------------------------------------------------------
# Valid config loading
# ---------------------------------------------------------------------------


def test_valid_config_loads():
    config = BotConfig.model_validate(VALID_DATA)
    assert config.name == "Test Bot"
    assert config.initial == "state_a"
    assert len(config.states) == 2
    assert len(config.transitions) == 4


def test_optional_fields_have_defaults():
    config = BotConfig.model_validate(VALID_DATA)
    assert config.description is None
    assert config.version == "1.0"
    assert config.clients == "all"


def test_a_start_location_in_an_older_file_is_dropped():
    """Nothing read it, so it is not a field any more — but old files still carry
    it, and a bot that refuses to load is a worse answer than one that forgets (#80)."""
    config = BotConfig.model_validate({**VALID_DATA, "start_location": "The Commons"})

    assert not hasattr(config, "start_location")
    assert "start_location" not in config.model_dump()


def test_state_optional_fields_have_defaults():
    config = BotConfig.model_validate(VALID_DATA)
    state = config.states[0]
    assert state.label is None
    assert state.position is None


def test_transition_when_defaults_to_none():
    data = {
        **VALID_DATA,
        "transitions": [
            {"trigger": "advance", "source": "state_a", "dest": "state_b"},
        ],
    }
    config = BotConfig.model_validate(data)
    assert config.transitions[0].when is None


def test_transition_when_is_preserved():
    config = BotConfig.model_validate(VALID_DATA)
    assert config.transitions[0].when == "SUCCESS"


# ---------------------------------------------------------------------------
# Required field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", ["name", "initial", "recovery_state", "states", "transitions"])
def test_missing_required_field_raises(missing_field):
    data = {k: v for k, v in VALID_DATA.items() if k != missing_field}
    with pytest.raises(ValidationError):
        BotConfig.model_validate(data)


# ---------------------------------------------------------------------------
# initial state validation
# ---------------------------------------------------------------------------


def test_initial_must_reference_existing_state():
    data = {**VALID_DATA, "initial": "nonexistent_state"}
    with pytest.raises(ValidationError, match="initial state 'nonexistent_state' is not in states list"):
        BotConfig.model_validate(data)


def test_recovery_state_must_reference_existing_state():
    data = {**VALID_DATA, "recovery_state": "nonexistent_state"}
    with pytest.raises(ValidationError, match="recovery_state 'nonexistent_state' is not in states list"):
        BotConfig.model_validate(data)


def test_initial_referencing_first_state_is_valid():
    config = BotConfig.model_validate(VALID_DATA)
    assert config.initial == config.states[0].id


# ---------------------------------------------------------------------------
# to_machine_config()
# ---------------------------------------------------------------------------


def test_to_machine_config_structure():
    config = BotConfig.model_validate(VALID_DATA)
    mc = config.to_machine_config()
    assert mc["initial"] == "state_a"
    assert len(mc["states"]) == 2
    assert len(mc["transitions"]) == 4


def test_to_machine_config_state_has_id_and_node_type():
    config = BotConfig.model_validate(VALID_DATA)
    state = config.to_machine_config()["states"][0]
    assert state["id"] == "state_a"
    assert state["node_type"] == "enter_zone"


def test_to_machine_config_when_generates_condition():
    """when='SUCCESS' should produce conditions=['_result_is_SUCCESS'] without 'when' key."""
    config = BotConfig.model_validate(VALID_DATA)
    transitions = config.to_machine_config()["transitions"]
    t = next(t for t in transitions if t["source"] == "state_a" and t["dest"] == "state_b")
    assert t["conditions"] == ["_result_is_SUCCESS"]
    assert "when" not in t  # 'when' is not a valid transitions library kwarg


def test_to_machine_config_no_when_no_conditions():
    """Transitions without when should have no conditions key."""
    config = BotConfig.model_validate(VALID_DATA)
    transitions = config.to_machine_config()["transitions"]
    t = next(t for t in transitions if t["source"] == "state_a" and t["dest"] == "state_a")
    assert "conditions" not in t
    assert "when" not in t


def test_to_machine_config_excludes_label_when_none():
    config = BotConfig.model_validate(VALID_DATA)
    for t in config.to_machine_config()["transitions"]:
        assert "label" not in t  # label is None and should be excluded


# ---------------------------------------------------------------------------
# Save / load roundtrip
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path):
    config = BotConfig.model_validate(VALID_DATA)
    path = tmp_path / "bot.json"
    config.save(path)
    loaded = BotConfig.load(path)
    assert loaded.name == config.name
    assert loaded.initial == config.initial
    assert len(loaded.states) == len(config.states)
    assert len(loaded.transitions) == len(config.transitions)


def test_save_produces_valid_json(tmp_path):
    config = BotConfig.model_validate(VALID_DATA)
    path = tmp_path / "bot.json"
    config.save(path)
    with open(path) as f:
        data = json.load(f)
    assert data["name"] == "Test Bot"
    assert data["initial"] == "state_a"


def test_load_nonexistent_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        BotConfig.load(tmp_path / "missing.json")


def test_load_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not valid json")
    with pytest.raises(json.JSONDecodeError):
        BotConfig.load(path)


def test_load_invalid_schema_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"name": "Bad Bot"}))  # missing required fields
    with pytest.raises(ValidationError):
        BotConfig.load(path)
