"""Tests for collect_safety_warnings() in safety_warning_dialog.py."""

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.safety_warning_dialog import collect_safety_warnings
from bartlebot.settings_manager import SettingsManager


def _config(*states: dict) -> BotConfig:
    """Build a minimal valid BotConfig containing the given state dicts."""
    state_list = [{"id": f"s{i}", "node_type": "sleep", **s} for i, s in enumerate(states)]
    if not state_list:
        state_list = [{"id": "s0", "node_type": "sleep"}]
    return BotConfig.model_validate(
        {
            "name": "Test Bot",
            "initial": state_list[0]["id"],
            "recovery_state": state_list[0]["id"],
            "states": state_list,
            "transitions": [],
        }
    )


# ---------------------------------------------------------------------------
# No warnings
# ---------------------------------------------------------------------------


def test_no_risky_nodes_returns_empty():
    config = _config({"node_type": "sleep"})
    assert collect_safety_warnings(config) == []


def test_unrelated_node_returns_empty():
    config = _config({"node_type": "enter_zone", "params": {}})
    assert collect_safety_warnings(config) == []


# ---------------------------------------------------------------------------
# quicksell
# ---------------------------------------------------------------------------


def test_quicksell_emits_warning():
    config = _config({"node_type": "quicksell", "params": {}})
    warnings = collect_safety_warnings(config)
    assert len(warnings) == 1
    assert "quicksell" in warnings[0].message.lower() or "backpack" in warnings[0].message.lower()


def test_quicksell_unset_flags_show_the_app_setting_and_say_so():
    """The stored value is not the answer for an unset param, and the file does not
    show which level decided — this dialog is the last stop before the sale."""
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)
    config = _config({"node_type": "quicksell", "params": {}})
    details = dict(collect_safety_warnings(config)[0].details)
    assert details["Sell Pets"] == "No (app setting)"


def test_quicksell_pinned_flags_are_shown_without_the_app_setting_note():
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)
    config = _config({"node_type": "quicksell", "params": {"sell_pets": True}})
    details = dict(collect_safety_warnings(config)[0].details)
    assert details["Sell Pets"] == "Yes"


def test_quicksell_all_flags_false_shows_no():
    config = _config(
        {
            "node_type": "quicksell",
            "params": {
                "sell_pets": False,
                "sell_socket_wrenches": False,
                "sell_skeleton_keys": False,
            },
        }
    )
    warnings = collect_safety_warnings(config)
    # Warning still fires (node is present), but details reflect the false values.
    assert len(warnings) == 1
    details = warnings[0].details
    assert all(v == "No" for _, v in details)


def test_quicksell_details_cover_sell_flags():
    config = _config({"node_type": "quicksell", "params": {}})
    details = collect_safety_warnings(config)[0].details
    keys = [k for k, _ in details]
    assert not any("Crowns" in k for k in keys)
    assert any("Pets" in k for k in keys)
    assert any("Socket" in k for k in keys)
    assert any("Skeleton" in k for k in keys)


def test_multiple_quicksell_states_prefix_details_with_label():
    config = _config(
        {"id": "qs1", "node_type": "quicksell", "label": "Sell A", "params": {}},
        {"id": "qs2", "node_type": "quicksell", "label": "Sell B", "params": {}},
    )
    details = collect_safety_warnings(config)[0].details
    assert any("Sell A" in k for k, _ in details)
    assert any("Sell B" in k for k, _ in details)


def test_multiple_quicksell_states_no_label_falls_back_to_id():
    config = _config(
        {"id": "qs1", "node_type": "quicksell", "params": {}},
        {"id": "qs2", "node_type": "quicksell", "params": {}},
    )
    details = collect_safety_warnings(config)[0].details
    assert any("qs1" in k for k, _ in details)
    assert any("qs2" in k for k, _ in details)


# ---------------------------------------------------------------------------
# collect_reagent
# ---------------------------------------------------------------------------


