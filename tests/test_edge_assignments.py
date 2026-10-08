"""Edge assignments (#189): writes an edge makes when it is the one taken.

The file shape, the machine applying them, the log, and what validation, rename and
delete-in-use make of them. The template diff's half is in test_template_diff_assignments.py,
and the editor's in test_edge_assignments_panel.py.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from pydantic import ValidationError

from bartlebot import variables
from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import (
    SUBGRAPH_ENTRY,
    AssignmentModel,
    BotConfig,
    FlagCondition,
    StateModel,
    SubgraphDefinition,
    TransitionModel,
    VariableModel,
    definition_structure_errors,
)
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS

DECLS = [
    VariableModel(name="swords", type="int"),
    VariableModel(name="next_spot", type="int", initial=1),
    VariableModel(name="found", type="bool"),
]

ADD_SWORD = {"variable": "swords", "op": "add", "value": 1}


def _state(sid: str, node_type: str = "work", **params) -> StateModel:
    return StateModel(id=sid, node_type=node_type, label=sid.title(), params=params)


def _edge(source: str, dest: str, *assign: dict, when: str | None = "SUCCESS", flags=(), **extra) -> TransitionModel:
    return TransitionModel(
        trigger="advance",
        source=source,
        dest=dest,
        when=when,
        flags=list(flags),
        assign=[AssignmentModel.model_validate(a) for a in assign],
        **extra,
    )


def _is(variable: str, value, op: str = "==") -> FlagCondition:
    return FlagCondition(variable=variable, compare=op, value=value)


def _bot(states, transitions, **extra) -> BotConfig:
    return BotConfig(
        id="bot-e",
        name="Edges",
        initial=states[0].id,
        recovery_state=extra.pop("recovery_state", states[0].id),
        states=states,
        transitions=transitions,
        variables=extra.pop("variables", DECLS),
        **extra,
    )


class _Recorder:
    """A stand-in node that counts its runs and returns the next of its results."""

    def __init__(self, *results: str):
        self.runs: list[str] = []
        self.results = list(results) or ["SUCCESS"]

    async def __call__(self, machine, clients, params):
        self.runs.append(machine.state)
        await asyncio.sleep(0)
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


async def _stop_node(machine, clients, params):
    machine.stop()
    return "SUCCESS"


def _machine(bot: BotConfig, work=None, **kwargs) -> BotMachine:
    registry = dict(NODE_REGISTRY)
    registry["work"] = work or _Recorder()
    registry["done"] = _stop_node
    return BotMachine(
        clients=[],
        config=bot.to_machine_config(node_specs=NODE_SPECS),
        node_registry=registry,
        bot_config=bot,
        stop_grace_period=0.1,
        **kwargs,
    )


async def _run(machine: BotMachine, timeout: float = 2.0) -> None:
    await asyncio.wait_for(machine.run(), timeout)


async def _until(predicate, timeout: float = 2.0) -> None:
    async def _wait():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(_wait(), timeout)


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def test_an_edge_without_assignments_is_written_as_before():
    dumped = _edge("a", "b").model_dump()
    assert "assign" not in dumped
    assert TransitionModel.model_validate(dumped).assign == []


def test_an_edges_assignments_round_trip_in_order():
    edge = _edge("a", "b", {"variable": "swords", "op": "set", "value": 0}, ADD_SWORD)
    again = TransitionModel.model_validate(edge.model_dump())
    assert [(r.variable, r.op, r.value) for r in again.assign] == [("swords", "set", 0), ("swords", "add", 1)]


def test_a_bot_file_carries_the_assignments_and_leaves_the_rest_alone():
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b", ADD_SWORD), _edge("b", "a")])
    data = bot.file_dump()
    assert data["transitions"][0]["assign"] == [ADD_SWORD]
    assert "assign" not in data["transitions"][1]
    assert BotConfig.model_validate(data).file_dump() == data


def test_the_machine_config_carries_the_rows_of_an_assigning_edge_only():
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b", ADD_SWORD), _edge("b", "a")])
    compiled = bot.to_machine_config()["transitions"]
    assert compiled[0]["assign"] == [ADD_SWORD]
    assert "assign" not in compiled[1]


@pytest.mark.parametrize("rows", [[ADD_SWORD], [{"variable": "found", "op": "toggle"}]])
def test_a_subgraph_definition_with_an_edge_assignment_is_refused(rows):
    states = [StateModel(id="entry", node_type=SUBGRAPH_ENTRY, label="Start"), _state("s")]
    transitions = [TransitionModel(trigger="advance", source="entry", dest="s"), _edge("s", "s", *rows)]
    assert "a subgraph cannot use variables: they belong to the bot" in definition_structure_errors(
        states, transitions, [], [], []
    )
    with pytest.raises(ValidationError):
        SubgraphDefinition(id="sg", name="SG", states=states, transitions=transitions)


# ---------------------------------------------------------------------------
# The machine: exactly once, on the edge taken, and never on a recovery jump
# ---------------------------------------------------------------------------


async def test_an_assignment_runs_once_when_its_edge_is_taken():
    changes: list[tuple] = []
    bot = _bot(
        [_state("a"), _state("b"), _state("end", "done")],
        [_edge("a", "b", ADD_SWORD, {"variable": "next_spot", "op": "set", "value": 4}), _edge("b", "end")],
    )
    machine = _machine(bot, on_variable_change=lambda *c: changes.append(c))
    await _run(machine)
    assert changes == [("swords", 0, 1), ("next_spot", 1, 4)]
    assert dict(machine.variables) == {"swords": 1, "next_spot": 4, "found": False}


async def test_another_edge_from_the_same_port_writes_nothing():
    """Two edges on one port: the conditioned one is tried first and fails, the otherwise is taken."""
    bot = _bot(
        [_state("a"), _state("one"), _state("other"), _state("end", "done")],
        [
            _edge("a", "one", ADD_SWORD, flags=[_is("found", True)]),
            _edge("a", "other", {"variable": "next_spot", "op": "add", "value": 1}),
            _edge("one", "end"),
            _edge("other", "end"),
        ],
    )
    machine = _machine(bot)
    await _run(machine)
    assert dict(machine.variables) == {"swords": 0, "next_spot": 2, "found": False}


async def test_an_edge_on_another_port_writes_nothing():
    bot = _bot(
        [_state("a"), _state("won"), _state("lost"), _state("end", "done")],
        [
            _edge("a", "won", ADD_SWORD),
            _edge("a", "lost", {"variable": "found", "op": "toggle"}, when="FAILURE"),
            _edge("won", "end"),
            _edge("lost", "end"),
        ],
    )
    machine = _machine(bot, work=_Recorder("FAILURE", "SUCCESS"))
    await _run(machine)
    assert dict(machine.variables) == {"swords": 0, "next_spot": 1, "found": True}


async def test_a_recovery_jump_takes_no_edge_and_applies_nothing():
    """'risky' fails with no FAILURE edge, so it falls through to recovery: its SUCCESS edge's write never runs."""
    bot = _bot(
        [_state("recover", "branch"), _state("risky"), _state("end", "done")],
        [
            _edge("recover", "risky", flags=[_is("found", False)]),
            _edge("recover", "end"),
            _edge("risky", "end", ADD_SWORD, {"variable": "found", "op": "set", "value": True}),
        ],
    )
    machine = _machine(bot, work=_Recorder("FAILURE", "FAILURE", "SUCCESS"))
    await _run(machine)
    # recover -> risky FAILS -> recover -> risky FAILS -> recover -> risky SUCCEEDS (writes) -> end
    assert machine.variables["swords"] == 1
    assert machine.variables["found"] is True


