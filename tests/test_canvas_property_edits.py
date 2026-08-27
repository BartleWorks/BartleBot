"""Regression tests for edits made from the properties panel (issues #121, #64).

Both bugs share a shape: an edit the panel writes straight into the model never
reached the code that redraws the graph.  Changing a node type left the canvas
showing the old type (#121); rewiring an edge into a self-loop left it drawing
its old two-node path (#64).

These are real widget tests (QApplication required), in the style of
test_combat_tab.  Only the wiring is exercised — nothing reads pixels.
"""

import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _config(transitions=None):
    return BotConfig(
        name="t",
        initial="a",
        states=[StateModel(id=sid, node_type="change_realm", params={}) for sid in ("a", "b", "c")],
        transitions=transitions
        if transitions is not None
        else [
            TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="b", dest="c", when="SUCCESS"),
        ],
    )


def _canvas(qapp, config):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    canvas = BotCanvas()
    canvas.setup(config)
    canvas.enter_edit_mode()
    return canvas


def _edge(scene, src, when, dst):
    return next(e for s, w, d, e in scene.edge_items if (s, w, d) == (src, when, dst))


# ---------------------------------------------------------------------------
# #121 — node type dropdown
# ---------------------------------------------------------------------------


def test_changing_node_type_updates_the_canvas_node(qapp):
    canvas = _canvas(qapp, _config())
    scene = canvas.scene
    node = scene.node_items["a"]
    panel = canvas.param_panel
    config = scene.config

    panel.show_state(config.states[0], all_states=config.states, all_transitions=config.transitions)
    idx = panel.node_type_combo.findData("click_path", Qt.ItemDataRole.UserRole)
    assert idx >= 0
    panel.node_type_combo.setCurrentIndex(idx)

    assert config.states[0].node_type == "click_path"
    assert node.node_type == "click_path"


def test_changing_node_type_updates_the_output_ports(qapp):
    """A type with different returns has to redraw its ports, not just its label."""
    canvas = _canvas(qapp, _config())
    scene = canvas.scene
    node = scene.node_items["a"]
    panel = canvas.param_panel
    config = scene.config

    panel.show_state(config.states[0], all_states=config.states, all_transitions=config.transitions)
    idx = panel.node_type_combo.findData("check_health", Qt.ItemDataRole.UserRole)
    if idx < 0:  # condition nodes are the only ones with non-SUCCESS/FAILURE returns
        pytest.skip("no condition node registered to switch to")
    panel.node_type_combo.setCurrentIndex(idx)

    assert node.returns == ["TRUE", "FALSE"]


# ---------------------------------------------------------------------------
# #64 — edge endpoint dropdowns
# ---------------------------------------------------------------------------


def test_rewiring_an_edge_into_a_self_loop_redraws_it(qapp):
    """_recompute_offsets skipped loops, so the edge kept its old two-node path."""
    canvas = _canvas(qapp, _config())
    scene = canvas.scene
    edge = _edge(scene, "a", "SUCCESS", "b")
    before = edge.path().boundingRect()

    scene.config.transitions[0].dest = "a"
    scene.change_edge_endpoints("a", "SUCCESS", "b", "a", "a")

    assert edge.path().boundingRect() != before
    assert [(s, w, d) for s, w, d, _ in scene.edge_items][0] == ("a", "SUCCESS", "a")


def test_rewiring_an_edge_can_be_undone(qapp):
    """The undo snapshot has to predate the model mutation the panel makes."""
    canvas = _canvas(qapp, _config())
    scene = canvas.scene
    config = scene.config
    panel = canvas.param_panel

    panel.show_transition(
        config.transitions[0],
        ["SUCCESS", "FAILURE"],
        [],
        states=list(config.states),
        all_transitions=config.transitions,
    )
    panel.edge_dst_combo.setCurrentIndex(panel.edge_dst_combo.findData("c"))
    assert config.transitions[0].dest == "c"

    canvas.undo()
    assert canvas.config.transitions[0].dest == "b"


