"""The canvas side of the default failure edge: a port that routes without one.

An unrouted failure is not drawn as an edge -- 28% of the shipped library's edges
were exactly that, nearly all of them long backward edges into one hub node. The
port wears the recovery badge instead, and the node at the other end already wore
it. See docs/RETURN_ROUTING.md.
"""

import pytest

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel
from bartlebot.gui.canvas.edit_scene import (
    _reachable_states,
    _validate_node,
    port_marks_for,
)
from bartlebot.gui.canvas.node_item import PORT_MARK_RECOVERY, PORT_MARK_STOP


def _state(sid: str, node_type: str = "sync_in_zone", **kwargs) -> StateModel:
    return StateModel(id=sid, node_type=node_type, params={"zone": "WizardCity/WC_Hub"}, **kwargs)


def _edge(source: str, dest: str, when: str | None = "SUCCESS", **kwargs) -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when=when, **kwargs)


def _config(states, transitions, recovery="state_r") -> BotConfig:
    return BotConfig(
        id="test-bot",
        name="Test",
        initial="state_a",
        recovery_state=recovery,
        states=states,
        transitions=transitions,
    )


@pytest.fixture
def config() -> BotConfig:
    """state_a routes only SUCCESS; state_r is the recovery node."""
    return _config(
        [_state("state_a"), _state("state_b"), _state("state_r", "teleport_to_world_hub")],
        [_edge("state_a", "state_b"), _edge("state_r", "state_a")],
    )


# ---------------------------------------------------------------------------
# port_marks_for
# ---------------------------------------------------------------------------


def test_an_unrouted_failure_port_wears_the_recovery_mark(config):
    assert port_marks_for(config.states[0], config) == {"FAILURE": PORT_MARK_RECOVERY}


def test_a_routed_failure_port_is_unmarked(config):
    config.transitions.append(_edge("state_a", "state_b", when="FAILURE"))
    assert port_marks_for(config.states[0], config) == {}


def test_an_unconditional_edge_covers_every_port(config):
    """A when=None edge fires on any result, so nothing is left to default."""
    config.transitions.append(_edge("state_a", "state_b", when=None))
    assert port_marks_for(config.states[0], config) == {}


def test_the_recovery_states_own_failure_is_unmarked(config):
    """Defaulting it would loop the node onto itself — the engine declines it too."""
    assert port_marks_for(config.states[2], config) == {}


def test_a_terminal_states_ports_are_unmarked():
    """No outgoing transitions at all means terminal, which stops rather than recovers."""
    config = _config(
        [_state("state_a"), _state("state_r", "teleport_to_world_hub")],
        [_edge("state_r", "state_a")],
    )
    assert port_marks_for(config.states[0], config) == {}


def test_a_marked_stop_wins_over_the_recovery_mark(config):
    config.states[0].stops = {"FAILURE": "Wrong world."}
    assert port_marks_for(config.states[0], config) == {"FAILURE": PORT_MARK_STOP}


def test_a_stop_is_marked_on_a_success_port_too(config):
    """The stop is the author's statement, so it is not limited to the failure names."""
    config.states[0].stops = {"SUCCESS": ""}
    assert port_marks_for(config.states[0], config)["SUCCESS"] == PORT_MARK_STOP


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_an_unrouted_failure_no_longer_warns(config):
    _is_error, is_warning, _errs, warns = _validate_node(config.states[0], config)
    assert not is_warning, warns


def test_an_unrouted_success_still_warns():
    config = _config(
        [_state("state_a"), _state("state_b"), _state("state_r", "teleport_to_world_hub")],
        [_edge("state_a", "state_b", when="FAILURE"), _edge("state_r", "state_a")],
    )
    _is_error, is_warning, _errs, warns = _validate_node(config.states[0], config)
    assert is_warning
    assert any("SUCCESS" in w for w in warns)


def test_the_recovery_states_own_uncovered_failure_still_warns(config):
    _is_error, is_warning, _errs, warns = _validate_node(config.states[2], config)
    assert is_warning
    assert any("FAILURE" in w for w in warns)


def test_a_stopped_port_does_not_warn(config):
    config.states[2].stops = {"FAILURE": "Cannot reach the hub."}
    _is_error, is_warning, _errs, warns = _validate_node(config.states[2], config)
    assert not is_warning, warns


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


def test_the_recovery_node_is_reachable_through_the_default():
    """Nothing points at it by edge, but every failure in the graph arrives there.

    Without this the one node reachable from everywhere would be warned as
    unreachable, and ``_cleanup_orphans`` would offer to delete it.
    """
    config = _config(
        [_state("state_a"), _state("state_r", "teleport_to_world_hub")],
        [_edge("state_a", "state_a")],
    )
    assert "state_r" in _reachable_states(config)


def test_a_genuinely_orphaned_state_is_still_unreachable():
    config = _config(
        [_state("state_a"), _state("state_b"), _state("state_r", "teleport_to_world_hub")],
        [_edge("state_a", "state_a"), _edge("state_r", "state_a")],
    )
    assert "state_b" not in _reachable_states(config)
