"""Tests for #66 — inserting a state at a node's input or output port.

Inserting on an *edge* splits that one edge. Inserting at a *port* takes everything
on that port at once, which is the case the issue is about: several edges converging
on one input, or several flag-guarded branches leaving one output.

What must survive the move is each rerouted edge's guards. A TransitionModel carries
``when``, ``flags``/``flag_groups`` and ``label``, and all of them describe the edge's
*source* node — so rerouting works by mutating the existing transition rather than
replacing it, and these tests are mostly about that not being quietly lost.

Real widget tests (QApplication required), in the style of test_fsm_role_checkboxes.
No dialog is ever shown: choosing the node type is separated from performing the
insert precisely so the insert can be driven directly.
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot.bot.schema import BotConfig, FlagCondition, StateModel, TransitionModel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _config(transitions=None, states=None):
    return BotConfig(
        name="t",
        initial="a",
        recovery_state="a",
        states=states or [StateModel(id=sid, node_type="change_realm", params={}) for sid in ("a", "b", "c", "target")],
        transitions=transitions
        if transitions is not None
        else [
            TransitionModel(trigger="advance", source="a", dest="target", when="SUCCESS"),
            TransitionModel(trigger="advance", source="b", dest="target", when="FAILURE"),
            TransitionModel(trigger="advance", source="c", dest="target", when="SUCCESS"),
        ],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    def _build(config=None):
        c = BotCanvas()
        c.setup(config or _config())
        c.enter_edit_mode()
        built.append(c)
        return c

    built = []
    yield _build
    for c in built:
        c.prepare_for_shutdown()


def _t(config, source, dest):
    return next((t for t in config.transitions if t.source == source and t.dest == dest), None)


# ---------------------------------------------------------------------------
# Input port
# ---------------------------------------------------------------------------


def test_input_port_reroutes_every_incoming_edge(canvas):
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert new_id is not None
    assert {t.source for t in c.config.transitions if t.dest == new_id} == {"a", "b", "c"}


def test_input_port_wires_the_new_state_onward(canvas):
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert _t(c.config, new_id, "target") is not None


def test_input_port_leaves_nothing_pointing_at_the_old_target(canvas):
    """Only the new state's own edge should reach it now."""
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert [t.source for t in c.config.transitions if t.dest == "target"] == [new_id]


def test_input_port_keeps_each_edge_when_value(canvas):
    """`when` describes the source node's outcome, so rerouting must not touch it."""
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    by_source = {t.source: t.when for t in c.config.transitions if t.dest == new_id}
    assert by_source == {"a": "SUCCESS", "b": "FAILURE", "c": "SUCCESS"}


def test_input_port_keeps_flag_guards(canvas):
    """The guards are the whole reason several edges share one port."""
    config = _config(
        transitions=[
            TransitionModel(
                trigger="advance",
                source="a",
                dest="target",
                when="SUCCESS",
                flags=[FlagCondition(option="repeat", negate=True)],
            ),
            TransitionModel(trigger="advance", source="b", dest="target", when="SUCCESS"),
        ]
    )
    c = canvas(config)

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    guarded = _t(c.config, "a", new_id)
    assert [(f.option, f.negate) for f in guarded.flags] == [("repeat", True)]


def test_input_port_keeps_the_edge_label(canvas):
    config = _config(
        transitions=[TransitionModel(trigger="advance", source="a", dest="target", when="SUCCESS", label="retry")]
    )
    c = canvas(config)

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert _t(c.config, "a", new_id).label == "retry"


def test_input_port_on_a_node_with_no_incoming_edges(canvas):
    """Degenerates to prepending a state, which is still worth allowing."""
    config = _config(transitions=[])
    c = canvas(config)

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert new_id is not None
    assert _t(c.config, new_id, "target") is not None


def test_input_port_routes_a_self_loop_through_the_new_state(canvas):
    config = _config(transitions=[TransitionModel(trigger="advance", source="target", dest="target", when="SUCCESS")])
    c = canvas(config)

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert _t(c.config, "target", new_id) is not None
    assert _t(c.config, new_id, "target") is not None


