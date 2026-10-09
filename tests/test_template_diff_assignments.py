"""Edge assignments in the template diff (#189), and a variable grouped with what uses it.

A slot's value is where its edge lands and what it sets when taken, so an added,
removed or changed assignment is a change to the edge. A variable the template declares
or drops is one change with every edge and node that reads or writes it: applied alone,
either would leave a row naming a variable the bot does not declare.
"""

from __future__ import annotations

from bartlebot import variables
from bartlebot.bot.schema import (
    AssignmentModel,
    BotConfig,
    FlagCondition,
    StateModel,
    TemplateMeta,
    TemplateRef,
    TransitionModel,
    VariableModel,
)
from bartlebot.content.templates.template_diff import Release, SlotValue, Status, TemplateUpdate, apply, diff

TEMPLATE_ID = "tpl-assign"
SWORDS = VariableModel(name="swords", type="int")
FOUND = VariableModel(name="found", type="bool")


def _state(sid: str, node_type: str = "sleep", **params) -> StateModel:
    return StateModel(id=sid, node_type=node_type, label=sid.replace("_", " ").title(), params=params)


def _edge(source: str, dest: str, *assign: dict, flags=(), when: str = "SUCCESS") -> TransitionModel:
    return TransitionModel(
        trigger="advance",
        source=source,
        dest=dest,
        when=when,
        flags=list(flags),
        assign=[AssignmentModel.model_validate(a) for a in assign],
    )


def _template(transitions=None, states=None, decls=(SWORDS,)) -> BotConfig:
    """Enter resets the count on its way to the spot; the spot counts a sword; Which reads it."""
    return BotConfig(
        id=TEMPLATE_ID,
        name="Assign",
        initial="t_enter",
        recovery_state="t_enter",
        template_meta=TemplateMeta(blurb="Assign"),
        variables=list(decls),
        states=states
        or [_state("t_enter"), _state("t_spot"), _state("t_which", "branch"), _state("t_rack"), _state("t_gate")],
        transitions=transitions
        if transitions is not None
        else [
            _edge("t_enter", "t_spot", {"variable": "swords", "op": "set", "value": 0}),
            _edge("t_spot", "t_which", {"variable": "swords", "op": "add", "value": 1}),
            _edge("t_which", "t_gate", flags=[FlagCondition(variable="swords", compare="==", value=2)]),
            _edge("t_which", "t_rack"),
            _edge("t_rack", "t_spot"),
            _edge("t_gate", "t_enter"),
        ],
    )


def _made_from(version: BotConfig) -> BotConfig:
    bot = version.model_copy(deep=True)
    bot.id, bot.name, bot.template_meta = "bot-1", "Mine", None
    bot.template = TemplateRef(id=TEMPLATE_ID, version=1)
    return bot


def _update(v1: BotConfig, v2: BotConfig) -> TemplateUpdate:
    return TemplateUpdate(TEMPLATE_ID, "Assign", 1, v1, [Release(2, "Release 2", v2)])


def _edge_of(bot: BotConfig, source: str, dest: str) -> TransitionModel:
    return next(t for t in bot.transitions if (t.source, t.dest) == (source, dest))


def _rows(t: TransitionModel) -> list[tuple]:
    return [(a.variable, a.op, a.value) for a in t.assign]


# ---------------------------------------------------------------------------
# An edge's assignments are part of its slot
# ---------------------------------------------------------------------------


def test_an_added_assignment_is_a_change_to_the_edge():
    v1 = _template()
    v2 = _template()
    _edge_of(v2, "t_rack", "t_spot").assign = [AssignmentModel(variable="swords", op="add", value=1)]
    mine = _made_from(v1)

    (change,) = diff(_update(v1, v2), mine)
    assert change.status is Status.OFFERED
    assert change.title == "T Rack · on SUCCESS"
    assert change.detail == "When taken, sets nothing → sets swords + 1"
    assert change.units[0].theirs == SlotValue("t_spot", (("swords", "add", 1),))
    assert _rows(_edge_of(apply(mine, change), "t_rack", "t_spot")) == [("swords", "add", 1)]


def test_a_removed_assignment_is_a_change_to_the_edge():
    v1 = _template()
    v2 = _template()
    _edge_of(v2, "t_spot", "t_which").assign = []
    mine = _made_from(v1)

    (change,) = diff(_update(v1, v2), mine)
    assert change.detail == "When taken, sets swords + 1 → sets nothing"
    after = apply(mine, change)
    assert _edge_of(after, "t_spot", "t_which").assign == []
    assert "assign" not in after.file_dump()["transitions"][1]


def test_a_changed_assignment_is_a_change_to_the_edge():
    v1 = _template()
    v2 = _template()
    _edge_of(v2, "t_enter", "t_spot").assign = [AssignmentModel(variable="swords", op="set", value=1)]
    mine = _made_from(v1)

    (change,) = diff(_update(v1, v2), mine)
    assert change.detail == "When taken, sets swords = 0 → sets swords = 1"
    assert _rows(_edge_of(apply(mine, change), "t_enter", "t_spot")) == [("swords", "set", 1)]


def test_reordered_rows_are_a_change_since_a_later_row_sees_an_earlier_one():
    v1 = _template()
    _edge_of(v1, "t_enter", "t_spot").assign = [
        AssignmentModel(variable="swords", op="set", value=0),
        AssignmentModel(variable="swords", op="add", value=1),
    ]
    v2 = v1.model_copy(deep=True)
    _edge_of(v2, "t_enter", "t_spot").assign.reverse()
    assert len(diff(_update(v1, v2), _made_from(v1))) == 1


