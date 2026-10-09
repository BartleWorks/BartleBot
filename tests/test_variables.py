"""bartlebot.bot.variables: comparisons, writes, and every error and warning a bot's variables can have.

No machine and no Qt: the run-time half is test_machine_variables.py.
"""

from __future__ import annotations

import pytest

from bartlebot.bot import variables
from bartlebot.bot.schema import (
    AssignmentModel,
    BotConfig,
    BotOptionModel,
    FlagCondition,
    FlagGroup,
    StateModel,
    TransitionModel,
    VariableModel,
)
from bartlebot.bot.variables import VariableError, apply_assignments, check, compare, run_refusal
from bartlebot.paths import project_root

SWORDS = VariableModel(name="swords", type="int")
FOUND = VariableModel(name="found", type="bool")
STAGE = VariableModel(name="stage", type="enum", values=["Sun", "Moon", "Star"])


def _state(sid: str, node_type: str = "sleep", **params) -> StateModel:
    return StateModel(id=sid, node_type=node_type, label=sid.title(), params=params)


def _set(sid: str, *rows: dict) -> StateModel:
    return _state(sid, "set_variables", assignments=list(rows))


def _edge(source: str, dest: str, when: str | None = "SUCCESS", *rows: FlagCondition, **extra) -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when=when, flags=list(rows), **extra)


def _is(variable: str, value, op: str = "==") -> FlagCondition:
    return FlagCondition(variable=variable, compare=op, value=value)


def _bot(states, transitions, decls=(SWORDS,), **extra) -> BotConfig:
    return BotConfig(
        id="bot-v",
        name="Variables",
        initial=states[0].id,
        recovery_state=states[0].id,
        states=states,
        transitions=transitions,
        variables=list(decls),
        **extra,
    )


def _clean_bot() -> BotConfig:
    """Start resets swords; Count adds one; Which branches on it, with an otherwise."""
    return _bot(
        [
            _set("start", {"variable": "swords", "op": "set", "value": 0}),
            _set("count", {"variable": "swords", "op": "add", "value": 1}),
            _state("which", "branch"),
            _state("rack_1"),
            _state("rack_2"),
        ],
        [
            _edge("start", "count"),
            _edge("count", "which"),
            _edge("which", "rack_1", "SUCCESS", _is("swords", 1)),
            _edge("which", "rack_2"),
            _edge("rack_1", "count"),
            _edge("rack_2", "start"),
        ],
    )


def _texts(problems) -> list[str]:
    return [p.text for p in problems]


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "op", "value", "expected"),
    [
        (2, "==", 2, True),
        (2, "==", 3, False),
        (2, "!=", 3, True),
        (2, "!=", 2, False),
        (2, "<", 3, True),
        (3, "<", 3, False),
        (3, "<=", 3, True),
        (4, "<=", 3, False),
        (4, ">", 3, True),
        (3, ">", 3, False),
        (3, ">=", 3, True),
        (2, ">=", 3, False),
        (True, "==", True, True),
        (False, "==", True, False),
        (False, "!=", True, True),
        ("Moon", "==", "Moon", True),
        ("Moon", "==", "Sun", False),
        ("Moon", "!=", "Sun", True),
    ],
)
def test_every_comparison_on_every_type(current, op, value, expected):
    assert compare(current, op, value) is expected


def test_a_bool_never_equals_an_int():
    assert compare(True, "==", 1) is False
    assert compare(1, "==", True) is False
    assert compare(0, "!=", False) is True


def test_an_ordering_comparison_on_a_bool_or_an_enum_is_never_true():
    assert compare(True, ">", False) is False
    assert compare("Sun", "<", "Moon") is False


def test_a_variable_that_does_not_exist_passes_no_row():
    assert compare(None, "==", 0) is False
    assert compare(None, "!=", 0) is False


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

DECLS = {d.name: d for d in (SWORDS, FOUND, STAGE)}
START = {"swords": 2, "found": False, "stage": "Sun"}


