"""The two option surfaces: values in Option Values, declarations in Options.

``OptionsSection`` shipped untested, which is how two bugs survived in it long
enough to be found by reading rather than by running: the rename fixup walked
only the *flat* ``flags`` list while the edge editor writes ``flag_groups`` — the
form that takes precedence at run time — and the Add/Edit dialog hardcoded
``type="bool"``, so pressing Edit on Safety Delay rewrote a float as a checkbox.

The assertion that matters most is the negative one: editing a value in Option Values
must leave the bot file byte-identical.  That is the whole epic in one test.

Real widget tests (QApplication required), in the style of
``test_canvas_property_edits``.  The settings store is injected rather than
patched — both sections take one — so nothing here touches the real
``settings.json``.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)

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
from bartlebot.gui.sidebar.sections.option_values_section import OptionValuesSection
from bartlebot.gui.sidebar.sections.options_section import OptionsSection, _AddOptionDialog
from bartlebot.gui.widgets.growing_text_edit import GrowingTextEdit
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


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A SettingsManager writing to tmp_path, isolated from the real singleton."""
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


def _config(options=None, transitions=None) -> BotConfig:
    """A bot whose one node requires no options, so every option here is authorial.

    ``sleep`` requires no options while its Use Safety Delay is off;
    most nodes pull in Safety Delay, which would put a derived row in every list.
    """
    return BotConfig(
        id="test-bot",
        name="Test Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="sleep", params={})],
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
# Option Values — the values surface
# ---------------------------------------------------------------------------


def test_editing_a_value_writes_an_override_and_not_the_declaration(qapp, settings):
    config = _config([_repeat(default=True)])
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)

    assert settings.get_option_override("test-bot", "repeat") is False
    assert config.options[0].default is True


def test_a_bot_file_is_untouched_by_a_value_edit(qapp, settings):
    """The epic in one assertion: tuning a shared bot must not modify it."""
    config = _config([_repeat(default=True)])
    before = config.model_dump()
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)

    assert config.model_dump() == before


def test_an_override_wins_over_the_declaration_in_the_row(qapp, settings):
    settings.set_option_override("test-bot", "repeat", False)
    config = _config([_repeat(default=True)])
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    assert section.option_widgets["repeat"].isChecked() is False
    assert section.resolution_for("repeat").source == bot_options.SOURCE_OVERRIDE


def test_a_row_with_no_override_reports_the_bot_as_its_source(qapp, settings):
    config = _config([_repeat(default=True)])
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    assert section.resolution_for("repeat").source == bot_options.SOURCE_BOT_FILE


def test_reset_removes_the_override_row(qapp, settings):
    settings.set_option_override("test-bot", "repeat", False)
    config = _config([_repeat(default=True)])
    section = OptionValuesSection(settings=settings)
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
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)
    assert settings.get_option_override("test-bot", "repeat") is False

    section.option_widgets["repeat"].setChecked(True)

    assert settings.get("bot_option_overrides") == {}
    assert section.resolution_for("repeat").source == bot_options.SOURCE_BOT_FILE


def test_a_value_edit_keeps_the_control_it_was_made_in(qapp, settings):
    """Rebuilding the list would delete the spin box under the arrow being clicked."""
    config = _config([BotOptionModel(name="Passes", type="int", default=1)])
    section = OptionValuesSection(settings=settings)
    section.set_config(config)
    before = section.option_widgets["Passes"]

    before.setValue(5)

    assert section.option_widgets["Passes"] is before
    assert settings.get_option_override("test-bot", "Passes") == 5


def test_resetting_puts_the_resolved_value_back_on_screen(qapp, settings):
    config = _config([BotOptionModel(name="Passes", type="int", default=1)])
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    section.option_widgets["Passes"].setValue(5)
    section._on_reset("Passes")

    assert section.option_widgets["Passes"].value() == 1
    assert settings.get("bot_option_overrides") == {}


