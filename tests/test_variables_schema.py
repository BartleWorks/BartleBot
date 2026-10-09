"""Variables in the bot file: declarations, variable condition rows, composition and the template diff.

What a value does at run time is test_machine_variables.py; the rules a bot's use of them
is held to are test_variables.py.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from bartlebot.bot.schema import (
    SUBGRAPH_ENTRY,
    BotConfig,
    BotOptionModel,
    DefinitionMeta,
    FlagCondition,
    FlagGroup,
    StateModel,
    SubgraphDefinition,
    TemplateMeta,
    TemplateRef,
    TransitionModel,
    VariableModel,
    definition_structure_errors,
)
from bartlebot.content.subgraphs.subgraphs import definition_problems
from bartlebot.content.templates import template_diff
from bartlebot.content.templates.template_diff import MISSING, Release, TemplateUpdate, apply, diff
from bartlebot.options import bot_options


def _state(sid: str, node_type: str = "sleep", **params) -> StateModel:
    return StateModel(id=sid, node_type=node_type, label=sid.title(), params=params)


def _edge(source: str, dest: str, *rows: FlagCondition, **extra) -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when="SUCCESS", flags=list(rows), **extra)


def _is(variable: str, value, op: str = "==") -> FlagCondition:
    return FlagCondition(variable=variable, compare=op, value=value)


def _bot(**extra) -> BotConfig:
    data = {
        "id": "bot-s",
        "name": "Schema",
        "initial": "t_1",
        "recovery_state": "t_1",
        "states": [_state("t_1"), _state("t_2", "branch"), _state("t_3")],
        "transitions": [
            _edge("t_1", "t_2"),
            _edge("t_2", "t_3", _is("swords", 2)),
            _edge("t_2", "t_1"),
            _edge("t_3", "t_1"),
        ],
        "variables": [VariableModel(name="swords", type="int")],
    }
    data.update(extra)
    return BotConfig(**data)


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def test_a_bot_without_variables_writes_no_variables_key():
    data = _bot(variables=[], transitions=[_edge("t_1", "t_2"), _edge("t_2", "t_3"), _edge("t_3", "t_1")]).file_dump()
    assert "variables" not in data


def test_an_option_row_is_written_exactly_as_before():
    bot = _bot(options=[BotOptionModel(name="Fast", type="bool", default=False)])
    bot.transitions[0].flags = [FlagCondition(option="Fast", negate=True)]
    assert bot.file_dump()["transitions"][0]["flags"] == [{"option": "Fast", "negate": True}]


def test_a_variable_row_is_written_without_negate():
    assert _bot().file_dump()["transitions"][1]["flags"] == [{"variable": "swords", "compare": "==", "value": 2}]


def test_a_bot_with_variables_round_trips_byte_identical(tmp_path):
    bot = _bot(
        variables=[
            VariableModel(name="swords", type="int", initial=0, description="Swords placed"),
            VariableModel(name="found", type="bool"),
            VariableModel(name="stage", type="enum", values=["Sun", "Moon"], initial="Moon"),
        ]
    )
    bot.states.append(
        _state(
            "t_4",
            "set_variables",
            assignments=[{"variable": "swords", "op": "add", "value": 1}, {"variable": "found", "op": "toggle"}],
        )
    )
    bot.transitions.append(
        TransitionModel(
            trigger="advance",
            source="t_3",
            dest="t_4",
            when="SUCCESS",
            flag_groups=[FlagGroup(flags=[_is("found", True), _is("stage", "Moon", "!=")], op="OR")],
        )
    )
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    bot.save(first)
    BotConfig.load(first).save(second)
    assert first.read_bytes() == second.read_bytes()
    loaded = BotConfig.load(first)
    assert loaded.variables == bot.variables
    assert loaded.transitions[-1].flag_groups[0].flags[1] == _is("stage", "Moon", "!=")
    assert json.loads(first.read_text())["variables"][2] == {
        "name": "stage",
        "type": "enum",
        "values": ["Sun", "Moon"],
        "initial": "Moon",
    }


def test_a_value_keeps_its_json_type():
    flags = TransitionModel.model_validate(
        {
            "trigger": "advance",
            "source": "a",
            "dest": "b",
            "flags": [
                {"variable": "n", "compare": ">=", "value": 1},
                {"variable": "b", "value": True},
                {"variable": "e", "value": "Sun"},
            ],
        }
    ).flags
    assert [type(f.value) for f in flags] == [int, bool, str]
    assert flags[1].compare == "==", "a variable row with no comparison is an equality"


@pytest.mark.parametrize(
    "row",
    [
        {"option": "Fast", "variable": "swords", "value": 1},
        {"negate": True},
        {"variable": "swords", "compare": "==", "value": 1, "negate": True},
        {"variable": "swords", "compare": "=="},
        {"variable": "swords", "compare": "~=", "value": 1},
    ],
)
def test_a_row_that_is_neither_one_shape_nor_the_other_does_not_load(row):
    with pytest.raises(ValidationError):
        FlagCondition.model_validate(row)


def test_an_option_row_with_a_stray_value_still_loads_and_drops_it():
    """Before variable rows, ``value`` was an unknown key a file could carry and pydantic ignored."""
    fc = FlagCondition.model_validate({"option": "Skip the chest", "value": True})
    assert fc.value is None and fc.model_dump(exclude_none=True) == {"option": "Skip the chest", "negate": False}


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_a_variable_row_compiles_to_a_registry_entry_beside_option_rows():
    bot = _bot(options=[BotOptionModel(name="Fast", type="bool", default=False)])
    bot.transitions[1].flags = []
    bot.transitions[1].flag_groups = [FlagGroup(flags=[_is("swords", 2), FlagCondition(option="Fast")])]
    config = bot.to_machine_config()
    compound = next(t for t in config["transitions"] if t["dest"] == "t_3")["conditions"][1]
    assert config["conditions_registry"][compound]["groups"][0]["flags"] == [
        {"variable": "swords", "compare": "==", "value": 2},
        {"option": "Fast", "negate": False},
    ]


def test_a_lone_variable_row_never_takes_the_option_shortcut():
    config = _bot().to_machine_config()
    conditions = next(t for t in config["transitions"] if t["dest"] == "t_3")["conditions"]
    assert conditions[1].startswith("_flag_compound_")


def test_a_conditioned_edge_is_tried_before_its_otherwise():
    config = _bot().to_machine_config()
    assert [t["dest"] for t in config["transitions"] if t["source"] == "t_2"] == ["t_3", "t_1"]


def test_the_machine_config_carries_the_declarations():
    assert _bot().to_machine_config()["variables"] == [
        {"name": "swords", "type": "int", "values": None, "initial": None, "description": None}
    ]


# ---------------------------------------------------------------------------
# Readers of option rows pass over variable rows
# ---------------------------------------------------------------------------


def test_a_variable_row_is_never_an_orphaned_option():
    assert bot_options.orphaned_flag_options(_bot()) == []


# ---------------------------------------------------------------------------
# Subgraph definitions
# ---------------------------------------------------------------------------


def _definition_parts(*states: StateModel, rows=()):
    entry = StateModel(id="entry", node_type=SUBGRAPH_ENTRY, label="Start")
    body = list(states) or [_state("s")]
    transitions = [TransitionModel(trigger="advance", source="entry", dest=body[0].id)]
    if rows:
        transitions.append(_edge(body[0].id, body[0].id, *rows))
    return [entry, *body], transitions


@pytest.mark.parametrize(
    ("states", "rows"),
    [
        ((_state("s", "set_variables", assignments=[]),), ()),
        ((_state("s"),), (_is("swords", 1),)),
    ],
)
def test_a_subgraph_definition_that_uses_a_variable_is_refused(states, rows):
    all_states, transitions = _definition_parts(*states, rows=rows)
    errors = definition_structure_errors(all_states, transitions, [], [], [])
    assert "a subgraph cannot use variables: they belong to the bot" in errors
    with pytest.raises(ValidationError):
        SubgraphDefinition(id="sg", name="SG", states=all_states, transitions=transitions)


def test_a_definition_using_a_branch_and_option_rows_is_fine():
    all_states, transitions = _definition_parts(_state("s", "branch"), rows=(FlagCondition(option="Fast"),))
    assert definition_structure_errors(all_states, transitions, [], [], []) == []


def test_a_definition_document_declaring_variables_cannot_be_saved():
    all_states, transitions = _definition_parts(_state("s"))
    doc = BotConfig(
        id="sg",
        name="SG",
        initial="entry",
        recovery_state="entry",
        states=all_states,
        transitions=transitions,
        definition=DefinitionMeta(id="sg"),
        variables=[VariableModel(name="n", type="int")],
    )
    errors, _warnings = definition_problems(doc, {})
    assert "a subgraph cannot declare variables: they belong to the bot" in errors


# ---------------------------------------------------------------------------
# The template diff
# ---------------------------------------------------------------------------

TEMPLATE_ID = "tpl-vars"


def _template(**extra) -> BotConfig:
    bot = _bot(**extra)
    bot.id = TEMPLATE_ID
    bot.template_meta = TemplateMeta(blurb="Vars")
    return bot


def _made_from(version: BotConfig) -> BotConfig:
    bot = version.model_copy(deep=True)
    bot.id, bot.name, bot.template_meta = "bot-1", "Mine", None
    bot.template = TemplateRef(id=TEMPLATE_ID, version=1)
    return bot


def _update(v1: BotConfig, v2: BotConfig) -> TemplateUpdate:
    return TemplateUpdate(TEMPLATE_ID, "Vars", 1, v1, [Release(2, "Release 2", v2)])


def test_an_added_variable_is_a_change_and_applying_it_declares_it():
    v1 = _template()
    v2 = _template(variables=[VariableModel(name="swords", type="int"), VariableModel(name="found", type="bool")])
    mine = _made_from(v1)

    changes = diff(_update(v1, v2), mine)

    assert [(c.title, c.detail, c.units[0].base) for c in changes] == [("Variable 'found'", "Added", MISSING)]
    after = apply(mine, changes[0])
    assert [v.name for v in after.variables] == ["swords", "found"]


def test_a_changed_declaration_and_a_removed_variable_are_changes():
    v1 = _template(variables=[VariableModel(name="swords", type="int"), VariableModel(name="found", type="bool")])
    v2 = _template(variables=[VariableModel(name="swords", type="int", initial=1)])
    mine = _made_from(v1)

    after = mine
    changes = diff(_update(v1, v2), mine)
    for change in changes:
        after = apply(after, change)

    assert sorted((c.title, c.detail) for c in changes) == [
        ("Variable 'found'", "Removed"),
        ("Variable 'swords'", "Its declaration changed"),
    ]
    assert after.variables == [VariableModel(name="swords", type="int", initial=1)]


def test_a_changed_condition_is_a_change_to_the_edge():
    v1 = _template()
    v2 = _template()
    v2.transitions[1].flags = [_is("swords", 3)]
    mine = _made_from(v1)

    changes = diff(_update(v1, v2), mine)
    after = mine
    for change in changes:
        after = apply(after, change)

    assert changes and all("(with conditions)" in c.title for c in changes)
    guarded = [t for t in after.transitions if t.source == "t_2" and t.flags]
    assert [t.flags for t in guarded] == [[_is("swords", 3)]]


def test_one_guard_in_two_row_orders_is_one_slot():
    a = _edge("t_2", "t_3", _is("swords", 2), FlagCondition(option="Fast"))
    b = _edge("t_2", "t_3", FlagCondition(option="Fast"), _is("swords", 2))
    assert template_diff._canon_flags(a) == template_diff._canon_flags(b)
    assert ("swords", "==", 2) in template_diff._canon_flags(a)[1]
