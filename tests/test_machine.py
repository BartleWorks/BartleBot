"""Tests for BotMachine FSM logic.

Nodes are replaced with simple async mock functions so no Wizard101 client is needed.
All tests use the same two-state config: state_a <-> state_b.

Transitions use the ``conditions`` key with ``_result_is_<VALUE>`` names — this is
exactly what ``BotConfig.to_machine_config()`` produces from a ``when`` field, so
these tests exercise the same path real bots use at runtime.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.bot.client_health_monitor as health_module
from bartlebot.bot.client_health_monitor import ClientHealthMonitor
from bartlebot.bot.machine import BotMachine

# ---------------------------------------------------------------------------
# Shared config and helpers
# ---------------------------------------------------------------------------

TWO_STATE_CONFIG = {
    "initial": "state_a",
    "states": [
        {"id": "state_a", "node_type": "mock", "params": {}},
        {"id": "state_b", "node_type": "mock", "params": {}},
    ],
    "transitions": [
        # On success: advance to the other state.
        {"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_b", "dest": "state_a", "conditions": ["_result_is_SUCCESS"]},
        # On failure: retry the same state.
        {"trigger": "advance", "source": "state_a", "dest": "state_a"},
        {"trigger": "advance", "source": "state_b", "dest": "state_b"},
    ],
}


def make_machine(node_fn, on_state_change=None, on_transition=None, stop_grace_period=1.0):
    registry = {"mock": node_fn}
    return BotMachine(
        clients=[],
        config=TWO_STATE_CONFIG,
        node_registry=registry,
        on_state_change=on_state_change,
        on_transition=on_transition,
        stop_grace_period=stop_grace_period,
    )


# ---------------------------------------------------------------------------
# Dynamic _result_is_* condition methods
# ---------------------------------------------------------------------------


def test_result_is_success_true():
    machine = make_machine(lambda *_: None)
    machine.last_result = "SUCCESS"
    assert machine._result_is_SUCCESS() is True


def test_result_is_success_false_on_failure():
    machine = make_machine(lambda *_: None)
    machine.last_result = "FAILURE"
    assert machine._result_is_SUCCESS() is False


def test_result_is_success_false_on_none():
    machine = make_machine(lambda *_: None)
    machine.last_result = None
    assert machine._result_is_SUCCESS() is False


def test_result_is_failure_true():
    machine = make_machine(lambda *_: None)
    machine.last_result = "FAILURE"
    assert machine._result_is_FAILURE() is True


def test_result_is_unknown_raises():
    machine = make_machine(lambda *_: None)
    with pytest.raises(AttributeError):
        _ = machine.no_such_method


# ---------------------------------------------------------------------------
# FSM transitions
# ---------------------------------------------------------------------------


async def test_machine_advances_on_success():
    """SUCCESS result transitions state_a → state_b → state_a → ..."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 4:
            machine.stop()
        return "SUCCESS"

    await make_machine(node).run()
    assert visited == ["state_a", "state_b", "state_a", "state_b"]


async def test_machine_retries_on_failure():
    """FAILURE result keeps the machine in the same state (self-loop)."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 3:
            machine.stop()
        return "FAILURE"

    machine = make_machine(node)
    await machine.run()
    assert all(s == "state_a" for s in visited)


async def test_machine_transitions_after_failure_then_success():
    """FAILURE → FAILURE → SUCCESS should eventually advance to state_b."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if machine.state == "state_b":
            machine.stop()
            return "SUCCESS"
        # First visit to state_a: fail; second visit: succeed
        return "FAILURE" if visited.count("state_a") == 1 else "SUCCESS"

    machine = make_machine(node)
    await machine.run()
    assert visited == ["state_a", "state_a", "state_b"]
    assert machine.state == "state_b"


# ---------------------------------------------------------------------------
# Pause / resume behaviour
# ---------------------------------------------------------------------------


async def test_is_paused_reflects_state():
    """is_paused returns True after pause() and False after resume()."""
    machine = make_machine(lambda *_: None)
    assert not machine.is_paused
    machine.pause()
    assert machine.is_paused
    machine.resume()
    assert not machine.is_paused