def test_revert_is_enabled_exactly_while_the_value_is_the_users(qapp, settings):
    """Revert going live and the override bar going accent are one fact said twice,
    which is why they sit next to each other in the gutter."""
    config = _config([_repeat(default=True)])
    section = OptionValuesSection(settings=settings)
    section.set_config(config)
    chrome = section._chrome["repeat"]

    assert not chrome.revert_btn.isEnabled()

    section.option_widgets["repeat"].setChecked(False)
    assert chrome.revert_btn.isEnabled()
    assert section.resolution_for("repeat").source == bot_options.SOURCE_OVERRIDE

    section._on_reset("repeat")
    assert not chrome.revert_btn.isEnabled()


def test_a_false_override_is_a_choice_and_survives_a_rebuild(qapp, settings):
    """Absence is unset; a stored ``false`` is an answer. Nothing may prune it."""
    config = _config([_repeat(default=True)])  # so False really is the user's choice
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    section.option_widgets["repeat"].setChecked(False)
    section.set_config(config)

    assert settings.get_option_override("test-bot", "repeat") is False
    assert section.resolution_for("repeat").source == bot_options.SOURCE_OVERRIDE


def test_an_empty_list_override_is_a_choice_too(qapp, settings):
    config = _config([BotOptionModel(name="Players", type="player_list", default=[1, 2])])
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    section._on_value_changed("Players", [])

    assert settings.get_option_override("test-bot", "Players") == []
    assert section.resolution_for("Players").value == []


def test_setup_has_no_authoring_controls_to_reveal(qapp, settings):
    """``set_editable`` is accepted and ignored — values are a run-mode concern."""
    config = _config([_repeat()])
    section = OptionValuesSection(settings=settings)
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
    assert all(btn.isVisibleTo(section) for btn in section._edit_btns)

    section.set_editable(False)
    assert not section._add_option_btn.isVisibleTo(section)
    assert not any(btn.isVisibleTo(section) for btn in section._edit_btns)


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
    # Delete lives inside the Edit dialog now, so the one absent button covers both.
    assert section._edit_btns == []


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


# ---------------------------------------------------------------------------
# The gutter, and the prose behind it
# ---------------------------------------------------------------------------


def _gutter_children(row):
    """``[bar, action, help]`` — the three fixed slots at the head of a row."""
    layout = row.layout()
    return [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget() is not None][:3]


def test_the_gutter_slots_line_up_across_both_surfaces(qapp, settings):
    """Option Values and Options are two views of one list, so a row's chrome has to land at
    the same x on both — otherwise a mode switch reads as a different row."""
    config = _config([_repeat()])
    options = OptionsSection(settings=settings)
    options.set_config(config)
    setup = OptionValuesSection(settings=settings)
    setup.set_config(config)

    opt_row, setup_row = options._option_rows[0], setup._chrome["repeat"].outer

    assert [w.width() for w in _gutter_children(opt_row)] == [w.width() for w in _gutter_children(setup_row)]
    # And at the same x: both rows sit in the same card, at the same inset. Editable, as
    # Options always is when it is on screen -- it is only offered in Edit mode.
    options.set_editable(True)
    for section in (options, setup):
        section.resize(400, 600)
        section.show()
    qapp.processEvents()
    try:
        opt_x = [w.mapTo(options, w.rect().topLeft()).x() for w in _gutter_children(opt_row)]
        setup_x = [w.mapTo(setup, w.rect().topLeft()).x() for w in _gutter_children(setup_row)]
        assert opt_x == setup_x
    finally:
        options.hide()
        setup.hide()


def test_both_surfaces_draw_the_same_cards_and_share_their_folds(qapp, settings):
    """A mode switch must not move a row into a different place, or reopen a card the
    reader folded on the other tab."""
    config = BotConfig(
        id="test-bot",
        name="Test Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="enter_dungeon", params={"offers_challenge_mode": True})],
        transitions=[],
        options=[_repeat()],
    )
    options = OptionsSection(settings=settings)
    options.set_config(config)
    setup = OptionValuesSection(settings=settings)
    setup.set_config(config)

    setup.set_group_collapsed("dungeon", True)
    options.show()
    qapp.processEvents()
    try:
        assert list(options.cards) == ["dungeon", "team"]  # General held only the user's own options
        assert list(setup.cards) == ["dungeon", "team", "general"]
        assert options.cards["dungeon"].is_collapsed
    finally:
        options.hide()


