"""Tests for tools/import_template.py — turning an old template bot file into a store draft."""

import sys

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from bartlebot.bot.schema import BotConfig  # noqa: E402
from bartlebot.content.templates.template_store import find_template, load_draft  # noqa: E402
from tools import import_template  # noqa: E402


def _old_template() -> BotConfig:
    return BotConfig.model_validate(
        {
            "id": "4ece5834-0000-0000-0000-000000000000",
            "name": "Boss Farmer Template",
            "description": "1) Record entering the dungeon.",
            "initial": "state_4",
            "recovery_state": "state_4",
            "states": [
                {"id": "state_8", "node_type": "quicksell", "position": {"x": 100, "y": 0}, "params": {}},
                {"id": "state_4", "node_type": "quicksell", "position": {"x": 0, "y": 0}, "params": {}},
            ],
            "transitions": [{"trigger": "advance", "source": "state_4", "dest": "state_8", "when": "SUCCESS"}],
        }
    )


def test_a_template_becomes_a_draft_under_its_own_id():
    config = _old_template()
    import_template.to_draft(config)

    assert config.id == "4ece5834-0000-0000-0000-000000000000"
    assert config.name == "Boss Farmer"
    assert config.description is None
    assert config.template_meta.blurb == "1) Record entering the dungeon."
    assert [s.id for s in config.states] == ["t_1", "t_2"]
    assert config.template is None


def test_a_draft_is_not_imported_twice():
    config = _old_template()
    import_template.to_draft(config)

    with pytest.raises(ValueError, match="already"):
        import_template.to_draft(config)


def test_write_puts_the_draft_in_the_store_and_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(import_template, "PROJECT_ROOT", tmp_path)
    src = tmp_path / "boss_farmer_template.json"
    _old_template().save(src)

    assert import_template.main([str(src)]) == 0
    assert find_template("4ece5834-0000-0000-0000-000000000000", tmp_path) is None

    assert import_template.main([str(src), "--write"]) == 0
    draft = load_draft("4ece5834-0000-0000-0000-000000000000", tmp_path)
    assert draft.name == "Boss Farmer"
    assert (tmp_path / "templates" / "boss-farmer" / "draft.json").is_file()
