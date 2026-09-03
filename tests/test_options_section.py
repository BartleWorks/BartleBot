"""The two option surfaces: values in Setup, declarations in Options.

``OptionsSection`` shipped untested, which is how two bugs survived in it long
enough to be found by reading rather than by running: the rename fixup walked
only the *flat* ``flags`` list while the edge editor writes ``flag_groups`` — the
form that takes precedence at run time — and the Add/Edit dialog hardcoded
``type="bool"``, so pressing Edit on Safety Delay rewrote a float as a checkbox.

The assertion that matters most is the negative one: editing a value in Setup
must leave the bot file byte-identical.  That is the whole epic in one test.

Real widget tests (QApplication required), in the style of
``test_canvas_property_edits``.  The settings store is injected rather than
patched — both sections take one — so nothing here touches the real
``settings.json``.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QCheckBox, QDoubleSpinBox, QLineEdit, QSpinBox

from bartlebot import bot_options
from bartlebot import settings_manager as sm_mod
from bartlebot.bot.schema import (
    BotConfig,
    BotOptionModel,
    FlagCondition,
    FlagGroup,
    StateModel,
    TransitionModel,
)
from bartlebot.gui.sidebar.sections.options_section import OptionsSection
from bartlebot.gui.sidebar.sections.setup_section import SetupSection
from bartlebot.gui.widgets.option_widgets import (
    OPTION_TYPES,
    BoolChoice,
    StrListLineEdit,
    make_option_widget,
    set_widget_value,
    widget_value,
)
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget
from bartlebot.settings_manager import SettingsManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A SettingsManager writing to tmp_path, isolated from the real singleton."""
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


def _config(options=None, transitions=None) -> BotConfig:
    """A bot whose one node requires no options, so every option here is authorial.

    ``mark_location`` is one of the sixteen nodes with no ``required_bot_options``;
    most nodes pull in Safety Delay, which would put a derived row in every list.
    """
    return BotConfig(
        id="test-bot",
        name="Test Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="mark_location", params={})],
        transitions=transitions if transitions is not None else [],
        options=options if options is not None else [],
    )


def _repeat(default=True) -> BotOptionModel:
    return BotOptionModel(name="repeat", type="bool", default=default, description="Run again")


# ---------------------------------------------------------------------------
# The shared widget mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opt_type", OPTION_TYPES)
def test_every_option_type_round_trips_through_its_widget(qapp, opt_type):
    """The mapping used to exist three times; this is what makes one copy enough."""
    sample = {
        "bool": True,
        "int": 7,
        "float": 2.5,
        "str": "hello",
        "str_list": ["ItemA", "ItemB"],
        "player_list": [1, 3],
    }[opt_type]
    widget = make_option_widget(opt_type, sample)
    assert widget_value(widget) == sample


def test_a_declaration_of_none_still_builds_a_control(qapp):
    """``None`` means the author has no opinion — there is still a row to draw."""
    for opt_type in OPTION_TYPES:
        widget = make_option_widget(opt_type, None)
        assert widget_value(widget) in (False, 0, 0.0, "", [])


def test_a_value_of_the_wrong_shape_does_not_break_the_control(qapp):
    """An author can change an option's type out from under a stored value."""
    widget = make_option_widget("int", 0)
    set_widget_value(widget, "not a number")
    assert widget_value(widget) == 0


def test_the_widget_types_are_the_ones_the_panels_expect(qapp):
    assert isinstance(make_option_widget("bool", True), QCheckBox)
    assert isinstance(make_option_widget("int", 1), QSpinBox)
    assert isinstance(make_option_widget("float", 1.0), QDoubleSpinBox)
    assert isinstance(make_option_widget("str_list", []), StrListLineEdit)
    assert isinstance(make_option_widget("player_list", []), PlayerListWidget)
    assert isinstance(make_option_widget("str", ""), QLineEdit)


# ---------------------------------------------------------------------------
# Setup — the values surface
# ---------------------------------------------------------------------------