def test_a_folded_options_card_summarizes_the_declarations(qapp, settings):
    section = OptionsSection(settings=settings)
    section.set_config(_config([_repeat(default=True)]))

    assert [part.text for part in section.cards["dungeon"].summary_parts] == ["repeat"]

    section.option_widgets["repeat"].setChecked(False)

    assert [part.text for part in section.cards["dungeon"].summary_parts] == ["Nothing turned on"]


def test_a_derived_option_keeps_its_edit_slot_empty_rather_than_closing_it(qapp, settings):
    """Only 24 of the library's 152 Options rows carry an Edit button. A gutter that
    closes up on the other 128 is not a column any more."""
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

    _bar, edit_btn, _help = _gutter_children(section._option_rows[0])

    assert not edit_btn.isVisibleTo(section)
    assert edit_btn.sizePolicy().retainSizeWhenHidden()


def test_a_description_is_folded_away_until_asked_for(qapp, settings):
    """Options showed every description unconditionally; Option Values has folded them since
    the prose was measured at 41% of a row's height."""
    section = OptionsSection(settings=settings)
    section.set_config(_config([_repeat()]))
    section.show()
    qapp.processEvents()
    try:
        assert not section._chrome["repeat"].description.isVisible()

        section._chrome["repeat"].help_btn.setChecked(True)

        assert section._chrome["repeat"].description.isVisible()
    finally:
        section.hide()


def test_folding_the_prose_away_is_remembered_for_both_surfaces(qapp, settings):
    """It is a property of the reader, not of the bot, so it is not set twice."""
    options = OptionsSection(settings=settings)
    options.set_config(_config([_repeat()]))

    options.set_descriptions_visible(True)

    setup = OptionValuesSection(settings=settings)
    setup.set_config(_config([_repeat()]))
    assert setup.descriptions_visible


def test_a_description_is_read_at_the_base_font(qapp, settings):
    """The same prose as on Option Values, so it is read at the same size."""
    section = OptionsSection(settings=settings)
    section.set_config(_config([_repeat()]))

    assert "font-size" not in section._chrome["repeat"].description.styleSheet()


# ---------------------------------------------------------------------------
# Delete moved into the dialog
# ---------------------------------------------------------------------------


def test_the_dialog_offers_delete_only_when_editing(qapp):
    """There is nothing to delete while adding, and Add is where a stray click on a
    button labeled Delete would be most surprising."""
    assert _AddOptionDialog([])._delete_btn is None
    assert _AddOptionDialog([], existing_option=_repeat())._delete_btn is not None


def test_deleting_from_the_dialog_accepts_without_a_result(qapp, monkeypatch):
    """An accepted dialog means the user finished, not that they saved — so the
    caller checks ``was_deleted`` before reaching for the option."""
    dlg = _AddOptionDialog([], existing_option=_repeat())
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)

    dlg._on_delete()

    assert dlg.was_deleted
    assert dlg.result() == QDialog.DialogCode.Accepted


def test_declining_the_confirmation_deletes_nothing(qapp, monkeypatch):
    dlg = _AddOptionDialog([], existing_option=_repeat())
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    dlg._on_delete()

    assert not dlg.was_deleted


def test_the_row_button_deletes_through_the_dialog(qapp, settings, monkeypatch):
    """The gutter's one button leads to both outcomes, which is what lets Delete off
    a 20px icon-only target a few pixels from the ``?``."""
    config = _config([_repeat()])
    section = OptionsSection(settings=settings)
    section.set_config(config)

    def _delete_it(self):
        self._deleted = True
        self.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_AddOptionDialog, "exec", _delete_it)
    section._on_edit_option("repeat")

    assert config.options == []
    assert "repeat" not in section.option_widgets


