"""Variables on BotMachine: fresh values, conditions that read them, the one write, and the loop limit.

Real set_variables and branch nodes beside async stand-ins for the rest; no Wizard101
client is involved.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from bartlebot.bot import variables
from bartlebot.bot.machine import BotMachine, VariableWriteRefused
from bartlebot.bot.schema import (
    BotConfig,
    BotOptionModel,
    FlagCondition,
    FlagGroup,
    StateModel,
    TransitionModel,
    VariableModel,
)
from bartlebot.bot.variables import VariableError
from bartlebot.nodes.control_flow import branch, set_variables
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS

DECLS = [
    VariableModel(name="swords", type="int", initial=1),
    VariableModel(name="found", type="bool"),
    VariableModel(name="stage", type="enum", values=["Sun", "Moon"]),
]


def _state(sid: str, node_type: str = "work", **params) -> StateModel:
    return StateModel(id=sid, node_type=node_type, label=sid.title(), params=params)


def _set(sid: str, *rows: dict) -> StateModel:
    return _state(sid, "set_variables", assignments=list(rows))


def _edge(source: str, dest: str, *rows: FlagCondition, when: str | None = "SUCCESS", **extra) -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when=when, flags=list(rows), **extra)


def _is(variable: str, value, op: str = "==") -> FlagCondition:
    return FlagCondition(variable=variable, compare=op, value=value)


def _bot(states, transitions, **extra) -> BotConfig:
    return BotConfig(
        id="bot-m",
        name="Machine",
        initial=states[0].id,
        recovery_state=extra.pop("recovery_state", states[0].id),
        states=states,
        transitions=transitions,
        variables=extra.pop("variables", DECLS),
        **extra,
    )


class _Recorder:
    """A stand-in node that counts its runs and returns what it is told."""

    def __init__(self, result: str = "SUCCESS"):
        self.runs: list[str] = []
        self.result = result

    async def __call__(self, machine, clients, params):
        self.runs.append(machine.state)
        await asyncio.sleep(0)
        return self.result


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


async def _stop_node(machine, clients, params):
    machine.stop()
    return "SUCCESS"


async def _run(machine: BotMachine, timeout: float = 2.0) -> None:
    await asyncio.wait_for(machine.run(), timeout)


async def _until(predicate, timeout: float = 2.0) -> None:
    async def _wait():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(_wait(), timeout)


# ---------------------------------------------------------------------------
# Fresh values
# ---------------------------------------------------------------------------


def test_every_variable_starts_at_its_initial_value():
    machine = _machine(_bot([_state("a")], []))
    assert dict(machine.variables) == {"swords": 1, "found": False, "stage": "Sun"}


def test_the_values_are_read_only():
    machine = _machine(_bot([_state("a")], []))
    with pytest.raises(TypeError):
        machine.variables["swords"] = 5  # type: ignore[index]


async def test_a_run_starts_every_value_fresh():
    bot = _bot(
        [_set("a", {"variable": "swords", "op": "add", "value": 2}), _state("b", "done")],
        [_edge("a", "b")],
    )
    machine = _machine(bot)
    await _run(machine)
    assert machine.variables["swords"] == 3

    # A second run of the same machine starts from the declarations, not from where the first stopped.
    machine.machine.set_state("a")
    machine._stop_flag.clear()
    await _run(machine)
    assert machine.variables["swords"] == 3


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


async def _route(bot: BotConfig) -> str:
    """Where the run goes from 'start': the first state after it."""
    work = _Recorder()
    machine = _machine(bot, work=work)
    await _run(machine)
    return work.runs[0]


def _switch(*edges: TransitionModel, writes=(), options=()) -> BotConfig:
    """start (sets ``writes``) -> which (branch) -> one of the edges; every destination stops the run."""
    return _bot(
        [
            _set("start", *writes),
            _state("which", "branch"),
            _state("one"),
            _state("two"),
            _state("other"),
            _state("end", "done"),
        ],
        [
            _edge("start", "which"),
            *edges,
            _edge("which", "other"),
            _edge("one", "end"),
            _edge("two", "end"),
            _edge("other", "end"),
        ],
        options=list(options),
    )


@pytest.mark.parametrize(
    ("row", "writes", "expected"),
    [
        (_is("swords", 1), (), "one"),
        (_is("swords", 2), (), "other"),
        (_is("swords", 1, "!="), (), "other"),
        (_is("swords", 0, ">"), (), "one"),
        (_is("swords", 1, ">"), (), "other"),
        (_is("swords", 1, ">="), (), "one"),
        (_is("swords", 2, "<"), (), "one"),
        (_is("swords", 1, "<"), (), "other"),
        (_is("swords", 1, "<="), (), "one"),
        (_is("swords", 3), ({"variable": "swords", "op": "add", "value": 2},), "one"),
        (_is("found", False), (), "one"),
        (_is("found", True), ({"variable": "found", "op": "toggle"},), "one"),
        (_is("found", True, "!="), ({"variable": "found", "op": "toggle"},), "other"),
        (_is("stage", "Sun"), (), "one"),
        (_is("stage", "Moon"), ({"variable": "stage", "op": "set", "value": "Moon"},), "one"),
        (_is("stage", "Sun", "!="), ({"variable": "stage", "op": "set", "value": "Moon"},), "one"),
    ],
)
async def test_a_condition_compares_correctly_for_every_type_and_comparison(row, writes, expected):
    assert await _route(_switch(_edge("which", "one", row), writes=writes)) == expected


OPTIONS = (BotOptionModel(name="On", type="bool", default=True), BotOptionModel(name="Off", type="bool", default=False))


@pytest.mark.parametrize(
    ("groups", "groups_op", "expected"),
    [
        ([FlagGroup(flags=[_is("swords", 1), FlagCondition(option="On")], op="AND")], "OR", "one"),
        ([FlagGroup(flags=[_is("swords", 1), FlagCondition(option="Off")], op="AND")], "OR", "other"),
        ([FlagGroup(flags=[_is("swords", 9), FlagCondition(option="On")], op="OR")], "OR", "one"),
        ([FlagGroup(flags=[_is("swords", 9), FlagCondition(option="On", negate=True)], op="OR")], "OR", "other"),
        (
            [FlagGroup(flags=[_is("swords", 9)]), FlagGroup(flags=[FlagCondition(option="Off", negate=True)])],
            "OR",
            "one",
        ),
        ([FlagGroup(flags=[_is("found", False)]), FlagGroup(flags=[FlagCondition(option="Off")])], "AND", "other"),
        ([FlagGroup(flags=[_is("found", False)]), FlagGroup(flags=[FlagCondition(option="On")])], "AND", "one"),
    ],
)
async def test_variable_rows_mix_with_option_rows_in_and_or_groups(groups, groups_op, expected):
    edge = _edge("which", "one", flag_groups=groups, flag_groups_op=groups_op)
    assert await _route(_switch(edge, options=OPTIONS)) == expected


async def test_a_flat_list_mixes_them_too():
    edge = _edge("which", "one", _is("swords", 1), FlagCondition(option="Off"), flag_op="OR")
    assert await _route(_switch(edge, options=OPTIONS)) == "one"


async def test_a_switch_takes_the_matching_edge_and_otherwise_the_unconditioned_one():
    edges = (_edge("which", "one", _is("swords", 2)), _edge("which", "two", _is("swords", 3)))
    add = {"variable": "swords", "op": "add", "value": 2}
    assert await _route(_switch(*edges)) == "other"
    assert await _route(_switch(*edges, writes=(add,))) == "two"


# ---------------------------------------------------------------------------
# The one write
# ---------------------------------------------------------------------------


async def test_a_write_is_logged_once_and_reported_to_the_callback(caplog):
    changes: list[tuple] = []
    bot = _bot(
        [
            _set(
                "a", {"variable": "swords", "op": "add", "value": 1}, {"variable": "found", "op": "set", "value": False}
            ),
            _state("b", "done"),
        ],
        [_edge("a", "b")],
    )
    with caplog.at_level(logging.DEBUG, logger="bartlebot.bot.machine"):
        await _run(_machine(bot, on_variable_change=lambda *a: changes.append(a)))
    assert changes == [("swords", 1, 2)], "an unchanged value is not a change"
    assert [r.getMessage() for r in caplog.records].count("Variable swords: 1 → 2") == 1
    assert "Variable found stays false" in [r.getMessage() for r in caplog.records]


async def test_a_callback_that_raises_never_stops_the_run(caplog):
    def boom(*_):
        raise RuntimeError("panel gone")

    bot = _bot([_set("a", {"variable": "swords", "op": "add", "value": 1}), _state("b", "done")], [_edge("a", "b")])
    machine = _machine(bot, on_variable_change=boom)
    with caplog.at_level(logging.ERROR):
        await _run(machine)
    assert machine.variables["swords"] == 2
    assert any("on_variable_change callback raised" in r.getMessage() for r in caplog.records)


def test_a_write_from_outside_any_task_is_refused():
    machine = _machine(_bot([_state("a")], []))
    with pytest.raises(VariableWriteRefused):
        machine.set_variable("swords", 2)


async def test_a_write_from_a_watchers_task_is_refused():
    machine = _machine(_bot([_state("a")], []))
    with pytest.raises(VariableWriteRefused):
        await asyncio.create_task(_write_later(machine))
    assert machine.variables["swords"] == 1


async def _write_later(machine):
    machine.set_variable("swords", 2)


async def test_a_write_from_a_tasks_beside_the_running_node_is_refused():
    """A watcher runs beside the node as a task of its own, so it is refused while a run is live."""
    refused: list[BaseException] = []

    async def node(machine, clients, params):
        try:
            await asyncio.create_task(_write_later(machine))
        except VariableWriteRefused as exc:
            refused.append(exc)
        return "SUCCESS"

    bot = _bot([_state("a"), _state("b", "done")], [_edge("a", "b")])
    machine = _machine(bot, work=node)
    await _run(machine)
    assert len(refused) == 1 and machine.variables["swords"] == 1


async def test_a_write_from_a_nodes_per_client_coroutine_is_refused():
    """gather wraps each client's coroutine in a task, which is not the node's own."""

    async def per_client(machine, client):
        machine.set_variable("swords", client)

    async def node(machine, clients, params):
        results = await asyncio.gather(*[per_client(machine, c) for c in (2, 3)], return_exceptions=True)
        return "SUCCESS" if all(isinstance(r, VariableWriteRefused) for r in results) else "FAILURE"

    work = _Recorder()
    bot = _bot([_state("a", "node"), _state("b"), _state("end", "done")], [_edge("a", "b"), _edge("b", "end")])
    machine = _machine(bot, work=work)
    machine._registry["node"] = node
    await _run(machine)
    assert work.runs == ["b"], "every per-client write was refused"
    assert machine.variables["swords"] == 1


async def test_the_running_nodes_own_task_may_write_and_is_type_checked():
    outcomes: list[object] = []

    async def node(machine, clients, params):
        machine.set_variable("swords", 7)
        for name, value in (("swords", True), ("stage", "Comet"), ("ghost", 1)):
            try:
                machine.set_variable(name, value)
            except VariableError as exc:
                outcomes.append(type(exc))
        return "SUCCESS"

    machine = _machine(_bot([_state("a"), _state("b", "done")], [_edge("a", "b")]), work=node)
    await _run(machine)
    assert machine.variables["swords"] == 7
    assert outcomes == [VariableError, VariableError, VariableError]


# ---------------------------------------------------------------------------
# Nothing else changes a value
# ---------------------------------------------------------------------------


async def test_a_recovery_jump_changes_no_value():
    """An unrouted FAILURE falls through to recovery; values carry through it untouched."""
    fail_once = _Recorder("FAILURE")
    bot = _bot(
        [
            _state("recover", "branch"),
            _set("count", {"variable": "swords", "op": "add", "value": 1}),
            _state("risky"),
            _state("end", "done"),
        ],
        [
            _edge("recover", "count", _is("swords", 3, "<")),
            _edge("recover", "end"),
            _edge("count", "risky"),
            _edge("risky", "end"),  # never taken: it always fails
        ],
    )
    machine = _machine(bot, work=fail_once)
    await _run(machine)
    # recover -> count (2) -> risky FAILS -> recover -> count (3) -> risky FAILS -> recover -> end
    assert fail_once.runs == ["risky", "risky"]
    assert machine.variables["swords"] == 3


async def test_force_state_changes_no_value():
    machine = _machine(_bot([_state("a"), _state("b")], [_edge("a", "b")]))
    machine._variables["swords"] = 5
    machine.force_state("b", reason="test")
    assert machine.variables["swords"] == 5


async def test_a_set_variables_node_cancelled_for_an_ambient_fight_changes_its_values_once():
    """The watcher's cancel lands before the node's first step; the rerun writes, once."""
    changes: list[tuple] = []
    machine_ref: list[BotMachine] = []
    preempted = []

    def on_state(state_id):
        if state_id == "count" and not preempted:
            machine = machine_ref[0]
            machine.pause()
            preempted.append(asyncio.ensure_future(machine._preempt_node()))

    bot = _bot(
        [_state("a"), _set("count", {"variable": "swords", "op": "add", "value": 1}), _state("end", "done")],
        [_edge("a", "count"), _edge("count", "end")],
    )
    machine = _machine(bot, on_state_change=on_state, on_variable_change=lambda *a: changes.append(a))
    machine_ref.append(machine)
    task = asyncio.create_task(machine.run())
    await _until(lambda: preempted and preempted[0].done())
    assert preempted[0].result() is True
    assert machine.state == "count" and changes == []

    machine.resume()
    await asyncio.wait_for(task, 2.0)
    assert changes == [("swords", 1, 2)]
    assert machine.variables["swords"] == 2


def test_set_variables_never_awaits():
    """No await in the node, so a cancel can only land before it starts or after it has written."""
    import inspect

    assert "await" not in inspect.getsource(set_variables.run).split('"""')[-1]