def test_editing_a_value_writes_an_override_and_not_the_declaration(qapp, settings):
    config = _config([_repeat(default=True)])
    section = SetupSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)

    assert settings.get_option_override("test-bot", "repeat") is False
    assert config.options[0].default is True


def test_a_bot_file_is_untouched_by_a_value_edit(qapp, settings):
    """The epic in one assertion: tuning a shared bot must not modify it."""
    config = _config([_repeat(default=True)])
    before = config.model_dump()
    section = SetupSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)

    assert config.model_dump() == before


def test_an_override_wins_over_the_declaration_in_the_row(qapp, settings):
    settings.set_option_override("test-bot", "repeat", False)
    config = _config([_repeat(default=True)])
    section = SetupSection(settings=settings)
    section.set_config(config)

    assert section.option_widgets["repeat"].isChecked() is False
    assert section.resolution_for("repeat").source == bot_options.SOURCE_OVERRIDE


def test_a_row_with_no_override_reports_the_bot_as_its_source(qapp, settings):
    config = _config([_repeat(default=True)])
    section = SetupSection(settings=settings)
    section.set_config(config)

    assert section.resolution_for("repeat").source == bot_options.SOURCE_BOT_FILE


def test_reset_removes_the_override_row(qapp, settings):
    settings.set_option_override("test-bot", "repeat", False)
    config = _config([_repeat(default=True)])
    section = SetupSection(settings=settings)
    section.set_config(config)

    section._on_reset("repeat")

    assert settings.get("bot_option_overrides") == {}
    assert section.option_widgets["repeat"].isChecked() is True


def test_typing_a_value_back_to_the_default_forgets_the_override(qapp, settings):
    """Reverting by hand and pressing Reset are the same act and must agree.

    An override storing the value the level below it already supplies marks the row
    as changed while changing nothing -- and would then silently outrank an author
    who later improved the declaration it happens to match.
    """
    config = _config([_repeat(default=True)])
    section = SetupSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)
    assert settings.get_option_override("test-bot", "repeat") is False

    section.option_widgets["repeat"].setChecked(True)

    assert settings.get("bot_option_overrides") == {}
    assert section.resolution_for("repeat").source == bot_options.SOURCE_BOT_FILE


def test_a_value_edit_keeps_the_control_it_was_made_in(qapp, settings):
    """Rebuilding the list would delete the spin box under the arrow being clicked."""
    config = _config([BotOptionModel(name="Passes", type="int", default=1)])
    section = SetupSection(settings=settings)
    section.set_config(config)
    before = section.option_widgets["Passes"]

    before.setValue(5)

    assert section.option_widgets["Passes"] is before
    assert settings.get_option_override("test-bot", "Passes") == 5


def test_resetting_puts_the_resolved_value_back_on_screen(qapp, settings):
    config = _config([BotOptionModel(name="Passes", type="int", default=1)])
    section = SetupSection(settings=settings)
    section.set_config(config)

    section.option_widgets["Passes"].setValue(5)
    section._on_reset("Passes")

    assert section.option_widgets["Passes"].value() == 1
    assert settings.get("bot_option_overrides") == {}


def test_the_reset_button_is_enabled_only_while_a_value_is_overridden(qapp, settings):
    config = _config([_repeat(default=True)])
    section = SetupSection(settings=settings)
    section.set_config(config)
    chrome = section._chrome["repeat"]

    assert not chrome.reset_btn.isEnabled()

    section.option_widgets["repeat"].setChecked(False)
    assert chrome.reset_btn.isEnabled()

    section._on_reset("repeat")
    assert not chrome.reset_btn.isEnabled()


def test_a_false_override_is_a_choice_and_survives_a_rebuild(qapp, settings):
    """Absence is unset; a stored ``false`` is an answer. Nothing may prune it."""
    config = _config([_repeat(default=True)])  # so False really is the user's choice
    section = SetupSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)
    section.set_config(config)

    assert settings.get_option_override("test-bot", "repeat") is False
    assert section.resolution_for("repeat").source == bot_options.SOURCE_OVERRIDE


