"""Tests for collect_safety_warnings() in safety_warning_dialog.py."""

import pytest
from PyQt6.QtWidgets import QCheckBox

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.dialogs.first_run_dialog import QuicksellPage
from bartlebot.gui.dialogs.safety_warning_dialog import (
    QUICKSELL_OPT_OUT_LABEL,
    SafetyWarning,
    SafetyWarningDialog,
    collect_safety_warnings,
    declared_sales_message,
    level_scaling_players,
    level_scaling_warning,
    quick_friend_teleport_warning,
    uses_friends_list,
)
from bartlebot.nodes.zone_entry import enter_dungeon
from bartlebot.settings_manager import SettingsManager
from tests.conftest import FakeClient


@pytest.fixture(autouse=True)
def _quicksell_on():
    """Quicksell is off by default (#262); these are about a bot that sells."""
    SettingsManager.instance().set_general("default_quicksell_enabled", True)


def _config(*states: dict, options: list[dict] | None = None) -> BotConfig:
    """Build a minimal valid BotConfig containing the given state dicts.

    *options* are declarations, which is where a risky node's settings now live:
    quicksell's five and teleport_to_friend's target name are bot options, so a
    dialog test says what the bot declares rather than what a state's params hold.
    """
    state_list = [{"id": f"s{i}", "node_type": "sleep", **s} for i, s in enumerate(states)]
    if not state_list:
        state_list = [{"id": "s0", "node_type": "sleep"}]
    return BotConfig.model_validate(
        {
            "id": "test-bot",
            "name": "Test Bot",
            "initial": state_list[0]["id"],
            "recovery_state": state_list[0]["id"],
            "states": state_list,
            "transitions": [],
            "options": options or [],
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


def test_relog_does_not_warn():
    """Nothing about a relog is the user's to decide before the run (#98)."""
    config = _config({"node_type": "relog", "params": {"relog_clients": [0]}})
    assert collect_safety_warnings(config) == []


# ---------------------------------------------------------------------------
# quicksell
# ---------------------------------------------------------------------------


def test_quicksell_emits_warning():
    config = _config({"node_type": "quicksell", "params": {}})
    warnings = collect_safety_warnings(config)
    assert len(warnings) == 1
    assert "quicksell" in warnings[0].message.lower() or "backpack" in warnings[0].message.lower()


def test_quicksell_shows_the_resolved_value_and_names_the_level_that_decided_it():
    """The stored value is not the answer, and the file does not show which level
    decided — this dialog is the last stop before an irreversible sale."""
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)
    config = _config({"node_type": "quicksell", "params": {}})

    details = dict(collect_safety_warnings(config)[0].details)

    assert details["Sell Pets"] == "No (App settings)"


def test_a_declared_flag_is_shown_as_coming_from_the_bot():
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)
    config = _config(
        {"node_type": "quicksell", "params": {}},
        options=[{"name": "Sell Pets", "type": "bool", "default": True}],
    )

    details = dict(collect_safety_warnings(config)[0].details)

    assert details["Sell Pets"] == "Yes (Bot default)"


def test_the_users_own_value_is_shown_as_theirs():
    config = _config({"node_type": "quicksell", "params": {}})
    SettingsManager.instance().set_option_override(config.id, "Sell Pets", False)

    details = dict(collect_safety_warnings(config)[0].details)

    assert details["Sell Pets"] == "No (Your value)"


def test_quicksell_details_cover_the_switch_the_flags_and_the_keep_list():
    config = _config(
        {"node_type": "quicksell", "params": {}},
        options=[{"name": "Quicksell Keep List", "type": "drop_keep_list", "default": ["Amber"]}],
    )

    details = dict(collect_safety_warnings(config)[0].details)

    assert details["Quicksell"].startswith("Yes")
    assert set(details) >= {"Sell Pets", "Sell Socket Wrenches", "Sell Skeleton Keys"}
    assert details["Quicksell Keep List"].startswith("Amber (")
    assert not any("Crowns" in k for k in details)