async def test_force_state_applies_no_assignment():
    machine = _machine(_bot([_state("a"), _state("b")], [_edge("a", "b", ADD_SWORD)]))
    machine.force_state("b", reason="test")
    assert machine.variables["swords"] == 0


async def test_rows_apply_in_order_and_a_later_row_sees_an_earlier_one():
    bot = _bot(
        [_state("a"), _state("end", "done")],
        [_edge("a", "end", {"variable": "swords", "op": "set", "value": 5}, ADD_SWORD, ADD_SWORD)],
    )
    machine = _machine(bot)
    await _run(machine)
    assert machine.variables["swords"] == 7


async def test_a_condition_reads_the_value_an_earlier_edge_wrote():
    """Heretical Academy's shape: a spot's edge counts the sword, and the branch after it reads the count."""
    bot = _bot(
        [_state("spot"), _state("which", "branch"), _state("rack_1"), _state("rack_2"), _state("end", "done")],
        [
            _edge("spot", "which", ADD_SWORD, {"variable": "next_spot", "op": "set", "value": 2}),
            _edge("which", "rack_1", flags=[_is("swords", 1)]),
            _edge("which", "rack_2"),
            _edge("rack_1", "spot", flags=[_is("swords", 2, "<")]),
            _edge("rack_1", "end"),
            _edge("rack_2", "end"),
        ],
    )
    work = _Recorder()
    machine = _machine(bot, work=work)
    await _run(machine)
    assert work.runs == ["spot", "rack_1", "spot", "rack_2"]
    assert machine.variables["swords"] == 2


