"""Tests for the bot tag vocabulary, its normalizer, and the tags a graph suggests.

The normalizer is deliberately tolerant: it runs on every bot load, so a bad value
has to cost one tag rather than the whole file.
"""

from pathlib import Path

import pytest

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.bot_folders import TEMPLATES_FOLDER
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, normalize_tags, suggested_tags, wants_rewards_prompt
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.paths import project_root
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import compose_bot


def test_bot_tags_is_the_flattened_groups():
    assert BOT_TAGS == tuple(t for group in TAG_GROUPS.values() for t in group)


def test_no_tag_appears_in_two_groups():
    """A tag in two groups would render two chips that filter identically."""
    assert len(BOT_TAGS) == len(set(BOT_TAGS))


def test_known_tags_survive():
    assert normalize_tags(["Boss", "Gear"]) == ["Gear", "Boss"]


def test_output_is_in_vocabulary_order_not_input_order():
    """Storage order is fixed so the editor cannot produce diff noise by reordering."""
    assert normalize_tags(["Collecting", "Boss", "Gear"]) == ["Gear", "Boss", "Collecting"]


def test_duplicates_collapse():
    assert normalize_tags(["Gear", "Gear", "Boss"]) == ["Gear", "Boss"]


def test_unknown_tags_are_dropped_not_raised():
    """A typo costs one tag. Raising here would make the bot fail to open at all."""
    assert normalize_tags(["Gear", "gear", "Sparkles"]) == ["Gear"]


@pytest.mark.parametrize("raw", [None, "Gear", 42, {"Gear": True}])
def test_non_list_input_is_tolerated(raw):
    assert normalize_tags(raw) == []


def test_non_string_entries_are_skipped():
    assert normalize_tags(["Gear", 7, None]) == ["Gear"]


def _minimal_bot(**extra) -> dict:
    return {
        "id": "test-bot",
        "name": "T",
        "initial": "s1",
        "recovery_state": "s1",
        "states": [{"id": "s1", "node_type": "sleep"}],
        "transitions": [],
        **extra,
    }


def test_a_bot_with_a_bad_tag_still_loads():
    config = BotConfig.model_validate(_minimal_bot(tags=["Boss", "NotATag"]))

    assert config.tags == ["Boss"]


def test_tags_default_to_empty():
    assert BotConfig.model_validate(_minimal_bot()).tags == []


def test_tags_survive_a_round_trip(tmp_path):
    """UndoManager round-trips through model_dump/model_validate on every edit."""
    config = BotConfig.model_validate(_minimal_bot(tags=["Collecting", "Reagents"]))
    path = tmp_path / "b.json"
    config.save(path)

    assert BotConfig.load(path).tags == ["Reagents", "Collecting"]


# ---------------------------------------------------------------------------
# Suggested tags
# ---------------------------------------------------------------------------


def _states(*nodes: str | tuple[str, dict]) -> list[StateModel]:
    out = []
    for i, node in enumerate(nodes):
        node_type, params = node if isinstance(node, tuple) else (node, {})
        out.append(StateModel(id=f"s{i}", node_type=node_type, params=params))
    return out


def test_an_enter_gauntlet_node_suggests_gauntlet():
    assert suggested_tags(_states("enter_gauntlet", "handle_combat")) == ["Gauntlet"]


def test_a_reagent_route_with_no_combat_suggests_reagents_and_collecting():
    assert suggested_tags(_states("collect_reagent", "go_to_zone")) == ["Reagents", "Collecting"]


def test_a_reagent_node_in_a_combat_bot_suggests_reagents_but_not_collecting():
    """The boss that drops reagents is not a collecting bot, however many it drops."""
    assert suggested_tags(_states("collect_reagent", "handle_combat")) == ["Reagents"]


def test_enter_dungeon_suggests_only_what_its_params_offer():
    both = {"offers_team_up": True, "offers_challenge_mode": True}
    assert suggested_tags(_states(("enter_dungeon", both))) == ["Challenge Mode", "Team-Up"]
    assert suggested_tags(_states(("enter_dungeon", {"offers_team_up": True}))) == ["Team-Up"]
    assert suggested_tags(_states(("enter_dungeon", {"offers_challenge_mode": False}))) == []


def test_one_dungeon_offering_team_up_is_enough_for_the_bot():
    """The ``required_when`` rule: one state offering it is enough for the whole bot."""
    states = _states(("enter_dungeon", {}), ("enter_dungeon", {"offers_team_up": True}))
    assert suggested_tags(states) == ["Team-Up"]


def test_a_graph_implying_nothing_suggests_nothing():
    assert suggested_tags(_states("handle_combat", "go_to_zone", "not_a_node")) == []


def test_every_implied_tag_is_in_the_vocabulary():
    implied = {t for spec in NODE_SPECS.values() for t in (*spec.implies_tags, *spec.implies_tags_when)}
    assert implied, "no node implies a tag, so this checks nothing"
    assert implied <= set(BOT_TAGS)


def test_every_implies_tags_when_names_a_bool_param_on_its_own_node():
    for node_type, spec in NODE_SPECS.items():
        params = {p.key: p for p in spec.params}
        for tag, key in spec.implies_tags_when.items():
            assert key in params, f"{node_type}: {tag} keys on {key!r}, which is not one of its params"
            assert params[key].type == "bool", f"{node_type}: {tag} keys on {key!r}, which is not a bool"


def _library_bots() -> list[Path]:
    """Every library bot: templates are skeletons, and ``test_*`` files are scratch."""
    bots = project_root() / "bots"
    return [
        p
        for p in sorted(bots.rglob("*.json"))
        if TEMPLATES_FOLDER not in p.relative_to(bots).parts and not p.name.startswith("test_")
    ]


def test_every_library_bot_carries_its_suggested_tags():
    """The editor only reaches bots someone opens; this is what holds the rest.

    Missing tags only, never extra ones. A bot that keeps a reagent node and drops
    Collecting is an author's judgment, and a test cannot tell that from a mistake.
    """
    shipped = project_root()
    library = SubgraphLibrary(shipped, shipped)
    missing = []
    for path in _library_bots():
        config = BotConfig.load(path)
        composed, _report = compose_bot(config, library)
        if lacking := [t for t in suggested_tags(composed.states) if t not in config.tags]:
            missing.append(f"{path.relative_to(shipped).as_posix()}: {', '.join(lacking)}")
    assert not missing, "bots missing the tags their graph suggests:\n" + "\n".join(missing)


# ---------------------------------------------------------------------------
# The save-time Rewards prompt
# ---------------------------------------------------------------------------


def test_a_bot_with_no_rewards_tag_is_asked_for_one(tmp_path):
    assert wants_rewards_prompt(["Boss"], tmp_path / "bots" / "01_wizard_city" / "x.json")


def test_a_bot_not_yet_saved_anywhere_is_asked_too():
    assert wants_rewards_prompt([], None)


def test_one_rewards_tag_is_enough():
    assert not wants_rewards_prompt(["Pets", "Boss"], None)


def test_a_template_is_never_asked(tmp_path):
    """What a template's runs yield depends on the dungeon it is turned into."""
    assert not wants_rewards_prompt(["Dungeon"], tmp_path / "bots" / TEMPLATES_FOLDER / "x.json")