def test_an_empty_keep_list_says_nothing_rather_than_showing_a_blank():
    config = _config({"node_type": "quicksell", "params": {}})

    details = dict(collect_safety_warnings(config)[0].details)

    assert details["Quicksell Keep List"].startswith("(nothing)")


def test_the_disabled_sentence_is_the_one_the_dialog_shows_when_nothing_sells():
    """The full warning ends with it, so the two cannot say different things."""
    from bartlebot.nodes.utility import quicksell

    assert quicksell.NODE_SPEC.safety_warning.endswith(quicksell.SAFETY_WARNING_DISABLED)


def test_quicksell_still_warns_when_enabled_resolves_true():
    config = _config({"node_type": "quicksell", "params": {}})

    warnings = collect_safety_warnings(config)

    assert len(warnings) == 1
    assert "Lock valuable items" in warnings[0].message
    assert dict(warnings[0].details)["Quicksell"].startswith("Yes")


def test_the_users_own_override_turning_quicksell_off_removes_the_warning():
    """Nothing is sold, so a sale warning has nothing to say — and a dialog that fires on
    runs with no risk is one the user learns to click straight through."""
    config = _config({"node_type": "quicksell", "params": {}})
    SettingsManager.instance().set_option_override(config.id, "Quicksell", False)

    assert collect_safety_warnings(config) == []


def test_turning_quicksell_off_in_settings_removes_the_warning():
    SettingsManager.instance().set_general("default_quicksell_enabled", False)
    config = _config({"node_type": "quicksell", "params": {}})

    assert collect_safety_warnings(config) == []


def test_an_override_turning_quicksell_back_on_outranks_the_app_setting():
    SettingsManager.instance().set_general("default_quicksell_enabled", False)
    config = _config({"node_type": "quicksell", "params": {}})
    SettingsManager.instance().set_option_override(config.id, "Quicksell", True)

    warnings = collect_safety_warnings(config)

    assert len(warnings) == 1
    assert "Lock valuable items" in warnings[0].message


def test_a_bot_that_declares_quicksell_off_keeps_the_sentence_that_is_still_true():
    """The user did not choose this one and cannot see it from here, so the cost of a long
    farm with no selling — backpack, then bank — is still worth saying."""
    from bartlebot.nodes.utility import quicksell

    config = _config(
        {"node_type": "quicksell", "params": {}},
        options=[{"name": "Quicksell", "type": "bool", "default": False}],
    )

    warnings = collect_safety_warnings(config)

    assert len(warnings) == 1
    assert warnings[0].message == quicksell.SAFETY_WARNING_DISABLED
    assert warnings[0].details == [("Quicksell", "No (Bot default)")]


def test_a_disabled_quicksell_does_not_drag_the_rest_of_the_table_along():
    config = _config(
        {"node_type": "quicksell", "params": {}},
        options=[
            {"name": "Quicksell", "type": "bool", "default": False},
            {"name": "Quicksell Keep List", "type": "drop_keep_list", "default": ["Amber"]},
        ],
    )

    details = dict(collect_safety_warnings(config)[0].details)

    assert set(details) == {"Quicksell"}


def test_a_suppressed_quicksell_leaves_the_other_warnings_alone():
    config = _config(
        {"node_type": "quicksell", "params": {}},
        {"node_type": "collect_reagent", "params": {"zone": "Z", "entity_name": "Fungus"}},
    )
    SettingsManager.instance().set_option_override(config.id, "Quicksell", False)

    warnings = collect_safety_warnings(config)

    assert len(warnings) == 1
    assert "reagent" in warnings[0].message.lower()


def test_quicksell_details_are_listed_once_however_many_nodes_there_are():
    """The settings are the bot's now, not any one state's, so prefixing them with a
    state label would say they could differ between the two — they cannot."""
    config = _config(
        {"id": "qs1", "node_type": "quicksell", "label": "Sell A", "params": {}},
        {"id": "qs2", "node_type": "quicksell", "label": "Sell B", "params": {}},
    )

    details = collect_safety_warnings(config)[0].details

    assert [k for k, _ in details].count("Sell Pets") == 1
    assert not any("Sell A" in k or "Sell B" in k for k, _ in details)


