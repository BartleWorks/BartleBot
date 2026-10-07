"""The Variables section on the Edit-mode Options tab, and its Add / Edit dialog (#187).

The dialog is driven by replacing its ``exec`` with a function that fills the fields and
presses Save, which is how test_options_section.py drives the option dialog.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QMessageBox, QSpinBox

from bartlebot import variables
from bartlebot.bot.schema import (
    BotConfig,
    BotOptionModel,
    FlagCondition,
    StateModel,
    TransitionModel,
    VariableModel,
)
from bartlebot.gui.sidebar.sections.variables_section import VariableDialog, VariablesSection, describe_declaration


def _bot() -> BotConfig:
    """swords is read on one edge and written by one node; spare is used by nothing."""
    return BotConfig(
        id="bot-vs",
        name="Variables section",
        initial="set",
        recovery_state="set",
        options=[BotOptionModel(name="Fast", type="bool", default=True)],
        variables=[
            VariableModel(name="swords", type="int"),
            VariableModel(name="stage", type="enum", values=["Sun", "Moon"], description="Which chamber is next."),
            VariableModel(name="spare", type="bool"),
        ],
        states=[
            StateModel(
                id="set",
                node_type="set_variables",
                label="Set",
                params={"assignments": [{"variable": "swords", "op": "add", "value": 1}]},
            ),
            StateModel(id="rack", node_type="sleep", label="Rack"),
        ],
        transitions=[
            TransitionModel(
                trigger="advance",
                source="set",
                dest="rack",
                when="SUCCESS",
                flags=[FlagCondition(variable="swords", compare="==", value=2)],
            ),
            TransitionModel(
                trigger="advance",
                source="rack",
                dest="set",
                flags=[FlagCondition(variable="stage", compare="==", value="Moon")],
            ),
        ],
    )


def _section(config: BotConfig) -> tuple[VariablesSection, list[str]]:
    section = VariablesSection()
    section.set_config(config)
    section.set_editable(True)
    steps: list[str] = []
    section.edit_starting.connect(steps.append)
    return section, steps


def _drive(monkeypatch, fill):
    """Make the next dialog's exec call ``fill(dialog)`` and report what the dialog ended with."""

    def _exec(self):
        fill(self)
        return self.result()

    monkeypatch.setattr(VariableDialog, "exec", _exec)


def _refusals(monkeypatch) -> list[str]:
    """Answer every message box with OK, and keep what each said."""
    said: list[str] = []

    def _exec(self):
        said.append(f"{self.text()}\n{self.informativeText()}")
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", _exec)
    return said


# ---------------------------------------------------------------------------
# The rows
# ---------------------------------------------------------------------------


def test_each_variable_is_a_row_naming_its_type_and_start(qapp, parentless_shows):
    section, _ = _section(_bot())
    assert section.row_names == ["swords", "stage", "spare"]
    assert describe_declaration(VariableModel(name="swords", type="int")) == "Whole number · starts at 0"
    assert describe_declaration(VariableModel(name="found", type="bool", initial=True)) == "Yes / No · starts at true"
    assert (
        describe_declaration(VariableModel(name="stage", type="enum", values=["Sun", "Moon"]))
        == "One of a list · starts at Sun"
    )
    assert parentless_shows == []


def test_the_edit_buttons_follow_set_editable(qapp):
    section, _ = _section(_bot())
    section.set_editable(False)
    assert not section.edit_button("swords").isVisibleTo(section)
    section.set_editable(True)
    assert section.edit_button("swords").isVisibleTo(section)


def test_a_bot_without_variables_says_what_one_is(qapp):
    config = _bot()
    config.variables = []
    section, _ = _section(config)
    assert section.row_names == []


# ---------------------------------------------------------------------------
# Add and edit
# ---------------------------------------------------------------------------


def test_adding_a_variable_declares_it_once_the_undo_step_is_named(qapp, monkeypatch):
    config = _bot()
    section, steps = _section(config)
    seen_before: list[int] = []
    section.edit_starting.connect(lambda _d: seen_before.append(len(config.variables)))

    def fill(dlg):
        dlg._name_edit.setText("next_spot")
        dlg._type_combo.setCurrentIndex(dlg._type_combo.findData("int"))
        dlg.initial_field.control.setValue(1)
        dlg._on_ok()

    _drive(monkeypatch, fill)
    section.add_variable()

    assert config.variables[-1] == VariableModel(name="next_spot", type="int", initial=1)
    assert steps == ["Add variable next_spot"]
    assert seen_before == [3], "the undo snapshot is taken before the config changes"
    assert section.row_names[-1] == "next_spot"