async def test_a_write_validation_should_have_refused_stops_the_run_and_writes_nothing():
    reasons: list[str] = []
    bot = _bot(
        [_state("a"), _state("b"), _state("end", "done")],
        [_edge("a", "b", ADD_SWORD, {"variable": "found", "op": "add", "value": 1}), _edge("b", "end")],
    )
    work = _Recorder()
    machine = _machine(bot, work=work, on_stopped=reasons.append)
    await _run(machine)
    assert machine.is_stopped and work.runs == ["a"]
    assert machine.variables["swords"] == 0
    assert len(reasons) == 1 and "could not set its variables" in reasons[0] and "'found'" in reasons[0]


async def test_a_rerun_of_the_node_the_edge_led_to_does_not_apply_it_again():
    """An ambient fight cancels and reruns the node an edge led into; the edge was taken once, so it wrote once."""
    changes: list[tuple] = []
    machine_ref: list[BotMachine] = []
    preempted = []

    def on_state(state_id):
        if state_id == "b" and not preempted:
            machine = machine_ref[0]
            machine.pause()
            preempted.append(asyncio.ensure_future(machine._preempt_node()))

    bot = _bot(
        [_state("a"), _state("b"), _state("end", "done")],
        [_edge("a", "b", ADD_SWORD), _edge("b", "end")],
    )
    machine = _machine(bot, on_state_change=on_state, on_variable_change=lambda *c: changes.append(c))
    machine_ref.append(machine)
    task = asyncio.create_task(machine.run())
    await _until(lambda: preempted and preempted[0].done())
    machine.resume()
    await asyncio.wait_for(task, 2.0)
    assert changes == [("swords", 0, 1)]


async def test_a_loop_through_an_assigning_edge_writes_once_per_trip():
    bot = _bot(
        [_state("game"), _state("which", "branch"), _state("end", "done")],
        [
            _edge("game", "which", ADD_SWORD),
            _edge("which", "end", flags=[_is("swords", 3)]),
            _edge("which", "game"),
        ],
    )
    changes: list[tuple] = []
    machine = _machine(bot, on_variable_change=lambda *c: changes.append(c))
    await _run(machine)
    assert changes == [("swords", 0, 1), ("swords", 1, 2), ("swords", 2, 3)]


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------


def _messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