def test_changing_the_source_reloads_the_when_choices(qapp):
    """The When list belongs to the source node, so a rewire has to repopulate it."""
    config = _config()
    config.states[1].node_type = "check_health"
    canvas = _canvas(qapp, config)
    panel = canvas.param_panel

    panel.show_transition(
        config.transitions[0],
        ["SUCCESS", "FAILURE"],
        [],
        states=list(config.states),
        all_transitions=config.transitions,
    )
    assert panel.when_choices == ["(none)", "SUCCESS", "FAILURE"]

    panel.edge_src_combo.setCurrentIndex(panel.edge_src_combo.findData("b"))

    # 'b' is a condition node; its returns replace the action-node ones, and the
    # stored SUCCESS is kept as an entry rather than silently rewritten.
    assert panel.when_choices == ["(none)", "TRUE", "FALSE", "SUCCESS"]
    assert config.transitions[0].when == "SUCCESS"


def test_changing_the_when_condition_reroutes_the_edge(qapp):
    """`when` picks the source port, so the model change alone leaves the old path."""
    config = _config()
    config.states[0].node_type = "check_health"
    config.transitions[0].when = "TRUE"
    canvas = _canvas(qapp, config)
    scene = canvas.scene
    edge = _edge(scene, "a", "TRUE", "b")
    before = edge.path().boundingRect()
    panel = canvas.param_panel

    panel.show_transition(
        config.transitions[0],
        ["TRUE", "FALSE"],
        [],
        states=list(config.states),
        all_transitions=config.transitions,
    )
    panel.when_combo.setCurrentIndex(panel.when_combo.findText("FALSE"))

    assert config.transitions[0].when == "FALSE"
    assert edge.when == "FALSE"
    assert edge.path().boundingRect() != before


def test_changing_the_when_condition_rekeys_the_edge_record(qapp):
    """`when` is part of the edge's identity — a stale key makes later lookups miss."""
    config = _config()
    config.states[0].node_type = "check_health"
    config.transitions[0].when = "TRUE"
    canvas = _canvas(qapp, config)
    scene = canvas.scene
    panel = canvas.param_panel

    panel.show_transition(
        config.transitions[0],
        ["TRUE", "FALSE"],
        [],
        states=list(config.states),
        all_transitions=config.transitions,
    )
    panel.when_combo.setCurrentIndex(panel.when_combo.findText("FALSE"))

    assert ("a", "FALSE", "b") in [(s, w, d) for s, w, d, _ in scene.edge_items]
    # A follow-up endpoint rewire has to still find the edge.
    panel.edge_dst_combo.setCurrentIndex(panel.edge_dst_combo.findData("c"))
    assert ("a", "FALSE", "c") in [(s, w, d) for s, w, d, _ in scene.edge_items]


def test_a_duplicate_when_condition_is_refused(qapp, monkeypatch):
    """The source/dest dropdowns guard against duplicates; When has to as well."""
    from bartlebot.gui.canvas import param_panel as pp

    config = _config(
        transitions=[
            TransitionModel(trigger="advance", source="a", dest="b", when="TRUE"),
            TransitionModel(trigger="advance", source="a", dest="b", when="FALSE"),
        ]
    )
    config.states[0].node_type = "check_health"
    canvas = _canvas(qapp, config)
    panel = canvas.param_panel

    warned = []
    monkeypatch.setattr(pp.QMessageBox, "warning", lambda *a, **k: warned.append(a))

    panel.show_transition(
        config.transitions[0],
        ["TRUE", "FALSE"],
        [],
        states=list(config.states),
        all_transitions=config.transitions,
    )
    panel.when_combo.setCurrentIndex(panel.when_combo.findText("FALSE"))

    assert warned
    assert config.transitions[0].when == "TRUE"
    assert panel.when_combo.currentText() == "TRUE"


def test_changing_the_when_condition_can_be_undone(qapp):
    config = _config()
    config.states[0].node_type = "check_health"
    config.transitions[0].when = "TRUE"
    canvas = _canvas(qapp, config)
    panel = canvas.param_panel

    panel.show_transition(
        config.transitions[0],
        ["TRUE", "FALSE"],
        [],
        states=list(config.states),
        all_transitions=config.transitions,
    )
    panel.when_combo.setCurrentIndex(panel.when_combo.findText("FALSE"))
    assert config.transitions[0].when == "FALSE"

    canvas.undo()
    assert canvas.config.transitions[0].when == "TRUE"