# ---------------------------------------------------------------------------
# quicksell's explanation, and what a bot's own file sells (#145)
#
# Nearly every bot sells once the templates reach the library, and a warning on every
# run is one the user learns to click through -- taking the other warnings in this
# dialog with it. So the explanation carries a check box that turns it off for the
# whole app, and a bot whose own file sells more than the user's settings would is
# flagged once per bot whatever that check box said.
# ---------------------------------------------------------------------------


def _quicksell_bot(bot_id: str = "test-bot", **declared: bool) -> BotConfig:
    config = _config(
        {"node_type": "quicksell", "params": {}},
        options=[{"name": name.replace("_", " "), "type": "bool", "default": v} for name, v in declared.items()],
    )
    return config.model_copy(update={"id": bot_id})


def _run(warnings, *, opt_out: bool = False) -> SafetyWarningDialog:
    """Run past *warnings* the way the user does, ticking every opt-out box if asked."""
    dlg = SafetyWarningDialog(warnings)
    for box in dlg.findChildren(QCheckBox):
        box.setChecked(opt_out)
    dlg.accept()
    return dlg


def _stop_explaining() -> None:
    SettingsManager.instance().show_quicksell_explanation = False


def test_the_explanation_offers_to_stop_showing_itself():
    (warning,) = collect_safety_warnings(_quicksell_bot())

    assert "Lock valuable items" in warning.message
    assert warning.opt_out is not None
    assert warning.opt_out[0] == QUICKSELL_OPT_OUT_LABEL


def test_running_past_the_explanation_shows_it_again_next_run(qapp):
    """Shown before each run until the user says otherwise -- running is not saying so."""
    _run(collect_safety_warnings(_quicksell_bot()))

    assert len(collect_safety_warnings(_quicksell_bot())) == 1


def test_ticking_the_box_stops_the_explanation_for_every_bot(qapp):
    _run(collect_safety_warnings(_quicksell_bot()), opt_out=True)

    assert SettingsManager.instance().show_quicksell_explanation is False
    assert collect_safety_warnings(_quicksell_bot("another-bot")) == []


def test_ticking_the_box_then_cancelling_changes_nothing(qapp):
    """A run the user backed out of is not an answer they gave."""
    dlg = SafetyWarningDialog(collect_safety_warnings(_quicksell_bot()))
    dlg.findChild(QCheckBox).setChecked(True)

    dlg.reject()

    assert SettingsManager.instance().show_quicksell_explanation is True


def test_the_box_sits_under_its_own_warning_only(qapp):
    """Not a footer button: the dialog holds other warnings, and the box must not read as theirs."""
    config = _config(
        {"node_type": "quicksell", "params": {}},
        {"node_type": "collect_reagent", "params": {"zone": "Z", "entity_name": "Fungus"}},
    )

    dlg = SafetyWarningDialog(collect_safety_warnings(config))

    assert [box.text() for box in dlg.findChildren(QCheckBox)] == [QUICKSELL_OPT_OUT_LABEL]


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, parentless_shows):
    SafetyWarningDialog(collect_safety_warnings(_quicksell_bot(Sell_Skeleton_Keys=True)))

    assert parentless_shows == []


def test_with_the_explanation_off_a_bot_selling_what_the_users_settings_keep_is_flagged_alone():
    _stop_explaining()

    (warning,) = collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True))

    assert warning.message == declared_sales_message(["Sell Skeleton Keys"])
    assert "skeleton keys" in warning.message and "Sell Skeleton Keys off" in warning.message
    # Nothing to turn off: this one is asked once per bot by itself.
    assert warning.opt_out is None


def test_such_a_bot_leads_with_what_it_sells():
    (warning,) = collect_safety_warnings(_quicksell_bot(Sell_Skeleton_Keys=True))

    assert warning.message.startswith(declared_sales_message(["Sell Skeleton Keys"]))
    assert "Lock valuable items" in warning.message