def test_collect_reagent_emits_warning():
    config = _config({"node_type": "collect_reagent", "params": {"zone": "Z", "entity_name": "Fungus"}})
    warnings = collect_safety_warnings(config)
    assert len(warnings) == 1
    assert "reagent" in warnings[0].message.lower()


def test_collect_reagent_has_no_details():
    config = _config({"node_type": "collect_reagent", "params": {"zone": "Z", "entity_name": "Fungus"}})
    assert collect_safety_warnings(config)[0].details == []


# ---------------------------------------------------------------------------
# teleport_to_friend
# ---------------------------------------------------------------------------


def test_teleport_to_friend_emits_warning():
    config = _config({"node_type": "teleport_to_friend", "params": {"friend_name": "Alice", "to_zone": "ZoneX"}})
    warnings = collect_safety_warnings(config)
    assert len(warnings) == 1
    assert "friend" in warnings[0].message.lower()


def test_teleport_to_friend_details_include_friend_name():
    config = _config({"node_type": "teleport_to_friend", "params": {"friend_name": "Alice", "to_zone": "ZoneX"}})
    details = collect_safety_warnings(config)[0].details
    assert any(v == "Alice" for _, v in details)


def test_teleport_to_friend_details_include_destination_zone():
    config = _config({"node_type": "teleport_to_friend", "params": {"friend_name": "Alice", "to_zone": "ZoneX"}})
    details = collect_safety_warnings(config)[0].details
    assert any(v == "ZoneX" for _, v in details)


def test_teleport_to_friend_details_include_game_setting_note():
    config = _config({"node_type": "teleport_to_friend", "params": {"friend_name": "Alice", "to_zone": "ZoneX"}})
    details = collect_safety_warnings(config)[0].details
    assert any("Dblclick" in v for _, v in details)


def test_multiple_teleport_to_friend_states_prefix_details_with_label():
    config = _config(
        {
            "id": "tf1",
            "node_type": "teleport_to_friend",
            "label": "Go A",
            "params": {"friend_name": "Alice", "to_zone": "Z"},
        },
        {
            "id": "tf2",
            "node_type": "teleport_to_friend",
            "label": "Go B",
            "params": {"friend_name": "Bob", "to_zone": "Z"},
        },
    )
    details = collect_safety_warnings(config)[0].details
    assert any("Go A" in k for k, _ in details)
    assert any("Go B" in k for k, _ in details)


# ---------------------------------------------------------------------------
# Multiple risky node types together
# ---------------------------------------------------------------------------


def test_multiple_risky_node_types_produce_multiple_warnings():
    config = _config(
        {"node_type": "quicksell", "params": {}},
        {"node_type": "collect_reagent", "params": {"zone": "Z", "entity_name": "Fungus"}},
        {"node_type": "teleport_to_friend", "params": {"friend_name": "Alice", "to_zone": "ZoneX"}},
    )
    warnings = collect_safety_warnings(config)
    assert len(warnings) == 3


def test_warning_order_matches_node_specs_order():
    """Warnings are emitted in NODE_SPECS insertion order, not config state order."""
    from bartlebot.nodes.registry import NODE_SPECS

    risky_types_in_spec_order = [nt for nt, spec in NODE_SPECS.items() if spec.safety_warning]
    config = _config(
        {"node_type": "teleport_to_friend", "params": {"friend_name": "Alice", "to_zone": "Z"}},
        {"node_type": "quicksell", "params": {}},
    )
    warnings = collect_safety_warnings(config)
    emitted_types = []
    for w in warnings:
        for nt in risky_types_in_spec_order:
            spec_msg = NODE_SPECS[nt].safety_warning
            if spec_msg and spec_msg in w.message:
                emitted_types.append(nt)
                break
    assert emitted_types == [nt for nt in risky_types_in_spec_order if nt in ("quicksell", "teleport_to_friend")]