# ---------------------------------------------------------------------------
# A loop of variable-only nodes
# ---------------------------------------------------------------------------


async def test_two_variable_only_nodes_looping_stop_the_run_with_a_reason(monkeypatch):
    monkeypatch.setattr(variables, "MAX_VARIABLE_ONLY_STEPS", 6)
    reasons: list[str] = []
    bot = _bot(
        [_set("tick", {"variable": "found", "op": "toggle"}), _state("check", "branch"), _state("end", "done")],
        [_edge("tick", "check"), _edge("check", "end", _is("swords", 99)), _edge("check", "tick")],
    )
    machine = _machine(bot, on_stopped=reasons.append)
    await _run(machine)
    assert machine.is_stopped
    assert len(reasons) == 1
    assert "7 variable-only nodes ran in a row" in reasons[0]
    assert "'tick' (Tick)" in reasons[0] and "'check' (Check)" in reasons[0]


async def test_a_node_that_touches_the_game_resets_the_count(monkeypatch):
    monkeypatch.setattr(variables, "MAX_VARIABLE_ONLY_STEPS", 3)
    reasons: list[str] = []
    bot = _bot(
        [
            _set("tick", {"variable": "swords", "op": "add", "value": 1}),
            _state("check", "branch"),
            _state("game"),
            _state("end", "done"),
        ],
        [
            _edge("tick", "check"),
            _edge("check", "end", _is("swords", 6)),
            _edge("check", "game"),
            _edge("game", "tick"),
        ],
    )
    machine = _machine(bot, on_stopped=reasons.append)
    await _run(machine)
    assert reasons == [] and machine.variables["swords"] == 6


