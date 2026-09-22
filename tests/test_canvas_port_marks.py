"""The canvas end-to-end: marks reach the node items and follow every edit.

The unit-level rules live in test_port_marks.py. What these cover is the wiring —
a mark is recomputed from the config rather than tracked, so the risk is a path
that changes the graph and forgets to ask again.
"""

import pytest

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel
from bartlebot.gui.canvas.node_item import PORT_MARK_RECOVERY, PORT_MARK_STOP


def _config() -> BotConfig:
    """state_a routes only SUCCESS; state_r is the recovery node."""
    return BotConfig(
        id="test-bot",
        name="t",
        initial="state_a",
        recovery_state="state_r",
        states=[
            StateModel(id="state_a", node_type="go_to_zone", params={"zone": "WizardCity/WC_Hub"}),
            StateModel(id="state_b", node_type="go_to_zone", params={"zone": "WizardCity/WC_Hub"}),
            StateModel(id="state_r", node_type="teleport_to_world_hub", params={}),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="state_a", dest="state_b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="state_b", dest="state_a", when="SUCCESS"),
            TransitionModel(trigger="advance", source="state_r", dest="state_a", when="SUCCESS"),
        ],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    c = BotCanvas()
    c.setup(_config())
    c.enter_edit_mode()
    yield c
    c.prepare_for_shutdown()


def _marks(canvas, state_id):
    return canvas.scene.node_items[state_id].port_marks


def test_setup_marks_the_unrouted_failure_ports(canvas):
    assert _marks(canvas, "state_a") == {"FAILURE": PORT_MARK_RECOVERY}
    assert _marks(canvas, "state_b") == {"FAILURE": PORT_MARK_RECOVERY}
    assert _marks(canvas, "state_r") == {}


def test_moving_the_recovery_role_moves_every_mark(canvas):
    """The new recovery node loses the mark on its own failure; the old one gains it."""
    canvas.config.recovery_state = "state_b"
    canvas.refresh_recovery("state_b")

    assert _marks(canvas, "state_b") == {}
    assert _marks(canvas, "state_r") == {"FAILURE": PORT_MARK_RECOVERY}


def test_routing_the_port_clears_its_mark(canvas):
    canvas.config.transitions.append(
        TransitionModel(trigger="advance", source="state_a", dest="state_b", when="FAILURE")
    )
    canvas.scene.refresh_port_marks()

    assert _marks(canvas, "state_a") == {}


def test_marking_a_stop_through_the_scene_redraws_the_port(canvas):
    canvas.scene._set_port_stop("state_a", "FAILURE", "Wrong world.")

    assert _marks(canvas, "state_a") == {"FAILURE": PORT_MARK_STOP}
    assert canvas.config.states[0].stops == {"FAILURE": "Wrong world."}


def test_clearing_the_last_stop_leaves_no_key_behind(canvas):
    """``stops`` goes back to None so the key stays out of the saved file."""
    canvas.scene._set_port_stop("state_a", "FAILURE", "Wrong world.")
    canvas.scene._set_port_stop("state_a", "FAILURE", None)

    assert canvas.config.states[0].stops is None
    assert _marks(canvas, "state_a") == {"FAILURE": PORT_MARK_RECOVERY}


def test_a_stop_survives_a_save_round_trip(tmp_path, canvas):
    canvas.scene._set_port_stop("state_a", "FAILURE", "Wrong world.")
    path = tmp_path / "bot.json"
    canvas.config.save(path)

    assert BotConfig.load(path).states[0].stop_reason("FAILURE") == "Wrong world."


def test_a_bot_with_no_stops_writes_no_stops_key(tmp_path, canvas):
    """Otherwise every state in all 87 shipped bots would grow a line of noise."""
    path = tmp_path / "bot.json"
    canvas.config.save(path)

    assert "stops" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The failure-edge toggle
# ---------------------------------------------------------------------------


def _visible_failure_edges(canvas):
    return [(s, d) for s, w, d, ei in canvas.scene.edge_items if w in ("FAILURE", "FALSE") and ei.isVisible()]


def test_the_middle_mode_hides_only_the_edges_the_default_would_have_drawn(canvas):
    """An explicit failure edge into the recovery node says what the default says."""
    canvas.config.transitions += [
        TransitionModel(trigger="advance", source="state_a", dest="state_r", when="FAILURE"),
        TransitionModel(trigger="advance", source="state_b", dest="state_b", when="FAILURE"),
    ]
    canvas.scene.setup(canvas.config)

    canvas._set_failure_edge_mode("all", persist=False)
    assert sorted(_visible_failure_edges(canvas)) == [("state_a", "state_r"), ("state_b", "state_b")]

    canvas._set_failure_edge_mode("hide_recovery", persist=False)
    assert _visible_failure_edges(canvas) == [("state_b", "state_b")]

    canvas._set_failure_edge_mode("none", persist=False)
    assert _visible_failure_edges(canvas) == []


def test_the_button_cycles_through_all_three_modes(canvas):
    from bartlebot.gui.canvas.bot_canvas import _FAILURE_EDGE_CYCLE

    seen = [canvas.failure_edge_mode]
    for _ in range(len(_FAILURE_EDGE_CYCLE)):
        canvas._cycle_failure_edge_mode()
        seen.append(canvas.failure_edge_mode)

    assert seen == [*_FAILURE_EDGE_CYCLE, _FAILURE_EDGE_CYCLE[0]]