async def test_pause_before_run_suspends_loop():
    """pause() called before run() blocks the loop until resume() is called."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        machine.stop()
        return "SUCCESS"

    machine = make_machine(node)
    machine.pause()

    run_task = asyncio.create_task(machine.run())
    await asyncio.sleep(0)  # yield so the task can start

    assert visited == [], "node should not execute while paused"
    machine.resume()
    await run_task
    assert visited == ["state_a"]


async def test_pause_halts_after_current_node():
    """pause() during a node prevents the next iteration from starting."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] == 1:
            machine.pause()
        return "SUCCESS"

    machine = make_machine(node)
    run_task = asyncio.create_task(machine.run())
    await asyncio.sleep(0.05)  # give the loop time to pause

    assert machine.is_paused
    assert calls[0] == 1
    machine.stop()
    machine.resume()
    await run_task


async def test_stop_while_paused_exits_cleanly():
    """stop() while paused causes run() to return without hanging."""

    async def node(machine, clients, params):
        return "SUCCESS"

    machine = make_machine(node)
    machine.pause()
    run_task = asyncio.create_task(machine.run())
    await asyncio.sleep(0)  # let task block on resume_event

    machine.stop()
    await asyncio.wait_for(run_task, timeout=1.0)


# ---------------------------------------------------------------------------
# Stop behaviour
# ---------------------------------------------------------------------------