def test_rows_apply_in_order_and_a_later_row_sees_an_earlier_one():
    rows = [
        AssignmentModel(variable="swords", op="set", value=0),
        AssignmentModel(variable="swords", op="add", value=1),
        AssignmentModel(variable="found", op="toggle"),
        AssignmentModel(variable="stage", op="set", value="Moon"),
    ]
    assert apply_assignments(START, DECLS, rows) == {"swords": 1, "found": True, "stage": "Moon"}


def test_applying_rows_changes_nothing_it_is_given():
    values = dict(START)
    apply_assignments(values, DECLS, [AssignmentModel(variable="swords", op="add", value=5)])
    assert values == START


@pytest.mark.parametrize(
    "row",
    [
        AssignmentModel(variable="ghost", op="set", value=1),
        AssignmentModel(variable="swords", op="toggle"),
        AssignmentModel(variable="found", op="add", value=1),
        AssignmentModel(variable="swords", op="add", value=True),
        AssignmentModel(variable="swords", op="set", value="two"),
        AssignmentModel(variable="stage", op="set", value="Comet"),
        AssignmentModel(variable="found", op="set", value=1),
    ],
)
def test_a_write_the_variable_cannot_take_is_refused_before_anything_is_written(row):
    with pytest.raises(VariableError):
        apply_assignments(START, DECLS, [AssignmentModel(variable="swords", op="set", value=9), row])


def test_an_unset_initial_value_starts_each_type_at_its_blank():
    assert SWORDS.initial_value() == 0
    assert FOUND.initial_value() is False
    assert STAGE.initial_value() == "Sun"
    assert VariableModel(name="n", type="int", initial=4).initial_value() == 4


def test_rows_and_conditions_read_as_text():
    assert variables.describe_assignment(AssignmentModel(variable="next_spot", value=4)) == "next_spot = 4"
    assert variables.describe_assignment(AssignmentModel(variable="swords", op="add", value=1)) == "swords + 1"
    assert variables.describe_assignment(AssignmentModel(variable="found", op="toggle")) == "found toggled"
    assert variables.describe_condition(_is("swords", 2)) == "swords == 2"
    assert variables.describe_condition(_is("found", True, "!=")) == "found != true"
    assert variables.describe_condition(FlagCondition(option="Fast", negate=True)) == "NOT Fast"


def test_assignments_that_are_not_rows_do_not_parse():
    with pytest.raises(ValueError):
        variables.parse_assignments({"variable": "swords"})
    with pytest.raises(ValueError):
        variables.parse_assignments([{"op": "set", "value": 1}])
    assert variables.parse_assignments(None) == []


# ---------------------------------------------------------------------------
# Validation: a clean bot, and one that uses no variables
# ---------------------------------------------------------------------------


def test_a_bot_that_uses_its_variables_well_has_nothing_to_report():
    report = check(_clean_bot())
    assert report.errors == [] and report.warnings == []
    assert run_refusal(_clean_bot()) is None


def test_a_bot_with_no_variables_has_nothing_to_report():
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b"), _edge("b", "a")], decls=())
    report = check(bot)
    assert report.errors == [] and report.warnings == []


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_a_condition_on_an_undeclared_variable_is_an_error():
    bot = _clean_bot()
    bot.transitions[2].flags = [_is("sword", 1)]
    assert "the edge from 'Which' reads 'sword', which is not a declared variable" in _texts(check(bot).errors)


def test_a_write_to_an_undeclared_variable_is_an_error():
    bot = _clean_bot()
    bot.states[1].params["assignments"] = [{"variable": "sword", "op": "add", "value": 1}]
    assert "'Count' writes 'sword', which is not a declared variable" in _texts(check(bot).errors)


