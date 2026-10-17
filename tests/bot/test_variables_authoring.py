"""bartlebot.bot.variables, the half the editors use (#187): what a row may offer, how an edge's
condition reads on the canvas, and what a rename or a delete has to find.

No Qt: the widgets that call these are tested in test_variables_section.py and
test_variables_panel.py.
"""

from __future__ import annotations

import pytest

from bartlebot.bot import variables
from bartlebot.bot.schema import (
    BotConfig,
    BotOptionModel,
    FlagCondition,
    FlagGroup,
    StateModel,
    TransitionModel,
    VariableModel,
)

SWORDS = VariableModel(name="swords", type="int")
STAGE = VariableModel(name="stage", type="enum", values=["Sun", "Moon", "Star"])


def _is(variable: str, value, op: str = "==") -> FlagCondition:
    return FlagCondition(variable=variable, compare=op, value=value)


def _bot() -> BotConfig:
    """Set writes both variables; Which reads swords on two edges and stage on one, in both condition forms."""
    return BotConfig(
        id="bot-a",
        name="Authoring",
        initial="set",
        recovery_state="set",
        options=[BotOptionModel(name="Fast", type="bool", default=True)],
        variables=[SWORDS, STAGE],
        states=[
            StateModel(
                id="set",
                node_type="set_variables",
                label="Set",
                params={
                    "assignments": [
                        {"variable": "swords", "op": "add", "value": 1},
                        {"variable": "stage", "op": "set", "value": "Moon"},
                    ]
                },
            ),
            StateModel(id="which", node_type="branch", label="Which"),
            StateModel(id="one", node_type="sleep", label="One"),
            StateModel(id="two", node_type="sleep", label="Two"),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="set", dest="which", when="SUCCESS"),
            TransitionModel(trigger="advance", source="which", dest="one", when="SUCCESS", flags=[_is("swords", 1)]),
            TransitionModel(
                trigger="advance",
                source="which",
                dest="two",
                when="SUCCESS",
                flag_groups=[FlagGroup(flags=[FlagCondition(option="Fast"), _is("swords", 2, ">=")])],
            ),
            TransitionModel(trigger="advance", source="one", dest="set", flags=[_is("stage", "Sun")]),
        ],
    )


# ---------------------------------------------------------------------------
# What a row offers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("var_type", "compares", "operations"),
    [
        ("int", ("==", "!=", "<", "<=", ">", ">="), ("set", "add")),
        ("bool", ("==", "!="), ("set", "toggle")),
        ("enum", ("==", "!="), ("set",)),
    ],
)
def test_a_row_offers_only_what_its_variables_type_takes(var_type, compares, operations):
    assert variables.comparisons_for(var_type) == compares
    assert variables.operations_for(var_type) == operations


def test_every_comparison_offered_is_one_validation_accepts():
    """The editor and the check agree: nothing offered on a bool or an enum orders."""
    for var_type in ("bool", "enum"):
        assert not set(variables.comparisons_for(var_type)) & variables.ORDERING_COMPARES


# ---------------------------------------------------------------------------
# An edge's condition as text
# ---------------------------------------------------------------------------


def test_an_edge_without_a_condition_has_no_text():
    assert variables.describe_guard(TransitionModel(trigger="advance", source="a", dest="b")) is None


def test_flat_rows_read_joined_by_their_operator():
    t = TransitionModel(
        trigger="advance", source="a", dest="b", flags=[FlagCondition(option="Fast", negate=True), _is("swords", 2)]
    )
    assert variables.describe_guard(t) == "NOT Fast AND swords == 2"


def test_groups_are_bracketed_only_when_there_are_several():
    one = TransitionModel(
        trigger="advance", source="a", dest="b", flag_groups=[FlagGroup(flags=[_is("swords", 1), _is("stage", "Sun")])]
    )
    assert variables.describe_guard(one) == "swords == 1 AND stage == Sun"

    two = TransitionModel(
        trigger="advance",
        source="a",
        dest="b",
        flag_groups=[
            FlagGroup(flags=[_is("swords", 1), _is("stage", "Sun")], op="OR"),
            FlagGroup(flags=[FlagCondition(option="Fast")]),
        ],
        flag_groups_op="AND",
    )
    assert variables.describe_guard(two) == "(swords == 1 OR stage == Sun) AND Fast"


def test_the_groups_win_over_stale_flat_rows_as_they_do_at_run_time():
    t = TransitionModel(
        trigger="advance",
        source="a",
        dest="b",
        flags=[FlagCondition(option="Stale")],
        flag_groups=[FlagGroup(flags=[_is("swords", 3, "<")])],
    )
    assert variables.describe_guard(t) == "swords < 3"


def test_a_long_condition_is_shortened_and_a_short_one_is_not():
    assert variables.shorten("swords == 2") == "swords == 2"
    long = "Defeat Brady the Sneak AND stage == Sun Chamber"
    short = variables.shorten(long)
    assert len(short) <= variables.EDGE_LABEL_CHARS
    assert short.endswith("…")
    assert long.startswith(short[:-1])


# ---------------------------------------------------------------------------
# Uses, rename, names
# ---------------------------------------------------------------------------


def test_uses_names_every_edge_that_reads_and_every_node_that_writes():
    found = variables.uses(_bot(), "swords")
    assert [u.text for u in found] == [
        "the edge from 'Which' to 'One' on SUCCESS",
        "the edge from 'Which' to 'Two' on SUCCESS",
        "the node 'Set'",
    ]
    assert [u.states for u in found] == [("which",), ("which",), ("set",)]


def test_a_variable_nothing_uses_has_no_uses():
    config = _bot()
    config.variables.append(VariableModel(name="spare", type="bool"))
    assert variables.uses(config, "spare") == []


def test_uses_narrowed_to_values_finds_only_the_rows_holding_one():
    config = _bot()
    assert [u.text for u in variables.uses(config, "stage", values=["Sun"])] == ["the edge from 'One' to 'Set'"]
    assert [u.text for u in variables.uses(config, "stage", values=["Moon"])] == ["the node 'Set'"]
    assert variables.uses(config, "stage", values=["Star"]) == []


def test_rename_carries_every_condition_and_assignment():
    config = _bot()
    variables.rename(config, "swords", "blades")

    rows = [fc for t in config.transitions for fc in variables.condition_rows(t) if fc.is_variable]
    assert sorted(fc.variable for fc in rows) == ["blades", "blades", "stage"]
    assert [r["variable"] for r in config.states[0].params["assignments"]] == ["blades", "stage"]
    assert variables.uses(config, "swords") == []
    # The declaration is the caller's: a rename of references alone leaves it.
    assert [v.name for v in config.variables] == ["swords", "stage"]


def test_rename_leaves_a_row_that_is_not_a_row_alone():
    config = _bot()
    config.states[0].params["assignments"].append("garbage")
    variables.rename(config, "swords", "blades")
    assert config.states[0].params["assignments"][-1] == "garbage"


@pytest.mark.parametrize(
    ("name", "current", "expected"),
    [
        ("", None, "A variable needs a name."),
        ("   ", None, "A variable needs a name."),
        ("swords", None, "There is already a variable named 'swords'."),
        ("swords", "swords", None),
        ("Fast", None, "There is already an option named 'Fast'."),
        ("rack", None, None),
    ],
)
def test_a_name_is_refused_when_blank_or_taken(name, current, expected):
    problem = variables.name_problem(_bot(), name, current=current)
    if expected is None:
        assert problem is None
    else:
        assert problem is not None and problem.startswith(expected)