# ---------------------------------------------------------------------------
# The description field
# ---------------------------------------------------------------------------


def test_the_description_field_holds_more_than_one_line(qapp):
    """Not one of the 22 hand-added descriptions in the shipped library fitted the
    old single-line field: the median ran to 75 characters against roughly 45 shown."""
    dlg = _AddOptionDialog([])

    assert isinstance(dlg._desc_edit, GrowingTextEdit)
    assert dlg._desc_edit.line_bounds == (2, 6)


def test_a_multi_line_description_round_trips_through_the_dialog(qapp):
    prose = "Which players to check. Empty means no players. Shared by two nodes."
    dlg = _AddOptionDialog([], existing_option=BotOptionModel(name="x", type="bool", description=prose, default=True))

    dlg._name_edit.setText("x")
    dlg._on_ok()

    assert dlg.get_option().description == prose


# ---------------------------------------------------------------------------
# No window flashes while a row is built
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("descriptions_shown", [False, True])
def test_building_a_row_never_shows_a_parentless_widget(qapp, settings, descriptions_shown, parentless_shows):
    """The user's report: windows flashing on an option edit, worst on a check box.

    Every value edit rebuilds a list of rows, and each row set the visibility of its
    description and its Edit button *before* the gutter had parented them — one
    top-level window mapped and unmapped per row, per rebuild.
    """
    settings.set("general", "show_option_descriptions", value=descriptions_shown)
    config = _config([_repeat()])
    options = OptionsSection(settings=settings)
    options.set_editable(True)  # Edit mode: the button the rows would have shown
    options.set_config(config)
    OptionValuesSection(settings=settings).set_config(config)

    assert parentless_shows == []


def test_a_changed_default_does_not_rebuild_the_row_it_was_changed_in(qapp, settings):
    """A check box whose row is rebuilt on ``toggled`` is destroyed under the pointer.

    Option Values has followed this rule since the spin-box bug; Options did not, because a
    default edit went out on ``options_changed``, whose handler recomposes and
    rebuilds the whole list.  It is ``defaults_changed`` now — no name can change,
    so there is nothing here to rebuild.
    """
    section = OptionsSection(settings=settings)
    section.set_config(_config([_repeat()]))
    box = section.option_widgets["repeat"]
    structural = []
    defaults = []
    section.options_changed.connect(lambda: structural.append(True))
    section.defaults_changed.connect(lambda: defaults.append(True))

    box.setChecked(False)

    assert defaults == [True]
    assert structural == []
    assert section.option_widgets["repeat"] is box
    assert section._config.options[0].default is False


# ---------------------------------------------------------------------------
# The canvas has to hear about it
# ---------------------------------------------------------------------------


def test_deleting_an_option_undots_the_edges_that_guarded_on_it(qapp, settings):
    """An edge is dotted while it is conditional, and a deletion can end that.

    The flags come off the transitions, but an ``EdgeItem`` holds a snapshot of
    whether it had any, and the Options surface cannot reach the canvas to say
    otherwise -- so the sweep runs from the controller's ``config_changed``
    handler, which is what this wires up.
    """
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    transition = TransitionModel(
        trigger="advance",
        source="a",
        dest="a",
        when="SUCCESS",
        flag_groups=[FlagGroup(flags=[FlagCondition(option="repeat")])],
    )
    config = _config([_repeat()], transitions=[transition])
    canvas = BotCanvas()
    try:
        canvas.setup(config)
        canvas.enter_edit_mode()
        section = OptionsSection(settings=settings)
        section.set_config(config)
        section.options_changed.connect(canvas.refresh_edge_flags)
        edge = next(e for s, w, d, e in canvas.scene.edge_items if (s, w, d) == ("a", "SUCCESS", "a"))
        assert edge.has_flags

        section._on_delete_option("repeat")

        assert not edge.has_flags
    finally:
        canvas.prepare_for_shutdown()