@pytest.mark.parametrize(
    ("decl", "row", "expected"),
    [
        (SWORDS, _is("swords", True), "compares 'swords' with true, which a int cannot hold"),
        (SWORDS, _is("swords", "two"), "compares 'swords' with two, which a int cannot hold"),
        (FOUND, _is("found", 1), "compares 'found' with 1, which a bool cannot hold"),
        (STAGE, _is("stage", "Comet"), "compares 'stage' with Comet, which a enum cannot hold"),
    ],
)
def test_a_condition_value_the_type_cannot_hold_is_an_error(decl, row, expected):
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b", "SUCCESS", row), _edge("a", "a")], decls=(decl,))
    assert any(expected in t for t in _texts(check(bot).errors))


@pytest.mark.parametrize("decl,value", [(FOUND, True), (STAGE, "Sun")])
@pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
def test_an_ordering_comparison_on_a_bool_or_an_enum_is_an_error(decl, value, op):
    bot = _bot(
        [_state("a"), _state("b")], [_edge("a", "b", "SUCCESS", _is(decl.name, value, op)), _edge("a", "a")], (decl,)
    )
    assert any(f"with {op}, which only an int can use" in t for t in _texts(check(bot).errors))


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"variable": "found", "op": "add", "value": 1}, "adds to 'found', and only an int can be added to"),
        ({"variable": "swords", "op": "toggle"}, "toggles 'swords', and only a bool can be toggled"),
        ({"variable": "swords", "op": "add", "value": "1"}, "adds 1 to 'swords': add a whole number"),
        ({"variable": "stage", "op": "set", "value": "Comet"}, "sets 'stage' to Comet, which a enum cannot hold"),
        ({"variable": "found", "op": "set", "value": 0}, "sets 'found' to 0, which a bool cannot hold"),
    ],
)
def test_a_write_the_type_refuses_is_an_error(row, expected):
    bot = _bot([_set("a", row), _state("b")], [_edge("a", "b"), _edge("b", "a")], (SWORDS, FOUND, STAGE))
    assert any(expected in t for t in _texts(check(bot).errors))


def test_assignments_that_are_not_rows_are_an_error():
    bot = _bot([_set("a", {"op": "set"}), _state("b")], [_edge("a", "b"), _edge("b", "a")])
    assert any("not a variable, an operation and a value" in t for t in _texts(check(bot).errors))


def test_two_variables_with_one_name_are_an_error():
    bot = _clean_bot()
    bot.variables.append(VariableModel(name="swords", type="bool"))
    assert "two variables are named 'swords'" in _texts(check(bot).errors)


def test_a_variable_named_like_an_option_is_an_error():
    bot = _clean_bot()
    bot.options = [BotOptionModel(name="swords", type="bool", default=False)]
    assert "variable 'swords' has the name of an option; rename one of them" in _texts(check(bot).errors)


def test_a_variable_named_like_an_option_a_node_declares_is_an_error():
    bot = _clean_bot()
    bot.states.append(_state("nap", "sleep", use_safety_delay=True))
    bot.variables.append(VariableModel(name="Safety Delay", type="int"))
    assert any("'Safety Delay' has the name of an option" in t for t in _texts(check(bot).errors))


@pytest.mark.parametrize(
    ("decl", "expected"),
    [
        (VariableModel(name="e", type="enum", values=[]), "enum variable 'e' lists no values"),
        (VariableModel(name="e", type="enum", values=["A", "A"]), "enum variable 'e' lists a value twice"),
        (VariableModel(name="e", type="enum", values=["A", " "]), "enum variable 'e' has a blank value"),
        (VariableModel(name="n", type="int", values=["A"]), "variable 'n' is int, and only an enum lists values"),
        (VariableModel(name="n", type="int", initial=True), "variable 'n' cannot start at true"),
        (VariableModel(name="b", type="bool", initial=0), "variable 'b' cannot start at 0"),
        (VariableModel(name="e", type="enum", values=["A"], initial="B"), "variable 'e' cannot start at B"),
        (VariableModel(name=" ", type="int"), "a variable has no name"),
    ],
)
def test_a_declaration_that_breaks_a_rule_is_an_error(decl, expected):
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b"), _edge("b", "a")], decls=(decl,))
    assert expected in _texts(check(bot).errors)