def test_a_sale_run_past_for_one_bot_is_not_asked_about_again_for_it(qapp):
    _stop_explaining()
    _run(collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True)))

    assert SettingsManager.instance().accepted_quicksell_sales("tree-of-life") == ["Sell Skeleton Keys"]
    assert collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True)) == []


@pytest.mark.parametrize("enabled", [True, False])
def test_after_setups_quicksell_page_no_run_shows_the_explanation(qapp, enabled):
    """#230: the page explained quicksell, so a run after it does not explain it again."""
    page = QuicksellPage()
    page.quicksell.group.header.control.setChecked(enabled)

    assert page.record()

    assert collect_safety_warnings(_quicksell_bot()) == []


def test_after_setups_quicksell_page_a_declared_sale_is_still_flagged_once(qapp):
    QuicksellPage().record()

    (warning,) = collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True))
    assert warning.message == declared_sales_message(["Sell Skeleton Keys"])

    _run([warning])
    assert collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True)) == []


def test_running_past_a_sale_does_not_also_turn_the_explanation_off(qapp):
    _run(collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True)))

    (warning,) = collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True))

    assert "Lock valuable items" in warning.message
    assert not warning.message.startswith(declared_sales_message(["Sell Skeleton Keys"]))


def test_a_sale_run_past_for_one_bot_is_still_asked_about_for_another(qapp):
    _stop_explaining()
    _run(collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True)))

    assert len(collect_safety_warnings(_quicksell_bot("another-bot", Sell_Skeleton_Keys=True))) == 1


def test_a_bot_that_starts_selling_something_else_is_asked_about_that_alone(qapp):
    """A template update can turn a second flag on in a bot the user already accepted."""
    _stop_explaining()
    _run(collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True)))

    (warning,) = collect_safety_warnings(
        _quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True, Sell_Socket_Wrenches=True)
    )

    assert warning.message == declared_sales_message(["Sell Socket Wrenches"])


def test_cancelling_a_sale_records_nothing(qapp):
    _stop_explaining()
    dlg = SafetyWarningDialog(collect_safety_warnings(_quicksell_bot("tree-of-life", Sell_Skeleton_Keys=True)))

    dlg.reject()

    assert SettingsManager.instance().accepted_quicksell_sales("tree-of-life") == []


def test_a_bot_that_keeps_more_than_the_user_asked_is_not_flagged():
    _stop_explaining()

    assert collect_safety_warnings(_quicksell_bot(Sell_Pets=False)) == []


def test_two_sales_are_named_together():
    message = declared_sales_message(["Sell Socket Wrenches", "Sell Skeleton Keys"])
    assert "socket wrenches and skeleton keys" in message
    assert "Sell Socket Wrenches and Sell Skeleton Keys off" in message


def test_the_explanation_comes_back_when_turned_back_on_in_settings():
    _stop_explaining()
    SettingsManager.instance().show_quicksell_explanation = True

    assert len(collect_safety_warnings(_quicksell_bot())) == 1


def test_a_warning_with_nothing_to_answer_records_nothing(qapp):
    dlg = SafetyWarningDialog([SafetyWarning(message="This bot teleports to collect reagents.")])

    assert dlg.findChildren(QCheckBox) == []
    dlg.accept()

    sm = SettingsManager.instance()
    assert sm.show_quicksell_explanation is True
    assert sm.get("accepted_quicksell_sales") == {}


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


# ---------------------------------------------------------------------------
# teleport_to_friend target resolution in the dialog
# ---------------------------------------------------------------------------


def test_teleport_to_friend_offers_the_name_for_editing(monkeypatch):
    """A wrong name is worth fixing here rather than cancelling, walking to Settings, and
    starting the run over — and it is the user's value, so it is theirs to change."""
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set_general("default_friend_name", "Alice StormBlade")
    config = _config({"node_type": "teleport_to_friend", "params": {"to_zone": "ZoneX"}})

    warning = collect_safety_warnings(config)[0]

    assert warning.editable_setting == ("Friend Name", "default_friend_name")
    assert any(v == "Alice StormBlade" for _, v in warning.details)


