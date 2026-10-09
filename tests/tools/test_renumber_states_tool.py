"""Tests for tools/renumber_states.py — renumbering a bot's state ids in canvas order."""

import json
import sys

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from bartlebot.bot.schema import BotConfig  # noqa: E402
from bartlebot.combat.profile_models import BotCombatStrategy  # noqa: E402
from tools import renumber_states  # noqa: E402
from tools.renumber_states import renumber, renumber_map  # noqa: E402

BOT_ID = "00000000-0000-0000-0000-000000000001"


def _state(sid: str, x: float, y: float = 0.0) -> dict:
    return {"id": sid, "node_type": "quicksell", "label": sid, "position": {"x": x, "y": y}, "params": {}}


def _edge(src: str, dst: str, when: str = "SUCCESS") -> dict:
    return {"trigger": "advance", "source": src, "dest": dst, "when": when}


def _config(**overrides) -> BotConfig:
    data = {
        "id": BOT_ID,
        "name": "Test",
        "initial": "state_9",
        "recovery_state": "state_4",
        # File order differs from canvas order on purpose.
        "states": [_state("state_9", 200), _state("state_4", 0), _state("state_12", 100, 90)],
        "transitions": [_edge("state_4", "state_12"), _edge("state_12", "state_9", "FAILURE")],
        "regions": [{"id": "r1", "label": "R", "members": ["state_9", "state_12"], "color": 1}],
    }
    data.update(overrides)
    return BotConfig.model_validate(data)


def test_ids_follow_canvas_order_and_every_reference_is_rewritten():
    config = _config()
    id_map = renumber(config)

    assert id_map == {"state_4": "state_1", "state_12": "state_2", "state_9": "state_3"}
    assert [s.id for s in config.states] == ["state_1", "state_2", "state_3"]
    assert (config.initial, config.recovery_state) == ("state_3", "state_1")
    assert [(t.source, t.dest) for t in config.transitions] == [("state_1", "state_2"), ("state_2", "state_3")]
    assert config.regions[0].members == ["state_3", "state_2"]


def test_an_already_ordered_bot_maps_nothing():
    config = _config()
    renumber(config)
    assert renumber_map(config) == {}


def test_a_state_without_a_position_sorts_last():
    config = _config(states=[_state("state_9", 200), {"id": "loose", "node_type": "quicksell"}, _state("state_4", 0)])
    assert renumber(config)["loose"] == "state_3"


def test_subgraph_instance_members_keep_their_ids():
    config = _config(
        initial="state_4",
        states=[_state("state_4", 0), _state("inst_state_1", 100), _state("state_9", 200)],
        transitions=[],
        regions=[
            {
                "id": "inst",
                "label": "I",
                "members": ["inst_state_1"],
                "color": 1,
                "source": {"subgraph_id": "sub", "version": 1},
            }
        ],
    )
    id_map = renumber(config)
    assert "inst_state_1" not in id_map
    assert id_map == {"state_4": "state_1", "state_9": "state_2"}


def test_a_new_id_colliding_with_a_kept_member_id_is_refused():
    config = _config(
        initial="state_4",
        states=[_state("state_4", 0), _state("state_1", 100)],
        transitions=[],
        regions=[
            {
                "id": "state",
                "label": "I",
                "members": ["state_1"],
                "color": 1,
                "source": {"subgraph_id": "s", "version": 1},
            }
        ],
    )
    with pytest.raises(ValueError, match="collide"):
        renumber_map(config)


def test_dry_run_leaves_the_file_alone(tmp_path):
    path = tmp_path / "bot.json"
    _config().save(path)
    before = path.read_text(encoding="utf-8")

    assert renumber_states.main([str(path)]) == 0
    assert path.read_text(encoding="utf-8") == before


def test_write_saves_the_bot_and_rekeys_strategies_in_both_roots(tmp_path, monkeypatch):
    project, user = tmp_path / "project", tmp_path / "user"
    monkeypatch.setattr(renumber_states, "PROJECT_ROOT", project)
    monkeypatch.setattr(renumber_states, "product_dir", lambda: user)

    for root, sid in ((project, "shipped"), (user, "mine")):
        bcs = BotCombatStrategy.model_validate(
            {"id": sid, "name": sid, "bot_id": BOT_ID, "state_assignments": [{"state_ids": ["state_9"]}]}
        )
        bcs.save(root / "bot_strategies" / f"{sid}.json")

    path = tmp_path / "bot.json"
    _config().save(path)
    assert renumber_states.main([str(path), "--write"]) == 0

    assert BotConfig.load(path).initial == "state_3"
    for root, sid in ((project, "shipped"), (user, "mine")):
        saved = json.loads((root / "bot_strategies" / f"{sid}.json").read_text(encoding="utf-8"))
        assert saved["state_assignments"][0]["fights"] == [{"state": "state_3"}]


# ---------------------------------------------------------------------------
# --prefix t_: the template scheme
# ---------------------------------------------------------------------------


def _with_instance() -> BotConfig:
    return _config(
        initial="sync_1_state_2",
        recovery_state="sync_1_state_1",
        states=[
            _state("state_9", 200),
            _state("sync_1_state_1", 0),
            _state("sync_1_state_2", 50),
            _state("sync_1_inner_1_state_1", 60),
            _state("state_4", 100),
        ],
        transitions=[_edge("sync_1_state_2", "state_4"), _edge("state_4", "state_9")],
        regions=[
            {
                "id": "sync_1",
                "label": "Sync",
                "members": ["sync_1_state_1", "sync_1_state_2", "sync_1_inner_1_state_1"],
                "color": 1,
                "source": {"subgraph_id": "sync", "version": 1},
            },
            {
                "id": "sync_1_inner_1",
                "label": "Inner",
                "members": ["sync_1_inner_1_state_1"],
                "color": 2,
                "source": {"subgraph_id": "inner", "version": 1},
            },
        ],
    )


def test_the_template_prefix_numbers_nodes_and_prefixes_instances():
    config = _with_instance()
    id_map = renumber(config, "t_")

    assert id_map == {
        "state_4": "t_1",
        "state_9": "t_2",
        "sync_1_state_1": "t_sync_1_state_1",
        "sync_1_state_2": "t_sync_1_state_2",
        "sync_1_inner_1_state_1": "t_sync_1_inner_1_state_1",
    }
    assert [r.id for r in config.regions] == ["t_sync_1", "t_sync_1_inner_1"]
    assert config.regions[1].members == ["t_sync_1_inner_1_state_1"]
    assert (config.initial, config.recovery_state) == ("t_sync_1_state_2", "t_sync_1_state_1")
    assert [(t.source, t.dest) for t in config.transitions] == [("t_sync_1_state_2", "t_1"), ("t_1", "t_2")]


def test_the_default_prefix_leaves_instance_ids_alone():
    config = _with_instance()
    renumber(config)

    assert [r.id for r in config.regions] == ["sync_1", "sync_1_inner_1"]


def test_a_template_already_in_the_scheme_maps_nothing():
    config = _with_instance()
    renumber(config, "t_")

    assert renumber(config, "t_") == {}