def test_an_error_refuses_the_run_and_names_it():
    bot = _clean_bot()
    bot.transitions[2].flags = [_is("sword", 1)]
    reason = run_refusal(bot)
    assert reason is not None and "'sword', which is not a declared variable" in reason


def test_a_warning_alone_never_refuses_the_run():
    bot = _clean_bot()
    bot.variables.append(VariableModel(name="spare", type="bool"))
    bot.states[0].params["assignments"].append({"variable": "spare", "op": "set", "value": True})
    assert check(bot).warnings and run_refusal(bot) is None


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_a_port_where_every_edge_has_a_condition_warns():
    bot = _clean_bot()
    bot.transitions[3].flags = [_is("swords", 2)]
    assert any("every edge from 'Which' on SUCCESS has a condition" in t for t in _texts(check(bot).warnings))


def test_an_edge_with_no_when_is_every_ports_otherwise():
    bot = _clean_bot()
    bot.transitions[3].when = None
    assert not any("has a condition" in t for t in _texts(check(bot).warnings))


def test_a_port_conditioned_only_on_options_is_left_alone():
    bot = _bot(
        [_state("a"), _state("b"), _state("c")],
        [
            _edge("a", "b", "SUCCESS", FlagCondition(option="Fast")),
            _edge("a", "c", "SUCCESS", FlagCondition(option="Fast", negate=True)),
            _edge("b", "a"),
            _edge("c", "a"),
        ],
        decls=(),
        options=[BotOptionModel(name="Fast", type="bool", default=False)],
    )
    assert check(bot).warnings == []


@pytest.mark.parametrize(
    ("first", "second", "overlap"),
    [
        (_is("swords", 1), _is("swords", 2), False),
        (_is("swords", 1), _is("swords", 1), True),
        (_is("swords", 3, "<"), _is("swords", 2, ">"), False),
        (_is("swords", 3, "<="), _is("swords", 3, ">="), True),
        (_is("swords", 5, "<"), _is("swords", 2, "<"), True),
        (_is("swords", 1, "!="), _is("swords", 2, "!="), True),
        (_is("swords", 1, "!="), _is("swords", 1), False),
        (_is("swords", 10, ">"), _is("swords", 10, ">"), True),
    ],
)
def test_two_int_conditions_on_one_port_warn_only_when_both_can_be_true(first, second, overlap):
    bot = _bot(
        [_state("a"), _state("b"), _state("c"), _state("d")],
        [
            _edge("a", "b", "SUCCESS", first),
            _edge("a", "c", "SUCCESS", second),
            _edge("a", "d"),
            _edge("b", "a"),
            _edge("c", "a"),
            _edge("d", "a"),
        ],
    )
    # Read-and-never-written is a warning too; only the overlap is under test.
    warned = any("can both be true" in t for t in _texts(check(bot).warnings))
    assert warned is overlap


@pytest.mark.parametrize(
    ("decl", "first", "second", "overlap"),
    [
        (FOUND, _is("found", True), _is("found", False), False),
        (FOUND, _is("found", True), _is("found", False, "!="), True),
        (STAGE, _is("stage", "Sun"), _is("stage", "Moon"), False),
        (STAGE, _is("stage", "Sun", "!="), _is("stage", "Moon", "!="), True),
    ],
)
def test_two_bool_or_enum_conditions_on_one_port_warn_only_when_both_can_be_true(decl, first, second, overlap):
    bot = _bot(
        [_state("a"), _state("b"), _state("c"), _state("d")],
        [
            _edge("a", "b", "SUCCESS", first),
            _edge("a", "c", "SUCCESS", second),
            _edge("a", "d"),
            _edge("b", "a"),
            _edge("c", "a"),
            _edge("d", "a"),
        ],
        decls=(decl,),
    )
    assert any("can both be true" in t for t in _texts(check(bot).warnings)) is overlap


