"""Edge assignments in the editor (#189): the edge panel's "When taken, set" list, and the label on the canvas.

A real ``BotCanvas``: selecting an edge shows its page, a row added there lands on the
transition as one undo step, and the edge's label says what it sets.
"""

from __future__ import annotations

import time

import pytest
from PyQt6.QtCore import QPointF

from bartlebot import variables
from bartlebot.bot.schema import AssignmentModel, BotConfig, FlagCondition, StateModel, TransitionModel, VariableModel
from bartlebot.gui.canvas.edge_item import EdgeItem

ADD_SWORD = AssignmentModel(variable="swords", op="add", value=1)


def _state(sid: str, node_type: str = "sleep", **params) -> StateModel:
    return StateModel(id=sid, node_type=node_type, label=sid.replace("_", " ").title(), params=params)


def _bot() -> BotConfig:
    """Enter resets the count on its way in; the spot counts a sword on SUCCESS; Which reads the count."""
    return BotConfig(
        id="bot-ea",
        name="Edge assignments",
        initial="enter",
        recovery_state="enter",
        variables=[VariableModel(name="swords", type="int"), VariableModel(name="found", type="bool")],
        states=[
            _state("enter", duration=1),
            _state("spot", duration=1),
            _state("which", "branch"),
            _state("rack", duration=1),
            _state("gate", duration=1),
        ],
        transitions=[
            TransitionModel(
                trigger="advance",
                source="enter",
                dest="spot",
                when="SUCCESS",
                assign=[AssignmentModel(variable="swords", op="set", value=0)],
            ),
            TransitionModel(trigger="advance", source="spot", dest="which", when="SUCCESS", assign=[ADD_SWORD]),
            TransitionModel(trigger="advance", source="spot", dest="spot", when="FAILURE"),
            TransitionModel(
                trigger="advance",
                source="which",
                dest="gate",
                when="SUCCESS",
                flags=[FlagCondition(variable="swords", compare="==", value=4)],
                assign=[AssignmentModel(variable="found", op="toggle")],
            ),
            TransitionModel(trigger="advance", source="which", dest="rack", when="SUCCESS"),
            TransitionModel(trigger="advance", source="rack", dest="spot", when="SUCCESS"),
            TransitionModel(trigger="advance", source="gate", dest="enter", when="SUCCESS"),
        ],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    canvas = BotCanvas()
    yield canvas
    canvas.deleteLater()


def _edge_to(canvas, source: str, dest: str) -> EdgeItem:
    return next(e for s, _w, d, e in canvas.scene.edge_items if (s, d) == (source, dest))


def _select(canvas, edge: EdgeItem) -> None:
    canvas.scene.clearSelection()
    edge.setSelected(True)


# ---------------------------------------------------------------------------
# The label
# ---------------------------------------------------------------------------


def test_every_edge_with_an_assignment_shows_it_on_the_canvas(canvas):
    canvas.setup(_bot())
    for *_key, edge in canvas.scene.edge_items:
        models = canvas.scene.transitions_of(edge)
        if not models[0].assign:
            continue
        whole, _short = variables.edge_label(models[0])
        assert edge.label is not None and edge.label.isVisible()
        assert edge.label.full_text == whole and edge.label.toolTip() == whole
    assert _edge_to(canvas, "spot", "which").label.shown_text == "→ swords + 1"
    assert _edge_to(canvas, "which", "gate").label.full_text == "swords == 4 → found toggled"
    # An assignment is not a condition: the edge is not drawn as one that might not fire.
    assert not _edge_to(canvas, "spot", "which").has_flags
    assert _edge_to(canvas, "spot", "spot").label is None


def test_a_long_condition_never_hides_what_the_edge_sets(canvas):
    config = _bot()
    config.transitions[3].flags = [
        FlagCondition(variable="swords", compare="==", value=4),
        FlagCondition(variable="found", compare="==", value=False),
        FlagCondition(variable="swords", compare=">", value=1),
    ]
    canvas.setup(config)
    label = _edge_to(canvas, "which", "gate").label
    assert "→ found toggled" in label.full_text
    whole, short = variables.edge_label(config.transitions[3])
    assert label.full_text == whole and short.endswith("→ found toggled")


def test_a_label_with_an_assignment_moving_and_changing_leaves_nothing_unrepainted(canvas, qtbot, tmp_path):
    """The repaint tracer (#73): the label gains and loses its assignment part and follows a moved node."""
    from bartlebot.gui.canvas.repaint_trace import RepaintTracer  # noqa: PLC0415

    canvas.setup(_bot())
    canvas.enter_edit_mode()
    canvas.resize(1200, 800)
    canvas.show()
    qtbot.waitExposed(canvas)
    tracer = RepaintTracer(canvas.view, screenshot_dir=tmp_path)
    qtbot.wait(50)
    tracer.sample()

    edge = _edge_to(canvas, "which", "rack")
    trans = canvas.scene.transitions_of(edge)[0]
    trans.assign = [ADD_SWORD, AssignmentModel(variable="found", op="set", value=True)]
    canvas.refresh_edge_flags()
    node = canvas.scene.node_items["rack"]
    node.setPos(node.pos() + QPointF(0, 90))
    trans.assign = []
    canvas.refresh_edge_flags()

    qtbot.wait(100)
    tracer.sample()
    assert tracer.report_expired(time.monotonic() + 60) == []
    assert not edge.label.isVisible()


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------


def test_an_edges_page_lists_what_it_sets_and_hands_it_back_unchanged(canvas):
    canvas.setup(_bot())
    canvas.enter_edit_mode()
    _select(canvas, _edge_to(canvas, "enter", "spot"))
    field = canvas.param_panel._edge_assign_field
    assert field.value() == [{"variable": "swords", "op": "set", "value": 0}]
    assert canvas.param_panel._assign_lbl.text() == "When taken, set"

    _select(canvas, _edge_to(canvas, "spot", "spot"))
    assert field.value() == []


def test_adding_a_row_lands_on_the_edge_as_one_undo_step_and_relabels_it(canvas):
    config = _bot()
    canvas.setup(config)
    canvas.enter_edit_mode()
    edge = _edge_to(canvas, "which", "rack")
    _select(canvas, edge)
    trans = canvas.scene.transitions_of(edge)[0]
    field = canvas.param_panel._edge_assign_field

    row = field.add_row()
    row.variable.setCurrentText("found")
    row.op.setCurrentIndex(row.op.findData("toggle"))
    assert [(a.variable, a.op) for a in trans.assign] == [("found", "toggle")]
    assert edge.label.full_text == "→ found toggled" and edge.label.shown_text.startswith("→ found")

    canvas.undo()
    restored = next(t for t in canvas.config.transitions if (t.source, t.dest) == ("which", "rack"))
    assert restored.assign == []


def test_removing_the_last_row_takes_the_assignment_and_its_label_away(canvas):
    canvas.setup(_bot())
    canvas.enter_edit_mode()
    edge = _edge_to(canvas, "spot", "which")
    _select(canvas, edge)
    field = canvas.param_panel._edge_assign_field
    field.rows[0].remove_btn.click()
    assert canvas.scene.transitions_of(edge)[0].assign == []
    assert not edge.label.isVisible()
    assert "assign" not in canvas.scene.config.file_dump()["transitions"][1]


def test_with_no_variables_the_list_says_where_to_declare_one(canvas):
    config = _bot()
    config.variables = []
    config.transitions = [t for t in config.transitions if not t.assign and not t.flags]
    canvas.setup(config)
    canvas.enter_edit_mode()
    _select(canvas, _edge_to(canvas, "spot", "spot"))
    field = canvas.param_panel._edge_assign_field
    assert not field.add_btn.isEnabled()
    assert field.add_row() is None


def test_a_rename_on_the_options_tab_reshows_the_rows_with_the_new_name(canvas):
    config = _bot()
    canvas.setup(config)
    canvas.enter_edit_mode()
    _select(canvas, _edge_to(canvas, "spot", "which"))
    variables.rename(config, "swords", "blades")
    config.variables[0] = VariableModel(name="blades", type="int")
    canvas.reshow_properties()
    assert canvas.param_panel._edge_assign_field.value() == [{"variable": "blades", "op": "add", "value": 1}]
    canvas.refresh_edge_flags()
    assert _edge_to(canvas, "spot", "which").label.shown_text == "→ blades + 1"


# ---------------------------------------------------------------------------
# A subgraph node's edge stands for several transitions
# ---------------------------------------------------------------------------


def test_every_transition_a_call_nodes_edge_stands_for_takes_its_assignments(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415

    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from bartlebot.subgraph_store import SubgraphLibrary  # noqa: PLC0415
    from bartlebot.subgraphs import compose_bot  # noqa: PLC0415
    from tests.test_subgraphs import REGION, SYNC_ID, linked_bot, sync_players_definition  # noqa: PLC0415

    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)
    library = SubgraphLibrary(tmp_path / "library")
    library.save(sync_players_definition())
    canvas = BotCanvas()
    try:
        canvas.set_subgraph_library(library)
        bot, report = compose_bot(linked_bot(library.get(SYNC_ID), library), library)
        bot.variables = [VariableModel(name="synced", type="bool")]
        canvas.setup(bot)
        canvas.set_compose_report(report)
        canvas.enter_edit_mode()
        edge = next(e for s, w, d, e in canvas.scene.edge_items if (s, w, d) == (REGION, "exit_done", "state_1"))
        _select(canvas, edge)
        row = canvas.param_panel._edge_assign_field.add_row()
        row.op.setCurrentIndex(row.op.findData("toggle"))
        models = canvas.scene.transitions_of(edge)
        assert models and all([(a.variable, a.op) for a in t.assign] == [("synced", "toggle")] for t in models)
    finally:
        canvas.prepare_for_shutdown()
