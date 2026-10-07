"""What the properties panel does with variables before Stage 2 gives them editors (#187).

Two guards and nothing more: a set_variables node's rows survive a save of its form, and
an edge's variable conditions survive an edit of its option rows. Both are edited in the
bot file until then, so a panel that dropped them would lose the file's work in silence.
"""

from bartlebot.bot.schema import FlagCondition, FlagGroup
from bartlebot.gui.canvas.param_panel import _DynamicParamForm, _FlagConditionsWidget, _HeldParam
from bartlebot.nodes.utility.set_variables import PARAM_SCHEMA

ROWS = [{"variable": "swords", "op": "add", "value": 1}, {"variable": "next_spot", "op": "set", "value": 4}]


def test_a_set_variables_form_hands_its_rows_back_unchanged(qapp, parentless_shows):
    form = _DynamicParamForm()
    form.load_schema(PARAM_SCHEMA, {"assignments": ROWS})

    held = form.widget("assignments")
    assert isinstance(held, _HeldParam)
    assert held.text() == "swords + 1\nnext_spot = 4"
    assert form.get_params() == {"assignments": ROWS}
    assert parentless_shows == []


def test_a_type_the_form_cannot_draw_keeps_its_value():
    from bartlebot.gui.canvas.param_panel import _held_text

    assert _held_text("future_type", 3) == "(unsupported type: future_type)"
    assert _held_text("assignments", []) == "(none yet: add rows in the bot file)"
    assert _held_text("assignments", [{"op": "set"}]) == "(not readable: fix the rows in the bot file)"


def test_an_edge_keeps_its_variable_rows_through_an_edit_of_its_option_rows(qapp, parentless_shows):
    widget = _FlagConditionsWidget()
    widget.set_flag_groups(
        [
            FlagGroup(
                flags=[FlagCondition(option="Fast"), FlagCondition(variable="swords", compare=">=", value=2)], op="OR"
            ),
            FlagGroup(flags=[FlagCondition(variable="stage", compare="==", value="Moon")]),
        ],
        "AND",
        ["Fast", "Slow"],
    )

    # The option row's NOT is ticked, which is an edit; the variable rows ride through it.
    widget._groups[0][3][0][0].setChecked(True)
    groups, op = widget.get_flag_groups()

    assert op == "AND"
    assert [g.flags for g in groups] == [
        [FlagCondition(option="Fast", negate=True), FlagCondition(variable="swords", compare=">=", value=2)],
        [FlagCondition(variable="stage", compare="==", value="Moon")],
    ]
    assert parentless_shows == []


def test_removing_a_group_removes_its_variable_rows_with_it(qapp):
    widget = _FlagConditionsWidget()
    widget.set_flag_groups(
        [
            FlagGroup(flags=[FlagCondition(option="Fast")]),
            FlagGroup(flags=[FlagCondition(variable="swords", compare="==", value=1)]),
        ],
        "OR",
        ["Fast"],
    )
    frame, *_rest = widget._groups[1]
    widget._on_remove_group(frame, widget._groups[1])

    assert widget.get_flag_groups()[0] == [FlagGroup(flags=[FlagCondition(option="Fast")])]


def test_the_flat_form_keeps_them_too(qapp):
    widget = _FlagConditionsWidget()
    widget.set_conditions_flat([FlagCondition(variable="found", compare="==", value=True)], "AND", ["Fast"])
    assert widget.get_flag_groups()[0] == [FlagGroup(flags=[FlagCondition(variable="found", compare="==", value=True)])]