async def test_the_transition_names_what_it_set_and_each_write_follows_it_once(caplog):
    bot = _bot(
        [_state("a"), _state("b"), _state("end", "done")],
        [_edge("a", "b", {"variable": "next_spot", "op": "set", "value": 4}, ADD_SWORD), _edge("b", "end")],
    )
    with caplog.at_level(logging.DEBUG, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    lines = [m for m in _messages(caplog) if m.startswith(("Transition:", "Variable "))]
    assert lines == [
        "Transition: 'a' (A) --SUCCESS / next_spot = 4, swords + 1--> 'b' (B)",
        "Variable next_spot: 1 → 4",
        "Variable swords: 0 → 1",
        "Transition: 'b' (B) --SUCCESS--> 'end' (End)",
    ]


async def test_a_conditioned_assigning_edge_names_both(caplog):
    bot = _bot(
        [_state("a"), _state("b"), _state("end", "done")],
        [
            _edge("a", "b", {"variable": "found", "op": "toggle"}, flags=[_is("swords", 0)]),
            _edge("a", "end"),
            _edge("b", "end"),
        ],
    )
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    assert "Transition: 'a' (A) --SUCCESS [swords == 0] / found toggled--> 'b' (B)" in _messages(caplog)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _texts(problems) -> list[str]:
    return [p.text for p in problems]


def _checked(*edges: TransitionModel, states=None) -> variables.Report:
    states = states or [_state("a"), _state("b")]
    return variables.check(_bot(states, list(edges)), specs={})


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {"variable": "ghost", "op": "set", "value": 1},
            "the edge from 'A' to 'B' writes 'ghost', which is not a declared variable",
        ),
        (
            {"variable": "found", "op": "add", "value": 1},
            "the edge from 'A' to 'B' adds to 'found', and only an int can be added to",
        ),
        (
            {"variable": "swords", "op": "add", "value": True},
            "the edge from 'A' to 'B' adds true to 'swords': add a whole number",
        ),
        (
            {"variable": "swords", "op": "toggle"},
            "the edge from 'A' to 'B' toggles 'swords', and only a bool can be toggled",
        ),
        (
            {"variable": "found", "op": "set", "value": 3},
            "the edge from 'A' to 'B' sets 'found' to 3, which a bool cannot hold",
        ),
    ],
)
def test_an_edges_bad_write_is_an_error_marked_on_its_source(row, expected):
    report = _checked(_edge("a", "b", row), _edge("b", "a"))
    found = [p for p in report.errors if p.text == expected]
    assert len(found) == 1 and found[0].states == ("a",)
    assert variables.run_refusal(_bot([_state("a"), _state("b")], [_edge("a", "b", row)]), specs={}) is not None


def test_an_edge_write_counts_as_the_write_a_read_needs():
    bot = _bot(
        [_state("a"), _state("b")],
        [
            _edge("a", "b", {"variable": "swords", "op": "set", "value": 1}),
            _edge("b", "a", flags=[_is("swords", 1)]),
            _edge("b", "b"),
        ],
        variables=[DECLS[0]],
    )
    assert not any("never set" in t for t in _texts(variables.check(bot, specs={}).warnings))


def test_an_edge_write_nothing_reads_warns():
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b", ADD_SWORD), _edge("b", "a")], variables=[DECLS[0]])
    found = [p for p in variables.check(bot, specs={}).warnings if "set and never read" in p.text]
    assert len(found) == 1 and found[0].states == ("a",)


def _puzzle(enter_rows=(), *, recover_to: str = "enter") -> BotConfig:
    """recover -> enter -> spot; spot's SUCCESS counts a sword and goes to which; which loops back or exits."""
    return _bot(
        [_state("recover"), _state("enter"), _state("spot"), _state("which", "branch"), _state("exit")],
        [
            _edge("recover", recover_to),
            _edge("enter", "spot", *enter_rows),
            _edge("spot", "which", ADD_SWORD),
            _edge("which", "exit", flags=[_is("swords", 4)]),
            _edge("which", "spot"),
            _edge("exit", "enter"),
        ],
        variables=[DECLS[0]],
    )


def test_a_counter_reset_on_the_edge_into_the_loop_is_fine():
    assert variables.check(_puzzle([{"variable": "swords", "op": "set", "value": 0}]), specs={}).warnings == []


def test_a_counter_an_edge_changes_and_nothing_sets_back_warns_on_that_edges_source():
    found = [p for p in variables.check(_puzzle(), specs={}).warnings if "carries over" in p.text]
    assert len(found) == 1 and found[0].states == ("spot",)


def test_a_recovery_past_the_edge_that_resets_warns():
    bot = _puzzle([{"variable": "swords", "op": "set", "value": 0}], recover_to="spot")
    found = [p for p in variables.check(bot, specs={}).warnings if "after a recovery" in p.text]
    assert len(found) == 1 and found[0].states == ("spot",)