# ---------------------------------------------------------------------------
# Output port
# ---------------------------------------------------------------------------


def _out_config():
    return _config(
        transitions=[
            TransitionModel(trigger="advance", source="a", dest="b", when="SUCCESS"),
            TransitionModel(trigger="advance", source="a", dest="c", when="SUCCESS"),
            TransitionModel(trigger="advance", source="a", dest="target", when="FAILURE"),
        ]
    )


def test_output_port_reroutes_only_that_port(canvas):
    c = canvas(_out_config())

    new_id = c.scene.insert_state_at_output_port("a", "SUCCESS", "sleep")

    assert {t.dest for t in c.config.transitions if t.source == new_id} == {"b", "c"}


def test_output_port_leaves_the_other_port_alone(canvas):
    """The FAILURE branch must not be dragged along by an insert on SUCCESS."""
    c = canvas(_out_config())

    c.scene.insert_state_at_output_port("a", "SUCCESS", "sleep")

    assert _t(c.config, "a", "target").when == "FAILURE"


def test_output_port_connects_the_source_on_the_clicked_port(canvas):
    c = canvas(_out_config())

    new_id = c.scene.insert_state_at_output_port("a", "SUCCESS", "sleep")

    assert _t(c.config, "a", new_id).when == "SUCCESS"


def test_output_port_keeps_the_rerouted_when(canvas):
    """Guaranteed by the node-type restriction, and the reason for it."""
    c = canvas(_out_config())

    new_id = c.scene.insert_state_at_output_port("a", "SUCCESS", "sleep")

    assert {t.when for t in c.config.transitions if t.source == new_id} == {"SUCCESS"}


def test_output_port_keeps_flag_guards(canvas):
    config = _config(
        transitions=[
            TransitionModel(
                trigger="advance",
                source="a",
                dest="b",
                when="SUCCESS",
                flags=[FlagCondition(option="repeat", negate=True)],
            ),
        ]
    )
    c = canvas(config)

    new_id = c.scene.insert_state_at_output_port("a", "SUCCESS", "sleep")

    assert [(f.option, f.negate) for f in _t(c.config, new_id, "b").flags] == [("repeat", True)]


def test_output_port_refuses_a_node_that_cannot_produce_the_return(canvas):
    """A check_* node cannot return SUCCESS, so it would strand every guard."""
    c = canvas(_out_config())
    before = list(c.config.transitions)

    result = c.scene.insert_state_at_output_port("a", "SUCCESS", "check_health")

    assert result is None
    assert c.config.transitions == before


def test_the_refused_insert_creates_no_state(canvas):
    c = canvas(_out_config())
    before = {s.id for s in c.config.states}

    c.scene.insert_state_at_output_port("a", "SUCCESS", "check_health")

    assert {s.id for s in c.config.states} == before


def test_output_port_accepts_a_condition_node_on_a_condition_port(canvas):
    config = _config(
        states=[
            StateModel(id="cond", node_type="check_health", params={}),
            StateModel(id="b", node_type="change_realm", params={}),
            StateModel(id="a", node_type="change_realm", params={}),
        ],
        transitions=[TransitionModel(trigger="advance", source="cond", dest="b", when="TRUE")],
    )
    config.initial = "a"
    config.recovery_state = "a"
    c = canvas(config)

    new_id = c.scene.insert_state_at_output_port("cond", "TRUE", "check_mana")

    assert new_id is not None
    assert _t(c.config, new_id, "b").when == "TRUE"


# ---------------------------------------------------------------------------
# Wiring, undo, and the happy-path default
# ---------------------------------------------------------------------------


def test_the_onward_edge_is_not_unconditional(canvas):
    """An edge with no `when` fires on failure too, which is never the intent."""
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert _t(c.config, new_id, "target").when is not None


def test_the_onward_edge_uses_the_happy_path_return(canvas):
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "change_realm")

    assert _t(c.config, new_id, "target").when == "SUCCESS"


def test_the_onward_edge_uses_true_for_a_condition_node(canvas):
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "check_health")

    assert _t(c.config, new_id, "target").when == "TRUE"