async def test_stop_halts_after_current_node():
    """stop() set during a node prevents the next advance."""

    async def node(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    machine = make_machine(node)
    await machine.run()  # Should return without hanging
    assert machine.state == "state_a"  # advance was skipped


async def test_stop_before_run_exits_immediately():
    """If stop() is called before run(), the loop should not execute any node."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        return "SUCCESS"

    machine = make_machine(node)
    machine.stop()
    await machine.run()
    assert visited == []


async def test_run_can_be_stopped_externally():
    """stop() halts the FSM even when a node is blocking, within the grace period."""
    started = asyncio.Event()

    async def blocking_node(machine, clients, params):
        started.set()
        await asyncio.sleep(30)  # Simulate long-running node
        return "SUCCESS"

    # Use a short grace period so the test runs quickly.
    machine = make_machine(blocking_node, stop_grace_period=0.1)
    task = asyncio.create_task(machine.run())
    await started.wait()
    machine.stop()
    # The deferred cancel fires after 0.1 s; run() should exit cleanly without
    # needing an external task.cancel().
    await asyncio.wait_for(task, timeout=1.0)


async def test_stop_before_grace_period_exits_cleanly_if_node_cooperates():
    """A node that checks is_stopped exits within the grace period without hard cancellation."""
    exited_cleanly = False

    async def cooperative_node(machine, clients, params):
        nonlocal exited_cleanly
        for _ in range(100):
            if machine.is_stopped:
                exited_cleanly = True
                return "FAILURE"
            await asyncio.sleep(0.01)
        return "SUCCESS"

    machine = make_machine(cooperative_node, stop_grace_period=5.0)
    task = asyncio.create_task(machine.run())
    await asyncio.sleep(0.05)
    machine.stop()
    await asyncio.wait_for(task, timeout=1.0)
    assert exited_cleanly


# ---------------------------------------------------------------------------
# on_state_change callback
# ---------------------------------------------------------------------------


async def test_on_state_change_called_for_each_state():
    changes = []

    async def node(machine, clients, params):
        if len(changes) >= 3:
            machine.stop()
        return "SUCCESS"

    await make_machine(node, on_state_change=changes.append).run()
    assert changes[:2] == ["state_a", "state_b"]


# ---------------------------------------------------------------------------
# on_transition callback
# ---------------------------------------------------------------------------


async def test_on_transition_args_on_success():
    """on_transition receives (prev, 'SUCCESS', next) for each successful advance."""
    log = []
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 3:
            machine.stop()
        return "SUCCESS"

    await make_machine(node, on_transition=lambda p, r, n: log.append((p, r, n))).run()
    # Third call sets stop before advance, so only two transitions are logged.
    assert log == [
        ("state_a", "SUCCESS", "state_b"),
        ("state_b", "SUCCESS", "state_a"),
    ]


async def test_on_transition_args_on_failure_retry():
    """On FAILURE, on_transition is called with prev_state == next_state (self-loop)."""
    log = []
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 3:
            machine.stop()
        return "FAILURE"

    await make_machine(node, on_transition=lambda p, r, n: log.append((p, r, n))).run()
    assert log == [
        ("state_a", "FAILURE", "state_a"),
        ("state_a", "FAILURE", "state_a"),
    ]


async def test_on_transition_not_called_when_stop_set():
    """on_transition is skipped entirely when stop() is called inside the node."""
    called = []

    async def node(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    await make_machine(node, on_transition=lambda *a: called.append(a)).run()
    assert called == []


async def test_on_transition_receives_failure_after_node_exception():
    """An unhandled node exception is surfaced to on_transition as FAILURE."""
    log = []
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("crash")
        machine.stop()
        return "SUCCESS"

    await make_machine(node, on_transition=lambda p, r, n: log.append((p, r, n))).run()
    # Exception on first call → FAILURE self-loop; second call stops before advance.
    assert log == [("state_a", "FAILURE", "state_a")]


# ---------------------------------------------------------------------------
# Callback exception resilience
# ---------------------------------------------------------------------------


async def test_on_state_change_exception_does_not_crash_fsm():
    """A raising on_state_change must not kill the FSM loop."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 2:
            machine.stop()
        return "SUCCESS"

    def bad_callback(state_id):
        raise RuntimeError("GUI crash")

    await make_machine(node, on_state_change=bad_callback).run()
    assert calls[0] >= 2


async def test_on_transition_exception_does_not_crash_fsm():
    """A raising on_transition must not kill the FSM loop."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 2:
            machine.stop()
        return "SUCCESS"

    def bad_callback(prev, result, next_):
        raise RuntimeError("GUI crash")

    await make_machine(node, on_transition=bad_callback).run()
    assert calls[0] >= 2


# ---------------------------------------------------------------------------
# Node exception handling
# ---------------------------------------------------------------------------


async def test_node_exception_results_in_failure():
    """An unhandled exception in a node should be treated as FAILURE."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 2:
            machine.stop()
            return "SUCCESS"
        raise RuntimeError("Simulated node crash")

    machine = make_machine(node)
    await machine.run()
    # First call raised → FAILURE → self-loop back to state_a
    # Second call returned SUCCESS → would advance, but stop was set first
    assert calls[0] == 2


# ---------------------------------------------------------------------------
# Loading screen watcher
# ---------------------------------------------------------------------------


async def test_loading_screen_watcher_fires_on_timeout():
    """_loading_screen_watcher calls on_client_disconnected and on_pause when a
    client is stuck loading past LOADING_SCREEN_TIMEOUT."""
    fake_hwnd = 12345
    fake_client = AsyncMock()
    fake_client.window_handle = fake_hwnd
    fake_client.is_loading.return_value = True

    paused: list[bool] = []
    disconnected: list[int] = []

    stop = asyncio.Event()
    monitor = ClientHealthMonitor(
        clients_ref=lambda: [fake_client],
        stop_flag=stop,
        on_pause=lambda: paused.append(True),
        on_client_disconnected=lambda hwnd: disconnected.append(hwnd),
    )

    with (
        patch.object(health_module, "LOADING_SCREEN_TIMEOUT", 0.0),
        patch.object(health_module, "POLL_INTERVAL", 0.0),
    ):
        task = asyncio.create_task(monitor._loading_screen_watcher())
        await asyncio.sleep(0.05)  # let the watcher fire at least once
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert disconnected == [fake_hwnd], "on_client_disconnected should be called with the stuck client's hwnd"
    assert paused, "on_pause should be called after loading screen timeout"
    assert fake_hwnd in monitor._flagged, "hwnd should be flagged to prevent duplicate callbacks"


async def test_loading_screen_watcher_resets_when_loading_clears():
    """_loading_screen_watcher does not fire if loading clears before the timeout."""
    fake_hwnd = 99999
    fake_client = AsyncMock()
    fake_client.window_handle = fake_hwnd
    # Returns True once, then False — loading clears before timeout
    fake_client.is_loading.side_effect = [True, False]

    disconnected: list[int] = []

    stop = asyncio.Event()
    monitor = ClientHealthMonitor(
        clients_ref=lambda: [fake_client],
        stop_flag=stop,
        on_pause=lambda: None,
        on_client_disconnected=lambda hwnd: disconnected.append(hwnd),
    )

    # Timeout is generous; loading clears after one tick, watcher should not fire.
    with (
        patch.object(health_module, "LOADING_SCREEN_TIMEOUT", 999.0),
        patch.object(health_module, "POLL_INTERVAL", 0.0),
    ):
        task = asyncio.create_task(monitor._loading_screen_watcher())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert disconnected == [], "on_client_disconnected must not fire when loading clears in time"


# ---------------------------------------------------------------------------
# Client status watcher
# ---------------------------------------------------------------------------


def _status_monitor(client, paused: list, on_disconnected) -> ClientHealthMonitor:
    return ClientHealthMonitor(
        clients_ref=lambda: [client],
        stop_flag=asyncio.Event(),
        on_pause=lambda: paused.append(True),
        on_client_disconnected=on_disconnected,
    )


async def _run_status_watcher(monitor: ClientHealthMonitor, duration: float = 0.05) -> None:
    with patch.object(health_module, "_CLIENT_STATUS_CHECK_INTERVAL", 0.0):
        task = asyncio.create_task(monitor._client_status_watcher())
        await asyncio.sleep(duration)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_client_status_watcher_reports_a_client_that_keeps_failing_once():
    """A client whose zone_name() keeps failing is reported, then left to the recovery."""
    client = AsyncMock()
    client.window_handle = 4242
    client.zone_name.side_effect = RuntimeError("process gone")
    paused: list[bool] = []
    disconnected: list[int] = []

    monitor = _status_monitor(client, paused, disconnected.append)
    await _run_status_watcher(monitor)

    assert disconnected == [4242]
    assert paused == [True]
    assert monitor.is_flagged(4242)
    # Flagged clients are skipped, so the check stops at the threshold instead of re-firing every tick.
    assert client.zone_name.await_count == health_module._CLIENT_STATUS_CHECK_FAIL_THRESHOLD


async def test_client_status_watcher_needs_the_failures_to_be_consecutive():
    """A slow client that still answers now and then is not dead."""
    threshold = health_module._CLIENT_STATUS_CHECK_FAIL_THRESHOLD
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls % threshold == 0:
            return "WizardCity/WC_Hub"
        raise RuntimeError("slow read")

    client = AsyncMock()
    client.window_handle = 4343
    client.zone_name.side_effect = flaky
    paused: list[bool] = []
    disconnected: list[int] = []

    monitor = _status_monitor(client, paused, disconnected.append)
    await _run_status_watcher(monitor)

    assert calls > threshold, "enough checks must run for a count that never reset to have tripped"
    assert disconnected == []
    assert paused == []


async def test_client_status_watcher_survives_a_failing_disconnect_callback(caplog):
    client = AsyncMock()
    client.window_handle = 4444
    client.zone_name.side_effect = RuntimeError("process gone")
    paused: list[bool] = []

    def explode(_hwnd):
        raise RuntimeError("gui blew up")

    monitor = _status_monitor(client, paused, explode)
    with caplog.at_level(logging.ERROR, logger=health_module.__name__):
        await _run_status_watcher(monitor)

    assert paused == [True]
    assert monitor.is_flagged(4444)
    assert "on_client_disconnected callback raised" in caplog.text


# ---------------------------------------------------------------------------
# Silent fall-through / no-match detection
# ---------------------------------------------------------------------------

# Config with only conditional transitions — no unconditional fallback.
# If a node returns "PARTIAL", no transition condition matches and advance()
# returns False, triggering the no-match detection logic.
CONDITIONAL_ONLY_CONFIG = {
    "initial": "state_a",
    "states": [
        {"id": "state_a", "node_type": "mock", "params": {}},
        {"id": "state_b", "node_type": "mock", "params": {}},
    ],
    "transitions": [
        {"trigger": "advance", "source": "state_a", "dest": "state_b", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_a", "dest": "state_a", "conditions": ["_result_is_FAILURE"]},
        {"trigger": "advance", "source": "state_b", "dest": "state_a", "conditions": ["_result_is_SUCCESS"]},
        {"trigger": "advance", "source": "state_b", "dest": "state_b", "conditions": ["_result_is_FAILURE"]},
    ],
}


def make_machine_conditional(node_fn):
    registry = {"mock": node_fn}
    return BotMachine(clients=[], config=CONDITIONAL_ONLY_CONFIG, node_registry=registry)


async def test_no_match_logs_warning_and_increments_counter(caplog):
    """A node returning an unmapped value logs a warning and increments the counter."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 3:
            machine.stop()
        return "PARTIAL"  # not mapped to any transition condition

    with caplog.at_level(logging.WARNING, logger="bartlebot.bot.machine"):
        machine = make_machine_conditional(node)
        await machine.run()

    assert machine._no_match_count == 2  # stopped before 3rd advance fires
    assert any("No transition matched" in r.message for r in caplog.records)
    assert any("PARTIAL" in r.message for r in caplog.records)


async def test_no_match_stops_after_threshold(caplog):
    """After 10 consecutive no-matches the machine logs an ERROR and stops."""

    async def node(machine, clients, params):
        return "PARTIAL"

    with caplog.at_level(logging.ERROR, logger="bartlebot.bot.machine"):
        machine = make_machine_conditional(node)
        await machine.run()

    assert machine._stop_flag.is_set()
    assert machine._no_match_count == 10
    assert any("stuck after" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Per-state idle detection (max_consecutive_retries / max_consecutive_same_state)
# ---------------------------------------------------------------------------

SELF_LOOP_ONLY_CONFIG = {
    "initial": "state_a",
    "states": [
        {"id": "state_a", "node_type": "mock", "params": {}, "max_consecutive_retries": 3},
    ],
    "transitions": [
        {"trigger": "advance", "source": "state_a", "dest": "state_a"},
    ],
}


async def test_consecutive_same_state_stops_at_per_state_limit(caplog):
    """Machine stops after max_consecutive_retries self-loop ticks on one state."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        return "FAILURE"  # always self-loops

    with caplog.at_level(logging.WARNING, logger="bartlebot.bot.machine"):
        registry = {"mock": node}
        machine = BotMachine(clients=[], config=SELF_LOOP_ONLY_CONFIG, node_registry=registry)
        await machine.run()

    assert machine._stop_flag.is_set()
    assert calls[0] == 3
    assert machine._consecutive_same_state_count == 3
    assert any("consecutive ticks without advancing" in r.message for r in caplog.records)


async def test_consecutive_same_state_resets_on_state_change():
    """Counter resets when the FSM advances to a different state."""
    visited = []

    async def node(machine, clients, params):
        visited.append(machine.state)
        if len(visited) >= 6:
            machine.stop()
        return "SUCCESS"  # always advances to the other state

    # machine-level limit of 3; alternating states means counter always resets to 0
    registry = {"mock": node}
    machine = BotMachine(
        clients=[],
        config=TWO_STATE_CONFIG,
        node_registry=registry,
        max_consecutive_same_state=3,
    )
    await machine.run()

    assert machine._stop_flag.is_set()
    assert machine._consecutive_same_state_count == 0  # reset on last state change
    assert len(visited) == 6


async def test_consecutive_same_state_none_disables():
    """When no limit is set, the counter never triggers a stop."""
    calls = [0]

    async def node(machine, clients, params):
        calls[0] += 1
        if calls[0] >= 5:
            machine.stop()
        return "FAILURE"  # self-loops via the unconditional transition

    registry = {"mock": node}
    # Use a config without max_consecutive_retries on the state and no machine-level limit
    config = {
        "initial": "state_a",
        "states": [{"id": "state_a", "node_type": "mock", "params": {}}],
        "transitions": [{"trigger": "advance", "source": "state_a", "dest": "state_a"}],
    }
    machine = BotMachine(clients=[], config=config, node_registry=registry)
    await machine.run()

    # Stopped by the node, not by the idle check
    assert calls[0] == 5
    assert machine._consecutive_same_state_count == 4  # 4 self-loops before node stopped on 5th
