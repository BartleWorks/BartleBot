"""The properties panel's variable editors (#187): an edge's condition rows, and a node's assignments.

A condition row picks an option or a variable from one grouped list, and a variable row
offers only the comparisons and values its type takes. The ``assignments`` param is
edited in rows of variable, op and value, the ops limited the same way. Neither editor
ever rewrites a row it only read, and a value the stored row holds that its type
refuses is handed back as it came.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QSpinBox

from bartlebot.bot.schema import FlagCondition, FlagGroup, VariableModel
from bartlebot.gui.canvas.param_panel import _DynamicParamForm, _FlagConditionsWidget, _HeldParam, _held_text
from bartlebot.gui.canvas.variable_rows import AssignmentsField
from bartlebot.gui.widgets.variable_widgets import NOT_A_VALUE, OPTION, VARIABLE, VariableValueField
from bartlebot.nodes.utility.set_variables import PARAM_SCHEMA

SWORDS = VariableModel(name="swords", type="int")
FOUND = VariableModel(name="found", type="bool")
STAGE = VariableModel(name="stage", type="enum", values=["Sun", "Moon", "Star"])
DECLS = [SWORDS, FOUND, STAGE]

ROWS = [{"variable": "swords", "op": "add", "value": 1}, {"variable": "next_spot", "op": "set", "value": 4}]


def _is(variable: str, value, op: str = "==") -> FlagCondition:
    return FlagCondition(variable=variable, compare=op, value=value)


def _items(combo: QComboBox) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def _conditions(*flags: FlagCondition, options=("Fast", "Slow"), decls=DECLS) -> _FlagConditionsWidget:
    widget = _FlagConditionsWidget()
    widget.set_flag_groups([FlagGroup(flags=list(flags))], "OR", list(options), list(decls))
    return widget


# ---------------------------------------------------------------------------
# The value control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decl", "value", "control", "shown"),
    [
        (FOUND, True, QCheckBox, True),
        (SWORDS, 3, QSpinBox, 3),
        (STAGE, "Moon", QComboBox, "Moon"),
    ],
)
def test_a_value_is_edited_in_the_control_its_type_calls_for(qapp, decl, value, control, shown):
    field = VariableValueField()
    field.set_declaration(decl, value)
    assert isinstance(field.control, control)
    assert field.value() == shown


def test_an_enums_control_offers_its_values_and_nothing_else(qapp):
    field = VariableValueField()
    field.set_declaration(STAGE, "Sun")
    assert _items(field.control) == ["Sun", "Moon", "Star"]
    assert not field.control.isEditable()


@pytest.mark.parametrize(("decl", "stored"), [(STAGE, "Comet"), (SWORDS, "Sun"), (FOUND, 1)])
def test_a_stored_value_the_type_refuses_is_shown_blank_and_kept_until_one_is_picked(qapp, decl, stored):
    field = VariableValueField()
    field.set_declaration(decl, stored)
    assert field.holds_a_stored_value
    assert field.value() == stored
    control = field.control
    if isinstance(control, QComboBox):
        assert control.currentIndex() == -1 and _items(control) == ["Sun", "Moon", "Star"]
        control.setCurrentIndex(1)
        assert field.value() == "Moon"
    elif isinstance(control, QSpinBox):
        assert control.text() == NOT_A_VALUE
        control.stepUp()
        assert field.value() == control.value() and control.minimum() == -999_999
    else:
        assert control.checkState() == Qt.CheckState.PartiallyChecked
        control.click()
        assert field.value() in (True, False) and not control.isTristate()
    assert not field.holds_a_stored_value


def test_a_variable_the_bot_does_not_declare_keeps_its_value_as_text(qapp):
    field = VariableValueField()
    field.set_declaration(None, 4)
    assert isinstance(field.control, QLabel)
    assert field.value() == 4


# ---------------------------------------------------------------------------
# Condition rows
# ---------------------------------------------------------------------------


def test_options_and_variables_are_picked_from_one_grouped_list(qapp, parentless_shows):
    widget = _conditions(FlagCondition(option="Fast"))
    row = widget.rows[0]
    assert row.source.sources() == [
        (OPTION, "Fast"),
        (OPTION, "Slow"),
        (VARIABLE, "swords"),
        (VARIABLE, "found"),
        (VARIABLE, "stage"),
    ]
    assert _items(row.source) == ["Options", "Fast", "Slow", "Variables", "swords", "found", "stage"]
    assert parentless_shows == []


def test_an_option_row_is_not_or_not_and_has_no_comparison(qapp):
    row = _conditions(FlagCondition(option="Slow", negate=True)).rows[0]
    assert row.not_box.isVisibleTo(row) and row.not_box.isChecked()
    assert not row.compare.isVisibleTo(row) and not row.value.isVisibleTo(row)
    assert row.condition() == FlagCondition(option="Slow", negate=True)


@pytest.mark.parametrize(
    ("decl", "compares"),
    [(SWORDS, ["==", "!=", "<", "<=", ">", ">="]), (FOUND, ["==", "!="]), (STAGE, ["==", "!="])],
)
def test_a_variable_row_offers_only_the_comparisons_its_type_takes(qapp, decl, compares):
    row = _conditions(_is(decl.name, decl.initial_value())).rows[0]
    assert not row.not_box.isVisibleTo(row)
    assert _items(row.compare) == compares


def test_picking_a_variable_resets_the_row_to_a_value_its_type_holds(qapp):
    widget = _conditions(FlagCondition(option="Fast"))
    row = widget.rows[0]
    changed: list[bool] = []
    widget.changed.connect(lambda: changed.append(True))

    row.source.setCurrentIndex(row.source.findText("stage"))
    assert row.condition() == _is("stage", "Sun")
    assert isinstance(row.value.control, QComboBox)

    row.source.setCurrentIndex(row.source.findText("swords"))
    row.compare.setCurrentText(">=")
    row.value.control.setValue(2)
    assert row.condition() == _is("swords", 2, ">=")
    assert changed


def test_switching_from_an_int_to_a_bool_drops_an_ordering_comparison(qapp):
    row = _conditions(_is("swords", 2, ">=")).rows[0]
    row.source.setCurrentIndex(row.source.findText("found"))
    assert row.compare.currentText() == "=="
    assert row.condition() == _is("found", False)


def test_rows_read_back_exactly_as_they_were_given(qapp):
    groups = [
        FlagGroup(flags=[FlagCondition(option="Fast"), _is("swords", 2, ">=")], op="OR"),
        FlagGroup(flags=[_is("stage", "Moon", "!="), _is("found", True)]),
    ]
    widget = _FlagConditionsWidget()
    widget.set_flag_groups(groups, "AND", ["Fast", "Slow"], DECLS)
    assert widget.get_flag_groups() == (groups, "AND")


def test_an_edit_to_an_option_row_leaves_the_variable_rows_as_they_were(qapp):
    widget = _conditions(FlagCondition(option="Fast"), _is("swords", 2, ">="))
    widget.rows[0].not_box.setChecked(True)
    groups, _op = widget.get_flag_groups()
    assert groups[0].flags == [FlagCondition(option="Fast", negate=True), _is("swords", 2, ">=")]


def test_a_row_on_an_undeclared_variable_is_listed_and_kept(qapp):
    widget = _conditions(_is("ghost", 3, "<"), decls=[SWORDS])
    row = widget.rows[0]
    assert (VARIABLE, "ghost") in row.source.sources()
    assert row.compare.currentText() == "<"
    assert widget.get_flag_groups()[0][0].flags == [_is("ghost", 3, "<")]


def test_a_stale_enum_value_is_kept_and_not_offered(qapp):
    widget = _conditions(_is("stage", "Comet"))
    row = widget.rows[0]
    assert "Comet" not in _items(row.value.control)
    assert widget.get_flag_groups()[0][0].flags == [_is("stage", "Comet")]


def test_add_condition_picks_the_first_thing_listed(qapp):
    widget = _conditions(options=(), decls=[STAGE])
    row = widget.add_condition()
    assert row.condition() == _is("stage", "Sun")


def test_removing_a_group_removes_its_rows_with_it(qapp):
    widget = _FlagConditionsWidget()
    widget.set_flag_groups(
        [FlagGroup(flags=[FlagCondition(option="Fast")]), FlagGroup(flags=[_is("swords", 1)])], "OR", ["Fast"], DECLS
    )
    frame, *_rest = widget._groups[1]
    widget._on_remove_group(frame, widget._groups[1])
    assert widget.get_flag_groups()[0] == [FlagGroup(flags=[FlagCondition(option="Fast")])]


def test_the_flat_form_loads_variable_rows_too(qapp):
    widget = _FlagConditionsWidget()
    widget.set_conditions_flat([_is("found", True)], "AND", ["Fast"], DECLS)
    assert widget.get_flag_groups()[0] == [FlagGroup(flags=[_is("found", True)])]


# ---------------------------------------------------------------------------
# The assignments editor
# ---------------------------------------------------------------------------


def _form(rows, decls=DECLS) -> _DynamicParamForm:
    form = _DynamicParamForm()
    form.set_variables_getter(lambda: list(decls))
    form.load_schema(PARAM_SCHEMA, {"assignments": rows})
    return form


def test_a_set_variables_node_edits_its_rows_and_hands_them_back_unchanged(qapp, parentless_shows):
    rows = [{"variable": "swords", "op": "add", "value": 1}, {"variable": "stage", "op": "set", "value": "Moon"}]
    form = _form(rows)
    field = form.widget("assignments")
    assert isinstance(field, AssignmentsField)
    assert form.get_params() == {"assignments": rows}
    assert parentless_shows == []


@pytest.mark.parametrize(
    ("decl", "ops"),
    [(SWORDS, ["=", "+"]), (FOUND, ["=", "toggle"]), (STAGE, ["="])],
)
def test_a_row_offers_only_the_writes_its_type_takes(qapp, decl, ops):
    field = _form([{"variable": decl.name, "op": "set", "value": decl.initial_value()}]).widget("assignments")
    assert _items(field.rows[0].op) == ops


def test_a_toggle_has_no_value_and_is_stored_without_one(qapp):
    form = _form([{"variable": "found", "op": "set", "value": True}])
    row = form.widget("assignments").rows[0]
    row.op.setCurrentIndex(row.op.findData("toggle"))
    assert not row.value.isVisibleTo(row)
    assert form.get_params() == {"assignments": [{"variable": "found", "op": "toggle"}]}


def test_an_add_is_a_whole_number_and_starts_at_one(qapp):
    form = _form([{"variable": "swords", "op": "set", "value": 0}])
    row = form.widget("assignments").rows[0]
    row.op.setCurrentIndex(row.op.findData("add"))
    assert isinstance(row.value.control, QSpinBox)
    assert form.get_params() == {"assignments": [{"variable": "swords", "op": "add", "value": 1}]}


def test_add_assignment_writes_the_first_variable_and_a_change_reaches_the_form(qapp):
    form = _form([])
    changed: list[bool] = []
    form.changed.connect(lambda: changed.append(True))
    field = form.widget("assignments")
    row = field.add_row()
    row.variable.setCurrentText("stage")
    row.value.control.setCurrentText("Star")
    assert form.get_params() == {"assignments": [{"variable": "stage", "op": "set", "value": "Star"}]}
    assert changed

    row.remove_btn.click()
    assert form.get_params() == {"assignments": []}


def test_with_no_variables_declared_it_says_where_to_declare_one(qapp):
    field = _form([], decls=[]).widget("assignments")
    assert field.hint.isVisibleTo(field)
    assert not field.add_btn.isEnabled()
    assert field.add_row() is None


def test_a_row_naming_an_undeclared_variable_is_kept(qapp):
    form = _form(ROWS)
    assert form.get_params() == {"assignments": ROWS}


def test_rows_that_are_not_rows_are_held_as_they_came(qapp):
    form = _form([{"op": "set"}])
    assert isinstance(form.widget("assignments"), _HeldParam)
    assert form.get_params() == {"assignments": [{"op": "set"}]}
    assert _held_text("assignments", [{"op": "set"}]) == "(not readable: fix the rows in the bot file)"
    assert _held_text("future_type", 3) == "(unsupported type: future_type)"