def test_conditions_mixing_options_or_variables_are_not_compared():
    bot = _clean_bot()
    bot.options = [BotOptionModel(name="Fast", type="bool", default=False)]
    bot.transitions.insert(3, _edge("which", "rack_2", "SUCCESS", _is("swords", 1), FlagCondition(option="Fast")))
    assert not any("can both be true" in t for t in _texts(check(bot).warnings))


def test_a_variable_set_and_never_read_warns():
    bot = _clean_bot()
    bot.transitions[2].flags = []
    assert "variable 'swords' is set and never read" in _texts(check(bot).warnings)


def test_a_variable_read_and_never_set_warns_to_make_it_an_option():
    bot = _clean_bot()
    for s in bot.states:
        if s.node_type == "set_variables":
            s.params["assignments"] = []
    assert any("'swords' is read and never set" in t and "make it an option" in t for t in _texts(check(bot).warnings))


def test_a_set_variables_node_with_no_rows_warns():
    bot = _clean_bot()
    bot.states[0].params["assignments"] = []
    assert "'Start' sets no variables" in _texts(check(bot).warnings)


def test_a_counter_counting_inside_a_loop_that_resets_it_is_fine():
    """Count, Which and Rack 1 go round without passing Start; the loop they are part of does pass it."""
    assert not any("carries over" in t for t in _texts(check(_clean_bot()).warnings))


def _with_recovery(dest: str) -> BotConfig:
    """The clean bot, started and recovered at a Recover node that goes on to ``dest``."""
    bot = _clean_bot()
    bot.states.insert(0, _state("recover"))
    bot.initial = bot.recovery_state = "recover"
    bot.transitions.append(_edge("recover", dest))
    return bot


def test_a_variable_a_loop_changes_and_never_sets_back_warns():
    bot = _with_recovery("start")
    bot.transitions[5].dest = "recover"  # Rack 2 goes round again without passing Start
    bot.transitions[-1].dest = "count"  # and so does Recover; Start runs once, at the beginning
    bot.initial = "start"
    bot.transitions.append(_edge("start", "count"))
    found = [p for p in check(bot).warnings if "carries over" in p.text]
    assert len(found) == 1 and found[0].states == ("count",)


def test_a_recovery_that_skips_the_reset_warns():
    """Any node can fall through to recovery, so a counter a recovery reaches past its reset is stale after one."""
    found = [p for p in check(_with_recovery("count")).warnings if "after a recovery" in p.text]
    assert len(found) == 1 and found[0].states == ("count",)


def test_a_recovery_that_goes_back_through_the_reset_is_fine():
    assert check(_with_recovery("start")).warnings == []


def test_a_node_that_sets_the_variable_as_well_as_adding_to_it_resets_it():
    bot = _with_recovery("count")
    bot.states[2].params["assignments"].insert(0, {"variable": "swords", "op": "set", "value": 0})
    assert check(bot).warnings == []


def test_grouped_conditions_are_read_too():
    bot = _clean_bot()
    bot.transitions[2].flags = []
    bot.transitions[2].flag_groups = [FlagGroup(flags=[_is("ghost", 1)])]
    assert any("reads 'ghost'" in t for t in _texts(check(bot).errors))


def test_every_shipped_bot_and_template_has_nothing_to_report():
    """No shipped bot uses a variable yet, so the new run refusal can never stop one."""
    reported = []
    for folder in ("bots", "templates"):
        for path in sorted((project_root() / folder).rglob("*.json")):
            if path.name == "template.json":  # a template's index, not a bot
                continue
            report = check(BotConfig.load(path))
            if report.errors or report.warnings:
                reported.append(path.name)
    assert reported == []