def test_an_assignment_the_user_changed_too_is_a_conflict_naming_theirs():
    v1 = _template()
    v2 = _template()
    _edge_of(v2, "t_enter", "t_spot").assign = [AssignmentModel(variable="swords", op="set", value=1)]
    mine = _made_from(v1)
    _edge_of(mine, "t_enter", "t_spot").assign = [AssignmentModel(variable="swords", op="set", value=5)]

    (change,) = diff(_update(v1, v2), mine)
    assert change.status is Status.CONFLICT
    assert change.detail == "When taken, sets swords = 0 → sets swords = 1. Yours: sets swords = 5"


def test_an_edge_the_bot_already_matches_is_not_shown():
    v1 = _template()
    v2 = _template()
    _edge_of(v2, "t_rack", "t_spot").assign = [AssignmentModel(variable="swords", op="add", value=1)]
    mine = _made_from(v2)
    mine.template = TemplateRef(id=TEMPLATE_ID, version=1)
    assert diff(_update(v1, v2), mine) == []


def test_a_rewire_and_a_new_assignment_together_are_one_change_and_say_both():
    v1 = _template()
    v2 = _template()
    rack = _edge_of(v2, "t_rack", "t_spot")
    rack.dest = "t_gate"
    rack.assign = [AssignmentModel(variable="swords", op="add", value=1)]
    mine = _made_from(v1)

    (change,) = diff(_update(v1, v2), mine)
    assert change.detail == "'T Spot' → 'T Gate', setting swords + 1"
    after = _edge_of(apply(mine, change), "t_rack", "t_gate")
    assert _rows(after) == [("swords", "add", 1)]


def test_a_new_edge_comes_with_its_assignments():
    v1 = _template()
    v2 = _template()
    v2.transitions.append(_edge("t_spot", "t_spot", {"variable": "swords", "op": "add", "value": 2}, when="FAILURE"))
    mine = _made_from(v1)

    (change,) = diff(_update(v1, v2), mine)
    added = next(t for t in apply(mine, change).transitions if t.when == "FAILURE")
    assert _rows(added) == [("swords", "add", 2)]


# ---------------------------------------------------------------------------
# A variable goes with what uses it
# ---------------------------------------------------------------------------


def _no_errors(bot: BotConfig) -> bool:
    return variables.check(bot, specs={}).errors == []


def test_a_declared_variable_and_the_edges_that_use_it_are_one_change():
    v1 = _template()
    v2 = _template(decls=(SWORDS, FOUND))
    _edge_of(v2, "t_which", "t_gate").assign = [AssignmentModel(variable="found", op="set", value=True)]
    _edge_of(v2, "t_gate", "t_enter").flags = [FlagCondition(variable="found", compare="==", value=True)]
    v2.transitions.append(_edge("t_gate", "t_gate"))
    mine = _made_from(v1)

    changes = diff(_update(v1, v2), mine)
    grouped = [c for c in changes if any(u.key == ("variable", "found") for u in c.units)]
    assert len(grouped) == 1
    (change,) = grouped
    assert change.title == "Variable 'found'"
    assert change.detail.startswith("Added (and ")
    kinds = sorted(u.key[0] for u in change.units)
    assert kinds.count("slot") >= 2 and kinds.count("variable") == 1
    assert _no_errors(apply(mine, change))


def test_a_dropped_variable_and_the_edges_that_used_it_are_one_change():
    v1 = _template(decls=(SWORDS, FOUND))
    _edge_of(v1, "t_which", "t_gate").assign = [AssignmentModel(variable="found", op="toggle")]
    v2 = _template()
    mine = _made_from(v1)

    changes = diff(_update(v1, v2), mine)
    (change,) = [c for c in changes if any(u.key == ("variable", "found") for u in c.units)]
    assert len(change.units) == 2
    assert change.title == "Variable 'found'" and change.detail == "Removed (and 1 more)"
    after = apply(mine, change)
    assert [v.name for v in after.variables] == ["swords"]
    assert _no_errors(after)


def test_a_set_variables_node_writing_a_declared_variable_goes_with_it_too():
    v1 = _template()
    v2 = _template(decls=(SWORDS, FOUND))
    v2.states.append(_state("t_mark", "set_variables", assignments=[{"variable": "found", "op": "set", "value": True}]))
    _edge_of(v2, "t_gate", "t_enter").dest = "t_mark"
    v2.transitions.append(_edge("t_mark", "t_enter"))
    mine = _made_from(v1)

    changes = diff(_update(v1, v2), mine)
    assert len(changes) == 1
    (change,) = changes
    assert change.title == "Add 'T Mark'; declare 'found'"
    assert _no_errors(apply(mine, change))


def test_a_changed_declaration_is_not_grouped_with_its_uses():
    """Only a declaration that comes or goes leaves a row dangling; one that changes does not."""
    v1 = _template()
    v2 = _template(decls=(VariableModel(name="swords", type="int", initial=1),))
    _edge_of(v2, "t_rack", "t_spot").assign = [AssignmentModel(variable="swords", op="add", value=1)]
    changes = diff(_update(v1, v2), _made_from(v1))
    assert sorted(len(c.units) for c in changes) == [1, 1]