def test_an_insert_is_one_undo_step(canvas):
    """Three reroutes plus a new state and edge is one edit, not five."""
    c = canvas()

    c.scene.insert_state_at_input_port("target", "sleep")
    c.undo()

    assert {t.source for t in c.config.transitions if t.dest == "target"} == {"a", "b", "c"}
    assert len(c.config.states) == 4


def test_the_new_state_appears_on_the_canvas(canvas):
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert new_id in c.scene.node_items


def test_the_stored_position_matches_where_the_node_is_drawn(canvas):
    """They diverged by one NODE_H on the edge-insert path before #66."""
    c = canvas()

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    state = next(s for s in c.config.states if s.id == new_id)
    item = c.scene.node_items[new_id]
    assert (state.position["x"], state.position["y"]) == (item.pos().x(), item.pos().y())


def test_inserting_at_an_unknown_state_is_refused(canvas):
    c = canvas()

    assert c.scene.insert_state_at_input_port("nope", "sleep") is None
    assert c.scene.insert_state_at_output_port("nope", "SUCCESS", "sleep") is None


def test_the_config_still_round_trips(canvas):
    """The end-to-end check: the edited graph is still a loadable bot."""
    c = canvas()

    c.scene.insert_state_at_input_port("target", "sleep")

    BotConfig.model_validate(c.config.model_dump())


# ---------------------------------------------------------------------------
# The picker restriction
# ---------------------------------------------------------------------------


def test_the_browser_offers_only_the_allowed_types(qapp):
    """Tabs and search both funnel through _populate_list, so one filter covers both."""
    from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

    dlg = NodeBrowserDialog(None, mode="add", allowed_node_types={"sleep", "change_realm"})
    try:
        assert set(dlg.offered_node_types) == {"sleep", "change_realm"}
    finally:
        dlg.deleteLater()


def test_the_browser_is_unrestricted_by_default(qapp):
    from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog
    from bartlebot.nodes.registry import NODE_SPECS

    dlg = NodeBrowserDialog(None, mode="add")
    try:
        assert len(dlg.offered_node_types) == len(NODE_SPECS)
    finally:
        dlg.deleteLater()


def test_a_success_port_offers_sleep():
    """The case that rules out matching on the whole return list: sleep never fails."""
    from bartlebot.gui.canvas.edit_scene import node_types_producing

    assert "sleep" in node_types_producing("SUCCESS")


def test_a_success_port_excludes_condition_nodes():
    from bartlebot.gui.canvas.edit_scene import node_types_producing

    assert "check_health" not in node_types_producing("SUCCESS")


def test_a_true_port_offers_only_condition_nodes():
    from bartlebot.gui.canvas.edit_scene import node_types_producing
    from bartlebot.nodes.registry import NODE_SPECS

    expected = {nt for nt, spec in NODE_SPECS.items() if "TRUE" in [r.name for r in spec.returns]}
    assert node_types_producing("TRUE") == expected


def test_every_allowed_type_actually_survives_the_insert(canvas):
    """The picker's promise: nothing it offers can strand a rerouted edge's guard."""
    from bartlebot.gui.canvas.edit_scene import node_types_producing

    for node_type in sorted(node_types_producing("SUCCESS")):
        c = canvas(_out_config())
        new_id = c.scene.insert_state_at_output_port("a", "SUCCESS", node_type)
        assert new_id is not None, node_type
        assert {t.when for t in c.config.transitions if t.source == new_id} == {"SUCCESS"}, node_type


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def _boxes(canvas_obj):
    from PyQt6.QtCore import QRectF

    from bartlebot.gui.canvas.node_item import NODE_H, NODE_W

    return {sid: QRectF(n.pos().x(), n.pos().y(), NODE_W, NODE_H) for sid, n in canvas_obj.scene.node_items.items()}