def test_a_reset_on_one_edge_does_not_cover_another_between_the_same_two_nodes():
    """Enter's FAILURE edge reaches the spot without passing the SUCCESS edge's reset.

    The loop as a whole still has its reset, so it is the recovery check that sees it: a
    recovery goes back in through Enter, and Enter failing skips the reset.
    """
    bot = _puzzle([{"variable": "swords", "op": "set", "value": 0}])
    assert variables.check(bot, specs={}).warnings == []
    bot.transitions.append(_edge("enter", "spot", when="FAILURE"))
    found = [p for p in variables.check(bot, specs={}).warnings if "after a recovery" in p.text]
    assert len(found) == 1 and found[0].states == ("spot",)


def test_the_badges_quick_skip_sees_an_edge_assignment():
    from bartlebot.gui.canvas.edit_scene import variable_report

    bot = _bot(
        [_state("a"), _state("b")], [_edge("a", "b", {"variable": "ghost", "op": "set", "value": 1})], variables=[]
    )
    report = variable_report(bot)
    assert report is not None and any("'ghost'" in t for t in _texts(report.errors))


# ---------------------------------------------------------------------------
# Rename and delete-in-use
# ---------------------------------------------------------------------------


def test_a_rename_carries_an_edges_assignments():
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b", ADD_SWORD, {"variable": "found", "op": "toggle"})])
    variables.rename(bot, "swords", "blades")
    assert [r.variable for r in bot.transitions[0].assign] == ["blades", "found"]


def test_an_edge_that_writes_a_variable_is_a_use_of_it():
    bot = _bot([_state("a"), _state("b")], [_edge("a", "b", ADD_SWORD, when="FAILURE"), _edge("b", "a")])
    assert [u.text for u in variables.uses(bot, "swords")] == ["the edge from 'A' to 'B' on FAILURE"]
    assert variables.uses(bot, "found") == []


def test_removing_an_enum_value_an_edge_sets_is_a_use_and_adding_to_one_is_not():
    stage = VariableModel(name="stage", type="enum", values=["Sun", "Moon"])
    bot = _bot(
        [_state("a"), _state("b")],
        [_edge("a", "b", {"variable": "stage", "op": "set", "value": "Moon"}), _edge("b", "a")],
        variables=[stage],
    )
    assert len(variables.uses(bot, "stage", values=["Moon"])) == 1
    assert variables.uses(bot, "stage", values=["Sun"]) == []


# ---------------------------------------------------------------------------
# The label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("edge", "whole", "short"),
    [
        (_edge("a", "b"), None, None),
        (_edge("a", "b", flags=[_is("swords", 2)]), "swords == 2", "swords == 2"),
        (
            _edge("a", "b", {"variable": "next_spot", "op": "set", "value": 4}, ADD_SWORD),
            "SUCCESS → next_spot = 4, swords + 1",
            "SUCCESS → next_spot = 4, swords + 1",
        ),
        (
            _edge("a", "b", {"variable": "found", "op": "toggle"}, flags=[_is("swords", 2)]),
            "swords == 2 → found toggled",
            "swords == 2 → found toggled",
        ),
        (_edge("a", "b", ADD_SWORD, when=None), "→ swords + 1", "→ swords + 1"),
    ],
)
def test_an_edges_label_says_what_chooses_it_and_then_what_it_sets(edge, whole, short):
    label = variables.edge_label(edge)
    assert (label is None) is (whole is None)
    if label is not None:
        assert label == (whole, short)


def test_each_part_of_the_label_is_cut_on_its_own():
    long_guard = [_is("swords", 1), _is("next_spot", 2), _is("found", True)]
    edge = _edge("a", "b", ADD_SWORD, {"variable": "next_spot", "op": "set", "value": 4}, flags=long_guard)
    whole, short = variables.edge_label(edge, limit=12)
    assert whole == "swords == 1 AND next_spot == 2 AND found == true → swords + 1, next_spot = 4"
    assert short == "swords == 1… → swords + 1,…"