# ---------------------------------------------------------------------------
# The nodes themselves
# ---------------------------------------------------------------------------


def test_both_nodes_are_registered_control_flow_nodes_returning_success():
    from bartlebot.nodes.registry import NODE_TYPE_TO_CATEGORY

    for name, module in (("set_variables", set_variables), ("branch", branch)):
        assert NODE_REGISTRY[name] is module.run
        assert NODE_SPECS[name] is module.NODE_SPEC
        assert NODE_TYPE_TO_CATEGORY[name] == "Control Flow"
        assert [r.name for r in module.NODE_SPEC.returns] == ["SUCCESS"]
    assert variables.VARIABLE_ONLY_NODE_TYPES == {"set_variables", "branch"}


async def test_branch_returns_success_and_touches_nothing():
    assert await branch.run(None, [], {}) == "SUCCESS"


async def test_set_variables_with_a_bad_row_fails_the_state_and_writes_nothing():
    """Validation refuses such a bot before the run; reaching one anyway raises, which the loop makes FAILURE."""
    bot = _bot(
        [
            _set("a", {"variable": "swords", "op": "add", "value": 1}, {"variable": "found", "op": "add", "value": 1}),
            _state("b", "done"),
            _state("failed", "done"),
        ],
        [_edge("a", "b"), _edge("a", "failed", when="FAILURE")],
    )
    machine = _machine(bot)
    await _run(machine)
    assert machine.state == "failed"
    assert machine.variables["swords"] == 1