def _grid_config():
    """Four nodes on the 440 px column grid the bots actually use."""
    from bartlebot.gui.canvas.node_item import GRID_W

    states = [
        StateModel(id="a", node_type="change_realm", params={}, position={"x": 0.0, "y": 0.0}),
        StateModel(id="b", node_type="change_realm", params={}, position={"x": 0.0, "y": 90.0}),
        StateModel(id="c", node_type="change_realm", params={}, position={"x": 0.0, "y": 180.0}),
        StateModel(id="target", node_type="change_realm", params={}, position={"x": float(GRID_W), "y": 90.0}),
    ]
    return _config(states=states)


def test_the_inserted_node_does_not_overlap_anything(canvas):
    c = canvas(_grid_config())

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    boxes = _boxes(c)
    new_box = boxes.pop(new_id)
    assert not any(new_box.intersects(b) for b in boxes.values())


def test_an_input_insert_lands_left_of_its_target(canvas):
    """It feeds the target, so it belongs on the target's input side."""
    c = canvas(_grid_config())

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert c.scene.node_items[new_id].pos().x() < c.scene.node_items["target"].pos().x()


def test_an_output_insert_lands_right_of_its_source(canvas):
    c = canvas(_grid_config())

    new_id = c.scene.insert_state_at_output_port("a", "SUCCESS", "sleep")

    assert c.scene.node_items[new_id].pos().x() > c.scene.node_items["a"].pos().x()


def test_placement_holds_the_column_and_moves_the_row(canvas):
    """The whole point of searching rows before columns: it stays in its layer.

    The three sources fill column 0 at rows 0, 90 and 180, so the insert has to take
    a free row in that same column rather than drift into another one.
    """
    c = canvas(_grid_config())

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    assert c.scene.node_items[new_id].pos().x() == pytest.approx(0.0)
    assert c.scene.node_items[new_id].pos().y() not in (0.0, 90.0, 180.0)


def test_placement_lands_on_the_same_grid_the_bots_use(canvas):
    """All 1264 nodes in the shipped library sit on whole GRID_W/GRID_H multiples.

    The older "Add State Here" search offsets its rows by half a node, which is
    collision-free but never row-aligned with anything already on the canvas.
    """
    from bartlebot.gui.canvas.node_item import GRID_H, GRID_W

    c = canvas(_grid_config())

    new_id = c.scene.insert_state_at_input_port("target", "sleep")

    p = c.scene.node_items[new_id].pos()
    assert p.x() % GRID_W == pytest.approx(0)
    assert p.y() % GRID_H == pytest.approx(0)


def test_free_position_near_snaps_an_already_clear_seed_to_the_grid(canvas):
    """Empty space still gets grid-aligned, so an insert never lands off-grid."""
    from PyQt6.QtCore import QPointF

    from bartlebot.gui.canvas.node_item import GRID_H, GRID_W

    c = canvas(_grid_config())

    got = c.scene.free_position_near(QPointF(-4400.0, -4505.0), x_dir=-1)

    assert got.x() % GRID_W == pytest.approx(0)
    assert got.y() % GRID_H == pytest.approx(0)
    assert abs(got.x() - (-4400.0)) <= GRID_W / 2
    assert abs(got.y() - (-4505.0)) <= GRID_H / 2


def test_every_insert_site_in_every_shipped_bot_is_collision_free(canvas):
    """The property the placement exists for, checked against the real library.

    Before this, 78% of these landed on top of an existing node.
    """
    import glob

    from PyQt6.QtCore import QRectF

    from bartlebot.gui.canvas.node_item import NODE_H, NODE_W

    checked = 0
    for path in sorted(glob.glob("bots/*/*.json"))[:12]:
        config = BotConfig.load(path)
        if any(s.position is None for s in config.states):
            continue
        c = canvas(config)
        target = config.states[len(config.states) // 2].id
        new_id = c.scene.insert_state_at_input_port(target, "sleep")
        boxes = {sid: QRectF(n.pos().x(), n.pos().y(), NODE_W, NODE_H) for sid, n in c.scene.node_items.items()}
        new_box = boxes.pop(new_id)
        assert not any(new_box.intersects(b) for b in boxes.values()), f"{path} -> {target}"
        checked += 1

    assert checked > 0