def test_a_start_that_is_the_types_own_is_left_out_of_the_file(qapp, monkeypatch):
    config = _bot()
    section, _ = _section(config)

    def fill(dlg):
        dlg._name_edit.setText("found")
        dlg._on_ok()

    _drive(monkeypatch, fill)
    section.add_variable()
    assert config.variables[-1] == VariableModel(name="found", type="bool")
    assert "initial" not in config.variables[-1].model_dump(exclude_none=True)


def test_an_enum_lists_its_values_and_starts_at_one_of_them(qapp, monkeypatch):
    config = _bot()
    section, _ = _section(config)

    def fill(dlg):
        dlg._name_edit.setText("chamber")
        dlg._type_combo.setCurrentIndex(dlg._type_combo.findData("enum"))
        dlg._values_edit.setPlainText("Sun Chamber\n\n  Moon Chamber  \nStar Chamber")
        combo = dlg.initial_field.control
        assert [combo.itemText(i) for i in range(combo.count())] == ["Sun Chamber", "Moon Chamber", "Star Chamber"]
        combo.setCurrentText("Moon Chamber")
        dlg._on_ok()

    _drive(monkeypatch, fill)
    section.add_variable()
    assert config.variables[-1] == VariableModel(
        name="chamber", type="enum", values=["Sun Chamber", "Moon Chamber", "Star Chamber"], initial="Moon Chamber"
    )


@pytest.mark.parametrize(
    ("var_type", "control"),
    [("bool", QCheckBox), ("int", QSpinBox), ("enum", QComboBox)],
)
def test_starts_at_is_the_control_for_the_type(qapp, var_type, control):
    dlg = VariableDialog(_bot())
    dlg._values_edit.setPlainText("A\nB")
    dlg._type_combo.setCurrentIndex(dlg._type_combo.findData(var_type))
    assert isinstance(dlg.initial_field.control, control)
    assert dlg._values_edit.isVisibleTo(dlg) == (var_type == "enum")


@pytest.mark.parametrize(
    ("name", "error"),
    [("", "A variable needs a name."), ("spare", "already a variable"), ("Fast", "already an option")],
)
def test_the_dialog_refuses_a_name_that_is_blank_or_taken(qapp, name, error):
    dlg = VariableDialog(_bot())
    dlg._name_edit.setText(name)
    dlg._on_ok()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert error in dlg._err_lbl.text()


def test_the_dialog_refuses_an_enum_with_no_values_or_a_value_twice(qapp):
    dlg = VariableDialog(_bot())
    dlg._name_edit.setText("chamber")
    dlg._type_combo.setCurrentIndex(dlg._type_combo.findData("enum"))
    dlg._on_ok()
    assert "at least one value" in dlg._err_lbl.text()
    dlg._values_edit.setPlainText("Sun\nSun")
    dlg._on_ok()
    assert "once" in dlg._err_lbl.text()
    assert dlg.result() != QDialog.DialogCode.Accepted


def test_a_rename_carries_every_condition_and_assignment(qapp, monkeypatch):
    config = _bot()
    section, steps = _section(config)

    def fill(dlg):
        dlg._name_edit.setText("blades")
        dlg._on_ok()

    _drive(monkeypatch, fill)
    section.edit_variable("swords")

    assert [v.name for v in config.variables] == ["blades", "stage", "spare"]
    assert config.transitions[0].flags[0].variable == "blades"
    assert config.states[0].params["assignments"][0]["variable"] == "blades"
    assert variables.uses(config, "swords") == []
    assert steps == ["Rename variable swords"]
    assert variables.check(config).errors == []


