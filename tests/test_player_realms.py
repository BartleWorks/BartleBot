"""The Player Realms option: Together or Separate, and the bots that cannot be Separate (#132).

A node param says where the players go; this option says how they are arranged. A node that
merges players by nature (teleport_to_friend) rules Separate out, enforced three ways: the
option rows gray the choice, the canvas marks the node, and the run refuses to start.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot import bot_options, player_realms
from bartlebot.bot.schema import BotConfig, BotOptionModel, StateModel
from bartlebot.gui.canvas.edit_scene import _validate_node
from bartlebot.gui.widgets.option_widgets import (
    NODE_ONLY_TYPES,
    block_separate_if_merging,
    make_option_widget,
    widget_value,
)
from bartlebot.gui.widgets.player_realms_choice import PlayerRealmsChoice
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.paths import project_root
from bartlebot.template_store import list_templates

_ROOT = project_root()

#: The bots that ship Separate: the next-realm reagent collectors, Scrap Iron and the Portico, the reagent bots
#: rebuilt from Reagent Collector (which declares it), and that template's draft.
SHIPPED_SEPARATE = {
    "bots/25_crafting/aeriel_shores_aethyr_burst.json",
    "bots/25_crafting/nightside_flax.json",
    "bots/25_crafting/aeriel_shores_silver_chests.json",
    "bots/25_crafting/crab_alley_stone_block.json",
    "bots/25_crafting/mirkholm_keep_reagents.json",
    "bots/25_crafting/the_necropolis_deep_mushroom.json",
    "bots/25_crafting/the_wild_deep_mushroom.json",
    "bots/25_crafting/the_zocalo_red_mandrake.json",
    "bots/25_crafting/scrap_iron_collector.json",
    "bots/25_crafting/selenopolis_parchment.json",
    "bots/25_crafting/the_portico_pearl.json",
    "templates/reagent-collector/draft.json",
}


def _config(*node_types: str, separate: bool | None = None) -> BotConfig:
    options = []
    if separate is not None:
        value = player_realms.SEPARATE if separate else player_realms.TOGETHER
        options.append(BotOptionModel(name=player_realms.OPTION, type=player_realms.OPTION_TYPE, default=value))
    return BotConfig(
        id="realms-bot",
        name="Realms Bot",
        initial="s0",
        recovery_state="s0",
        options=options,
        states=[StateModel(id=f"s{i}", node_type=t, label=f"Node {i}", params={}) for i, t in enumerate(node_types)],
        transitions=[],
    )


# ---------------------------------------------------------------------------
# Who declares what
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "node_type",
    [
        "change_realm",
        "enter_dungeon",
        "enter_zone",
        "enter_zone_via_object",
        "sync_in_zone",
        "handle_combat",
        "teleport_to_marked_location",
        "teleport_to_world_hub",
    ],
)
def test_every_node_that_moves_players_declares_the_one_shared_spec(node_type):
    assert player_realms.SPEC in NODE_SPECS[node_type].required_bot_options


def test_the_option_defaults_to_together():
    config = _config("sync_in_zone")
    resolution = bot_options.resolve_for_config(config, player_realms.OPTION)
    assert resolution.value == player_realms.TOGETHER


def test_only_teleport_to_friend_merges_players():
    assert [name for name, spec in NODE_SPECS.items() if spec.merges_players] == ["teleport_to_friend"]


def test_only_the_two_values_are_valid():
    assert bot_options.value_matches_type(player_realms.SEPARATE, player_realms.OPTION_TYPE)
    assert bot_options.value_matches_type(player_realms.TOGETHER, player_realms.OPTION_TYPE)
    assert not bot_options.value_matches_type("separate", player_realms.OPTION_TYPE)
    assert not bot_options.value_matches_type(True, player_realms.OPTION_TYPE)


def test_a_separate_declaration_survives_a_save_and_a_together_one_is_pruned():
    separate = _config("sync_in_zone", separate=True).file_dump()["options"]
    together = _config("sync_in_zone", separate=False).file_dump()["options"]
    assert separate == [{"name": player_realms.OPTION, "type": player_realms.OPTION_TYPE, "default": "Separate"}]
    assert together == []


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_bot_without_a_merging_node_may_be_separate():
    assert player_realms.separate_blocked_by(_config("sync_in_zone", "handle_combat")) == []


def test_every_merging_node_is_named():
    config = _config("sync_in_zone", "teleport_to_friend", "teleport_to_friend")
    assert player_realms.separate_blocked_by(config) == ["Node 1", "Node 2"]
    assert player_realms.blocked_reason(["Node 1"]) == (
        "This bot cannot be Separate: its node 'Node 1' puts every player on one realm."
    )
    assert "nodes 'Node 1', 'Node 2' put" in player_realms.blocked_reason(["Node 1", "Node 2"])


def test_the_run_refuses_separate_only_with_a_merging_node():
    merging = _config("sync_in_zone", "teleport_to_friend")
    assert player_realms.run_refusal(merging, player_realms.SEPARATE) is not None
    assert player_realms.run_refusal(merging, player_realms.TOGETHER) is None
    assert player_realms.run_refusal(_config("sync_in_zone"), player_realms.SEPARATE) is None


def test_unrecognized_values_read_as_together():
    assert not player_realms.is_separate(None)
    assert not player_realms.is_separate("separate")
    assert player_realms.is_separate(player_realms.SEPARATE)


# ---------------------------------------------------------------------------
# The canvas
# ---------------------------------------------------------------------------


def _merging_error(config: BotConfig, index: int) -> bool:
    _is_error, _is_warning, errors, _warnings = _validate_node(config.states[index], config)
    return any(player_realms.OPTION in e for e in errors)


def test_a_merging_node_is_an_error_in_a_bot_declared_separate():
    assert _merging_error(_config("sync_in_zone", "teleport_to_friend", separate=True), 1)


def test_a_merging_node_is_fine_in_a_together_bot():
    assert not _merging_error(_config("sync_in_zone", "teleport_to_friend"), 1)
    assert not _merging_error(_config("sync_in_zone", "teleport_to_friend", separate=False), 1)


def test_only_the_merging_node_is_marked():
    assert not _merging_error(_config("sync_in_zone", "teleport_to_friend", separate=True), 0)


# ---------------------------------------------------------------------------
# The control
# ---------------------------------------------------------------------------


def test_the_option_type_builds_the_radio_pair(qapp):
    assert player_realms.OPTION_TYPE in NODE_ONLY_TYPES
    widget = make_option_widget(player_realms.OPTION_TYPE, player_realms.SEPARATE)
    assert isinstance(widget, PlayerRealmsChoice)
    assert widget_value(widget) == player_realms.SEPARATE


def test_an_unset_value_shows_together(qapp):
    widget = make_option_widget(player_realms.OPTION_TYPE, None)
    assert widget_value(widget) == player_realms.TOGETHER


def test_a_merging_node_grays_separate_and_says_why(qapp):
    widget = make_option_widget(player_realms.OPTION_TYPE, player_realms.TOGETHER)
    block_separate_if_merging(widget, _config("sync_in_zone", "teleport_to_friend"))
    assert widget.separate_blocked()
    assert "'Node 1'" in widget.blocked_reason()


def test_a_blocked_separate_value_is_still_shown(qapp):
    # An override from before the node was added is the user's to clear, not ours.
    widget = make_option_widget(player_realms.OPTION_TYPE, player_realms.SEPARATE)
    block_separate_if_merging(widget, _config("sync_in_zone", "teleport_to_friend"))
    assert widget_value(widget) == player_realms.SEPARATE


def test_without_a_merging_node_separate_is_offered(qapp):
    widget = make_option_widget(player_realms.OPTION_TYPE, player_realms.TOGETHER)
    block_separate_if_merging(widget, _config("sync_in_zone"))
    assert not widget.separate_blocked()
    assert widget.blocked_reason() == ""


def test_other_controls_are_left_alone(qapp):
    widget = make_option_widget("bool", True)
    block_separate_if_merging(widget, _config("teleport_to_friend"))  # no error, no change
    assert widget_value(widget) is True


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------


def _shipped_bots() -> list[Path]:
    """Every bot, and every template draft: a bot made from one starts as a copy of it."""
    return sorted([*(_ROOT / "bots").rglob("*.json"), *(t.draft_path for t in list_templates(_ROOT))])


def test_exactly_the_reagent_bots_ship_separate():
    separate = set()
    for path in _shipped_bots():
        data = json.loads(path.read_text(encoding="utf-8"))
        if "states" not in data:
            continue
        if player_realms.declares_separate(BotConfig.model_validate(data)):
            separate.add(path.relative_to(_ROOT).as_posix())
    assert separate == SHIPPED_SEPARATE


def test_no_shipped_bot_is_separate_with_a_merging_node():
    for rel in SHIPPED_SEPARATE:
        config = BotConfig.load(_ROOT / rel)
        assert player_realms.separate_blocked_by(config) == [], rel