def test_teleport_to_friend_shows_an_unconfigured_name_as_the_problem_it_is(monkeypatch):
    from bartlebot.settings_manager import SettingsManager

    monkeypatch.setattr(SettingsManager.instance(), "default_friend_name", "")
    config = _config({"node_type": "teleport_to_friend", "params": {"to_zone": "ZoneX"}})

    details = collect_safety_warnings(config)[0].details

    assert any("no friend name configured" in v for _, v in details)


def test_hooked_player_target_is_shown_read_only():
    """In slot mode the name is not the user's to change — it is whoever is logged into
    that window — so the dialog reports it and offers no field."""
    from bartlebot.game import wizard_names

    clients = [FakeClient(), FakeClient()]
    wizard_names.remember(clients[1], "Luke SkullHunter")
    config = _config(
        {
            "node_type": "teleport_to_friend",
            "params": {"target_hooked_player": True, "target_player": 2, "to_zone": "Z"},
        }
    )
    try:
        warning = collect_safety_warnings(config, clients)
    finally:
        wizard_names.clear_cache()

    assert warning[0].editable_setting is None
    assert any(v == "Player 2 — Luke SkullHunter" for _, v in warning[0].details)


def test_hooked_player_target_says_so_when_the_name_is_not_known_yet():
    config = _config(
        {
            "node_type": "teleport_to_friend",
            "params": {"target_hooked_player": True, "target_player": 1, "to_zone": "Z"},
        }
    )

    details = collect_safety_warnings(config, [FakeClient()])[0].details

    assert any("has not been detected" in v for _, v in details)


def test_a_bot_that_still_pins_a_name_is_not_offered_the_app_setting_field():
    """Editing the app setting would change nothing while the bot's own value outranks it,
    so offering the field would be a lie about what the run will do."""
    config = _config({"node_type": "teleport_to_friend", "params": {"friend_name": "Alice", "to_zone": "Z"}})

    assert collect_safety_warnings(config)[0].editable_setting is None


# ---------------------------------------------------------------------------
# enter_dungeon — the Team-Up split-party warning
#
# enter_dungeon is in most of the library, so all three conditions have to hold
# before the warning is earned: the author ticked Offers Team-Up, the user turned
# the option on, and more than one client is hooked.
# ---------------------------------------------------------------------------


def _teamup_config(*, offers: bool = True, team_up: bool = True, team_size: int | None = None) -> BotConfig:
    options: list[dict] = [{"name": enter_dungeon.OPTION_TEAM_UP, "type": "bool", "default": team_up}]
    if team_size is not None:
        options.append({"name": enter_dungeon.OPTION_MINIMUM_TEAM_SIZE, "type": "int", "default": team_size})
    return _config(
        {"node_type": "enter_dungeon", "params": {enter_dungeon.PARAM_OFFERS_TEAM_UP: offers}},
        options=options,
    )


def _enter_dungeon_warning(config, clients):
    found = [w for w in collect_safety_warnings(config, clients) if "Team-Up" in w.message]
    return found[0] if found else None


def test_teamup_with_two_clients_warns():
    warning = _enter_dungeon_warning(_teamup_config(), [FakeClient(), FakeClient()])

    assert warning is not None
    assert "different copy of the dungeon" in warning.message


def test_teamup_with_one_client_does_not_warn():
    """One client cannot be split from itself."""
    assert _enter_dungeon_warning(_teamup_config(), [FakeClient()]) is None


def test_a_normal_entrance_does_not_warn():
    """Every dungeon bot has this node. A warning on all of them is one the user learns
    to click away, which is the one habit this dialog cannot afford to teach."""
    assert _enter_dungeon_warning(_teamup_config(team_up=False), [FakeClient(), FakeClient()]) is None


def test_the_option_alone_does_not_warn():
    """Team-Up off at the param means the node will not queue whatever the option says,
    so the warning would describe something that cannot happen."""
    assert _enter_dungeon_warning(_teamup_config(offers=False), [FakeClient(), FakeClient()]) is None