def test_a_type_change_is_refused_while_the_variable_is_used(qapp):
    config = _bot()
    dlg = VariableDialog(config, existing=config.variables[0])
    dlg._type_combo.setCurrentIndex(dlg._type_combo.findData("bool"))
    dlg._on_ok()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert "the edge from 'Set' to 'Rack' on SUCCESS" in dlg._err_lbl.text()
    assert "the node 'Set'" in dlg._err_lbl.text()


def test_an_unused_variable_can_change_type(qapp, monkeypatch):
    config = _bot()
    section, _ = _section(config)

    def fill(dlg):
        dlg._type_combo.setCurrentIndex(dlg._type_combo.findData("int"))
        dlg._on_ok()

    _drive(monkeypatch, fill)
    section.edit_variable("spare")
    assert config.variables[2] == VariableModel(name="spare", type="int")


def test_removing_an_enum_value_a_row_uses_is_refused_and_one_nothing_uses_is_not(qapp):
    config = _bot()
    dlg = VariableDialog(config, existing=config.variables[1])
    dlg._values_edit.setPlainText("Sun")
    dlg._on_ok()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert "Moon" in dlg._err_lbl.text() and "the edge from 'Rack' to 'Set'" in dlg._err_lbl.text()

    dlg._values_edit.setPlainText("Moon\nStar")
    dlg._on_ok()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.result_model().values == ["Moon", "Star"]
    assert dlg.result_model().description == "Which chamber is next."


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_deleting_a_variable_in_use_is_refused_and_names_each_use(qapp, monkeypatch):
    config = _bot()
    said = _refusals(monkeypatch)
    dlg = VariableDialog(config, existing=config.variables[0])
    dlg._on_delete()

    assert not dlg.was_deleted
    assert len(said) == 1
    assert "'swords' is used" in said[0]
    assert "• the edge from 'Set' to 'Rack' on SUCCESS" in said[0]
    assert "• the node 'Set'" in said[0]
    assert [v.name for v in config.variables] == ["swords", "stage", "spare"]


def test_deleting_a_variable_nothing_uses_removes_it(qapp, monkeypatch):
    config = _bot()
    section, steps = _section(config)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    _drive(monkeypatch, lambda dlg: dlg._on_delete())
    section.edit_variable("spare")

    assert [v.name for v in config.variables] == ["swords", "stage"]
    assert steps == ["Delete variable spare"]
    assert section.row_names == ["swords", "stage"]


def test_declining_the_confirmation_deletes_nothing(qapp, monkeypatch):
    config = _bot()
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
    dlg = VariableDialog(config, existing=config.variables[2])
    dlg._on_delete()
    assert not dlg.was_deleted
    assert dlg.result() != QDialog.DialogCode.Accepted


def test_the_dialog_offers_delete_only_when_editing(qapp):
    assert VariableDialog(_bot())._delete_btn is None
    assert VariableDialog(_bot(), existing=_bot().variables[0])._delete_btn is not None


# ---------------------------------------------------------------------------
# On the tab
# ---------------------------------------------------------------------------


def test_the_section_sits_under_options_on_the_edit_mode_tab_and_follows_the_config(qapp, monkeypatch):
    from PyQt6.QtWidgets import QWidget

    from bartlebot.gui.sidebar.left_sidebar import LeftSidebar

    sidebar = LeftSidebar(QWidget())
    config = _bot()
    sidebar.set_config(config)
    sidebar.set_editable(True)
    changed: list[bool] = []
    steps: list[str] = []
    sidebar.config_changed.connect(lambda: changed.append(True))
    sidebar.edit_starting.connect(steps.append)
    declared: list[bool] = []
    sidebar.declarations_changed.connect(lambda: declared.append(True))

    assert sidebar.variables.row_names == ["swords", "stage", "spare"]
    page = sidebar.widget(0)
    assert sidebar.variables.isAncestorOf(sidebar.variables.row("swords"))
    assert page.isAncestorOf(sidebar.variables)

    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    _drive(monkeypatch, lambda dlg: dlg._on_delete())
    sidebar.variables.edit_variable("spare")
    assert changed and steps == ["Delete variable spare"]
    assert declared, "the Properties page is re-shown from the config after a declaration edit"

    # Undo hands back another config object; the section follows it.
    other = _bot()
    sidebar.sync_config(other, True)
    assert sidebar.variables.row_names == ["swords", "stage", "spare"]