# ---------------------------------------------------------------------------
# Reading a run: the condition behind a transition, and the final values
# ---------------------------------------------------------------------------


def _transitions(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Transition:")]


async def test_a_conditioned_transition_names_its_condition_and_a_plain_one_does_not(caplog):
    add = {"variable": "swords", "op": "add", "value": 1}
    bot = _switch(_edge("which", "one", _is("swords", 2)), writes=(add,))
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    assert _transitions(caplog) == [
        "Transition: 'start' (Start) --SUCCESS--> 'which' (Which)",
        "Transition: 'which' (Which) --SUCCESS [swords == 2]--> 'one' (One)",
        "Transition: 'one' (One) --SUCCESS--> 'end' (End)",
    ]


async def test_an_otherwise_edge_beside_conditioned_ones_names_no_condition(caplog):
    bot = _switch(_edge("which", "one", _is("swords", 2)), _edge("which", "two", _is("swords", 3)))
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    assert "Transition: 'which' (Which) --SUCCESS--> 'other' (Other)" in _transitions(caplog)


async def test_two_edges_to_one_node_name_the_condition_that_was_true(caplog):
    """The destination cannot tell them apart, so the edge that was taken has to say which it was."""
    edges = (
        _edge("which", "one", _is("stage", "Moon")),
        _edge("which", "one", FlagCondition(option="On"), _is("found", False)),
    )
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(_switch(*edges, options=OPTIONS)))
    assert "Transition: 'which' (Which) --SUCCESS [On AND found == false]--> 'one' (One)" in _transitions(caplog)