def test_an_empty_list_override_is_a_choice_too(qapp, settings):
    config = _config([BotOptionModel(name="Players", type="player_list", default=[1, 2])])
    section = SetupSection(settings=settings)
    section.set_config(config)

    section._on_value_changed("Players", [])

    assert settings.get_option_override("test-bot", "Players") == []
    assert section.resolution_for("Players").value == []


def test_setup_has_no_authoring_controls_to_reveal(qapp, settings):
    """``set_editable`` is accepted and ignored — values are a run-mode concern."""
    config = _config([_repeat()])
    section = SetupSection(settings=settings)
    section.set_config(config)

    section.set_editable(True)
    section.set_editable(False)

    assert set(section.option_widgets) == {"repeat"}


# ---------------------------------------------------------------------------
# Options — the declaration surface
# ---------------------------------------------------------------------------


def test_editing_a_default_writes_the_declaration(qapp, settings):
    config = _config([_repeat(default=True)])
    section = OptionsSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)

    assert config.options[0].default is False
    assert settings.get("bot_option_overrides") == {}


def test_renaming_an_option_carries_the_users_value(qapp, settings):
    settings.set_option_override("test-bot", "repeat", False)
    config = _config([_repeat()])
    section = OptionsSection(settings=settings)
    section.set_config(config)

    section._rename_option("repeat", "again")

    assert settings.get_option_override("test-bot", "again") is False
    assert settings.get_option_override("test-bot", "repeat") is bot_options.UNSET


def test_renaming_an_option_fixes_both_flag_forms(qapp, settings):
    """The grouped form is what the edge editor writes, and it takes precedence."""
    transition = TransitionModel(
        trigger="advance",
        source="a",
        dest="a",
        when="SUCCESS",
        flags=[FlagCondition(option="repeat")],
        flag_groups=[FlagGroup(flags=[FlagCondition(option="repeat", negate=True)])],
    )
    config = _config([_repeat()], transitions=[transition])
    section = OptionsSection(settings=settings)
    section.set_config(config)

    section._rename_option("repeat", "again")

    assert [fc.option for fc in transition.flags] == ["again"]
    assert [fc.option for fc in transition.flag_groups[0].flags] == ["again"]


def test_deleting_an_option_drops_its_override_and_both_flag_forms(qapp, settings):
    settings.set_option_override("test-bot", "repeat", False)
    transition = TransitionModel(
        trigger="advance",
        source="a",
        dest="a",
        when="SUCCESS",
        flags=[FlagCondition(option="repeat")],
        flag_groups=[FlagGroup(flags=[FlagCondition(option="repeat")])],
    )
    config = _config([_repeat()], transitions=[transition])
    section = OptionsSection(settings=settings)
    section.set_config(config)

    section._on_delete_option("repeat")

    assert config.options == []
    assert transition.flags == []
    assert transition.flag_groups == []
    assert settings.get_option_override("test-bot", "repeat") is bot_options.UNSET


def test_deleting_one_option_leaves_the_rest_of_a_group_intact(qapp, settings):
    transition = TransitionModel(
        trigger="advance",
        source="a",
        dest="a",
        when="SUCCESS",
        flag_groups=[FlagGroup(flags=[FlagCondition(option="repeat"), FlagCondition(option="loud")])],
    )
    config = _config([_repeat(), BotOptionModel(name="loud", type="bool", default=False)], transitions=[transition])
    section = OptionsSection(settings=settings)
    section.set_config(config)

    section._on_delete_option("repeat")

    assert [fc.option for fc in transition.flag_groups[0].flags] == ["loud"]


def test_the_authoring_controls_follow_set_editable(qapp, settings):
    config = _config([_repeat()])
    section = OptionsSection(settings=settings)
    section.set_config(config)

    section.set_editable(True)
    assert section._add_option_btn.isVisibleTo(section)
    assert all(btn.isVisibleTo(section) for btn in section._edit_btns + section._delete_btns)

    section.set_editable(False)
    assert not section._add_option_btn.isVisibleTo(section)
    assert not any(btn.isVisibleTo(section) for btn in section._edit_btns + section._delete_btns)