def test_the_warning_names_the_team_size_and_the_player_count():
    warning = _enter_dungeon_warning(_teamup_config(team_size=4), [FakeClient(), FakeClient()])

    assert warning is not None
    assert ("Players hooked", "2") in warning.details
    assert any(
        label == enter_dungeon.OPTION_MINIMUM_TEAM_SIZE and value.startswith("4") for label, value in warning.details
    )


# ---------------------------------------------------------------------------
# An elixir already active at run start (#141)
# ---------------------------------------------------------------------------

_SCALING_PLAYERS = [{"name": "Level Scaling Players", "type": "player_list", "default": [1, 3]}]


@pytest.mark.parametrize("node_type", ["handle_level_scaling", "check_level_scaling"])
def test_a_bot_with_any_level_scaling_node_names_its_players_for_the_elixir_read(node_type):
    config = _config({"node_type": node_type}, options=_SCALING_PLAYERS)

    assert level_scaling_players(config, client_count=4) == [1, 3]


def test_a_player_with_no_client_behind_it_is_not_read():
    config = _config({"node_type": "handle_level_scaling"}, options=_SCALING_PLAYERS)

    assert level_scaling_players(config, client_count=2) == [1]


def test_a_bot_with_no_level_scaling_node_reads_nobody():
    """The read costs a task and a wait at every run start, so a bot that never scales skips it."""
    config = _config({"node_type": "sleep"}, options=_SCALING_PLAYERS)

    assert level_scaling_players(config, client_count=4) == []


def test_the_users_override_decides_who_is_read():
    config = _config({"node_type": "handle_level_scaling"}, options=_SCALING_PLAYERS)
    SettingsManager.instance().set_option_override(config.id, "Level Scaling Players", [2])

    assert level_scaling_players(config, client_count=4) == [2]


def test_an_elixir_already_active_warns_and_names_the_players():
    warning = level_scaling_warning([1, 3])

    assert "cannot tell one elixir from another" in warning.message
    assert warning.details == [("Elixir already active", "Player 1, Player 3")]


def test_no_elixir_active_does_not_warn():
    assert level_scaling_warning([]) is None


def test_the_elixir_warning_joins_the_other_safety_warnings():
    config = _config({"node_type": "handle_level_scaling"}, options=_SCALING_PLAYERS)

    assert collect_safety_warnings(config) == []
    warnings = collect_safety_warnings(config, [FakeClient()], [1])
    assert [w.details for w in warnings] == [[("Elixir already active", "Player 1")]]


# ---------------------------------------------------------------------------
# Dblclick Go To Friend on in a client this app did not launch
# ---------------------------------------------------------------------------


def test_a_team_run_uses_the_friends_list():
    """Forming the group and every port to another player go through it."""
    assert uses_friends_list(_config({"node_type": "sleep"}), client_count=2)


def test_a_solo_run_uses_the_friends_list_only_to_teleport_to_a_friend():
    assert not uses_friends_list(_config({"node_type": "sleep"}), client_count=1)
    assert uses_friends_list(_config({"node_type": "teleport_to_friend"}), client_count=1)


def test_the_option_on_warns_and_names_the_players_and_where_to_turn_it_off():
    warning = quick_friend_teleport_warning([2, 3])

    assert "as if the friend were not on the list" in warning.message
    assert warning.details == [
        ("Dblclick Go To Friend on", "Player 2, Player 3"),
        ("Required Game Setting", "Options → Gameplay Options 4 → Dblclick Go To Friend: No"),
    ]


def test_the_option_off_everywhere_does_not_warn():
    assert quick_friend_teleport_warning([]) is None


def test_the_option_warning_joins_the_other_safety_warnings():
    config = _config({"node_type": "sleep"})

    warnings = collect_safety_warnings(config, [FakeClient(), FakeClient()], quick_teleport_players=[1])

    assert [w.details[0] for w in warnings] == [("Dblclick Go To Friend on", "Player 1")]