async def test_an_option_condition_is_named_too(caplog):
    bot = _switch(_edge("which", "one", FlagCondition(option="On")), options=OPTIONS)
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    assert "Transition: 'which' (Which) --SUCCESS [On]--> 'one' (One)" in _transitions(caplog)


async def test_a_grouped_condition_is_named_as_the_edge_label_shows_it(caplog):
    groups = [FlagGroup(flags=[_is("swords", 1), FlagCondition(option="On")]), FlagGroup(flags=[_is("found", True)])]
    edge = _edge("which", "one", flag_groups=groups, flag_groups_op="OR")
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(_switch(edge, options=OPTIONS)))
    assert "Transition: 'which' (Which) --SUCCESS [(swords == 1 AND On) OR found == true]--> 'one' (One)" in (
        _transitions(caplog)
    )


async def test_a_labeled_edge_runs():
    """An edge's label is the editor's; the machine's transitions library would refuse it as an argument."""
    bot = _bot([_state("a"), _state("b", "done")], [_edge("a", "b", label="onward")])
    machine = _machine(bot)
    await _run(machine)
    assert machine.state == "b"


async def test_the_run_summary_lists_each_variables_final_value(caplog):
    bot = _bot(
        [
            _set("a", {"variable": "swords", "op": "add", "value": 2}, {"variable": "found", "op": "toggle"}),
            _state("b", "done"),
        ],
        [_edge("a", "b")],
    )
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    assert "Variables: swords = 3, found = true, stage = Sun" in [r.getMessage() for r in caplog.records]


async def test_a_bot_without_variables_has_no_variables_line(caplog):
    bot = _bot([_state("a", "done")], [], variables=[])
    with caplog.at_level(logging.INFO, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    assert not [r for r in caplog.records if r.getMessage().startswith("Variables:")]


async def test_every_conditioned_transition_in_a_run_names_its_condition_and_every_write_is_logged_once(caplog):
    """The issue's two Done-when lines about the log, over a loop that writes and branches several times."""
    bot = _bot(
        [
            _set("count", {"variable": "swords", "op": "add", "value": 1}),
            _state("which", "branch"),
            _state("game"),
            _state("end", "done"),
        ],
        [
            _edge("count", "which"),
            _edge("which", "end", _is("swords", 4)),
            _edge("which", "game", _is("swords", 4, "<")),
            _edge("game", "count"),
        ],
    )
    with caplog.at_level(logging.DEBUG, logger="bartlebot.bot.machine"):
        await _run(_machine(bot))
    from_which = [t for t in _transitions(caplog) if t.startswith("Transition: 'which'")]
    assert from_which == [
        "Transition: 'which' (Which) --SUCCESS [swords < 4]--> 'game' (Game)",
        "Transition: 'which' (Which) --SUCCESS [swords < 4]--> 'game' (Game)",
        "Transition: 'which' (Which) --SUCCESS [swords == 4]--> 'end' (End)",
    ]
    writes = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Variable swords")]
    assert writes == ["Variable swords: 1 → 2", "Variable swords: 2 → 3", "Variable swords: 3 → 4"]