def test_a_derived_option_cannot_be_renamed_or_deleted(qapp, settings):
    """It is re-composed from its NodeSpec on load, so the edit would not survive."""
    config = BotConfig(
        id="test-bot",
        name="Test Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="enter_dungeon", params={"offers_challenge_mode": True})],
        transitions=[],
    )
    section = OptionsSection(settings=settings)
    section.set_config(config)
    section.set_editable(True)

    assert "Challenge Mode" in section.option_widgets
    assert section._edit_btns == []
    assert section._delete_btns == []


# ---------------------------------------------------------------------------
# The Add / Edit dialog
# ---------------------------------------------------------------------------


def test_editing_a_float_option_keeps_it_a_float(qapp, settings):
    """The dialog hardcoded ``type="bool"``: one click turned Safety Delay into a checkbox."""
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    opt = BotOptionModel(name="Safety Delay", type="float", default=2.5)
    dlg = _AddOptionDialog(["Safety Delay"], existing_option=opt)

    assert dlg._current_type() == "float"
    dlg._on_ok()
    result = dlg.get_option()

    assert result.type == "float"
    assert result.default == 2.5
    dlg.deleteLater()


def test_the_dialog_offers_every_option_type(qapp):
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    dlg = _AddOptionDialog([])
    offered = [dlg._type_combo.itemData(i) for i in range(dlg._type_combo.count())]

    assert offered == list(OPTION_TYPES)
    dlg.deleteLater()


def test_the_dialog_refuses_a_duplicate_name(qapp):
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    dlg = _AddOptionDialog(["repeat"])
    dlg._name_edit.setText("repeat")
    dlg._on_ok()

    assert dlg._err_lbl.isVisibleTo(dlg)
    assert dlg._result is None
    dlg.deleteLater()


def test_the_dialog_offers_both_answers_for_a_bool(qapp):
    """Under a field captioned "Default", a lone tick box reads as True-or-nothing."""
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    dlg = _AddOptionDialog([])
    dlg._name_edit.setText("Silly option")

    assert isinstance(dlg._default_widgets["bool"], BoolChoice)
    assert dlg._default_widgets["bool"].value() is False

    dlg._on_ok()
    assert dlg.get_option().default is False
    dlg.deleteLater()


def test_a_new_option_never_gets_a_null_declaration(qapp):
    """Null means "use the level below", and a hand-added option has none."""
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    for opt_type in OPTION_TYPES:
        dlg = _AddOptionDialog([])
        dlg._name_edit.setText("Silly option")
        dlg._select_type(opt_type)
        dlg._on_ok()

        assert dlg.get_option().default is not None
        dlg.deleteLater()


def test_editing_a_null_declaration_does_not_promote_it_to_a_value(qapp):
    """Opening Edit to fix a description must not change what the option resolves to."""
    opt = BotOptionModel(name="Safety Delay", type="float", default=None, description="old")
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    dlg = _AddOptionDialog(["Safety Delay"], existing_option=opt)
    dlg._desc_edit.setText("new")
    dlg._on_ok()

    assert dlg.get_option().default is None
    assert dlg.get_option().description == "new"
    dlg.deleteLater()


def test_a_null_bool_shows_as_neither_answer(qapp):
    """The radio pair has a spare state, so the null is visible and not just kept."""
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    opt = BotOptionModel(name="repeat", type="bool", default=None)
    dlg = _AddOptionDialog(["repeat"], existing_option=opt)

    assert dlg._default_widgets["bool"].value() is None
    dlg.deleteLater()


def test_touching_the_default_of_a_null_declaration_replaces_it(qapp):
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog

    opt = BotOptionModel(name="repeat", type="bool", default=None)
    dlg = _AddOptionDialog(["repeat"], existing_option=opt)

    dlg._default_widgets["bool"].set_value(True)
    dlg._default_touched = True  # what a click on the radio does
    dlg._on_ok()

    assert dlg.get_option().default is True
    dlg.deleteLater()
