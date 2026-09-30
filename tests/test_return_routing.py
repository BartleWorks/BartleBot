"""What the FSM does with a return value nothing routes anywhere.

Two halves of one design (issue #11): an unrouted FAILURE/FALSE falls through to
the recovery state instead of spinning to the no-match limit, and a return value
the author marked as a stop ends the run with a reason the user is shown. See
docs/RETURN_ROUTING.md.
"""

import asyncio

from bartlebot.bot.machine import BotMachine

# state_a advances to state_b on SUCCESS and routes nothing else. state_c is the
# recovery state, and its own FAILURE is deliberately unrouted too -- the one port
# the fall-through must not take.
CONFIG = {
    "initial": "state_a",
    "recovery_state": "state_c",
    "states": [
        {"id": "state_a", "node_type": "mock", "params": {}},
        {"id": "state_b", "node_type": "mock", "params": {}},
        {"id": "state_c", "node_type": "mock", "params": {}},
    ],
    "transitions": [
        {"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_b", "dest": "state_a", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_c", "dest": "state_a", "conditions": ["_result_is_SUCCESS"]},
    ],
}


def make_machine(node_fn, config=None, **kwargs):
    return BotMachine(
        clients=[],
        config=config or CONFIG,
        node_registry={"mock": node_fn},
        stop_grace_period=0.01,
        **kwargs,
    )


def _with_stops(state_id: str, stops: dict[str, str]) -> dict:
    """CONFIG with *stops* attached to one state, the way to_machine_config emits them."""
    config = {**CONFIG, "states": [dict(s) for s in CONFIG["states"]]}
    for state in config["states"]:
        if state["id"] == state_id:
            state["stops"] = stops
    return config


# ---------------------------------------------------------------------------
# Fall-through to the recovery state
# ---------------------------------------------------------------------------


async def test_an_unrouted_failure_goes_to_the_recovery_state():
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 2:
            machine.stop()
        return "FAILURE"

    machine = make_machine(node)
    await machine.run()
    assert visited == ["state_a", "state_c"]


async def test_an_unrouted_false_falls_through_too():
    """FALSE is a failure name as much as FAILURE is — condition nodes use it."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 2:
            machine.stop()
        return "FALSE"

    machine = make_machine(node)
    await machine.run()
    assert visited == ["state_a", "state_c"]


async def test_an_explicit_failure_edge_still_wins():
    """The default is what happens when nothing is routed, not an override."""
    config = {
        **CONFIG,
        "transitions": [
            *CONFIG["transitions"],
            {"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["_result_is_FAILURE"]},
        ],
    }
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 2:
            machine.stop()
        return "FAILURE"

    machine = make_machine(node, config=config)
    await machine.run()
    assert visited == ["state_a", "state_b"]


async def test_an_unrouted_success_does_not_fall_through():
    """Only the two failure names default. A SUCCESS landing on recovery is nonsense."""
    config = {
        **CONFIG,
        "transitions": [{"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["_result_is_X"]}],
    }
    seen = []

    async def node(machine, clients, params):
        seen.append(machine.state)
        if len(seen) >= 3:
            machine.stop()
        return "SUCCESS"

    machine = make_machine(node, config=config)
    await machine.run()
    assert seen == ["state_a", "state_a", "state_a"]


async def test_the_recovery_state_does_not_fall_through_to_itself():
    """Defaulting the recovery node's own failure would loop it onto itself forever."""
    config = {**CONFIG, "initial": "state_c"}
    seen = []

    async def node(machine, clients, params):
        seen.append(machine.state)
        if len(seen) >= 3:
            machine.stop()
        return "FAILURE"

    machine = make_machine(node, config=config)
    await machine.run()
    # Three plain no-match ticks on state_c, never a jump to itself.
    assert seen == ["state_c"] * 3
    assert machine.state == "state_c"


async def test_a_terminal_state_still_stops_rather_than_recovering():
    """No outgoing transitions at all means terminal, which outranks the default."""
    config = {
        **CONFIG,
        "transitions": [{"trigger": "advance", "source": "state_c", "dest": "state_a"}],
    }
    ticks = []

    async def node(machine, clients, params):
        ticks.append(machine.state)
        return "FAILURE"

    machine = make_machine(node, config=config)
    await asyncio.wait_for(machine.run(), timeout=2)
    assert ticks == ["state_a"]
    assert machine.is_stopped


async def test_a_config_without_a_recovery_state_keeps_the_old_no_match_behavior():
    """A hand-built config (a test, a fixture) simply has the default switched off."""
    config = {k: v for k, v in CONFIG.items() if k != "recovery_state"}
    seen = []

    async def node(machine, clients, params):
        seen.append(machine.state)
        if len(seen) >= 3:
            machine.stop()
        return "FAILURE"

    machine = make_machine(node, config=config)
    await machine.run()
    assert seen == ["state_a"] * 3


# ---------------------------------------------------------------------------
# Explicit stop
# ---------------------------------------------------------------------------


async def test_a_marked_return_stops_the_run_and_reports_its_reason():
    reported = []

    async def node(machine, clients, params):
        return "FAILURE"

    machine = make_machine(
        node,
        config=_with_stops("state_a", {"FAILURE": "Wrong world."}),
        on_explicit_stop=lambda reason, label, result: reported.append((reason, result)),
    )
    await asyncio.wait_for(machine.run(), timeout=2)
    assert machine.is_stopped
    assert reported == [("Wrong world.", "FAILURE")]


async def test_a_stop_with_no_reason_is_still_a_stop():
    """An empty reason is a real answer, so the check is against None, not truthiness."""
    reported = []

    async def node(machine, clients, params):
        return "FAILURE"

    machine = make_machine(
        node,
        config=_with_stops("state_a", {"FAILURE": ""}),
        on_explicit_stop=lambda reason, label, result: reported.append(reason),
    )
    await asyncio.wait_for(machine.run(), timeout=2)
    assert machine.is_stopped
    assert reported == [""]


async def test_a_stop_outranks_the_edge_that_would_have_fired():
    """Otherwise an author could not make a routed return fatal without deleting the edge."""

    async def node(machine, clients, params):
        return "SUCCESS"

    machine = make_machine(node, config=_with_stops("state_a", {"SUCCESS": "Done."}))
    await asyncio.wait_for(machine.run(), timeout=2)
    assert machine.is_stopped
    assert machine.state == "state_a"


async def test_an_unmarked_return_value_is_unaffected():
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 2:
            machine.stop()
        return "SUCCESS"

    machine = make_machine(node, config=_with_stops("state_a", {"FAILURE": "Wrong world."}))
    await machine.run()
    assert visited == ["state_a", "state_b"]


async def test_a_raising_stop_callback_does_not_take_the_fsm_with_it():
    async def node(machine, clients, params):
        return "FAILURE"

    def boom(reason, label, result):
        raise RuntimeError("GUI blew up")

    machine = make_machine(node, config=_with_stops("state_a", {"FAILURE": "x"}), on_explicit_stop=boom)
    await asyncio.wait_for(machine.run(), timeout=2)
    assert machine.is_stopped
