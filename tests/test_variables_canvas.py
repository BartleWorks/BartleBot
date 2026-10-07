"""Variables on the canvas (#187): every conditioned edge says its condition, and the badge says what is wrong.

A label is a child item of its edge, placed where the edge turns toward its destination
and cut to the room there; the tooltip holds the whole condition. Labels are new canvas
items, so one test runs them under the repaint tracer (#73).
"""

from __future__ import annotations

import time

import pytest
from PyQt6.QtCore import QPointF

from bartlebot import variables
from bartlebot.bot.schema import (
    BotConfig,
    BotOptionModel,
    FlagCondition,
    FlagGroup,
    StateModel,
    TransitionModel,
    VariableModel,
)
from bartlebot.gui.canvas.edge_item import EdgeItem


def _is(variable: str, value, op: str = "==") -> FlagCondition:
    return FlagCondition(variable=variable, compare=op, value=value)


def _switch_bot() -> BotConfig:
    """Reset sets swords to 0; Count adds one; Which switches on it to three racks, with an otherwise to the fourth."""
    racks = [
        StateModel(id=f"rack_{k}", node_type="sleep", label=f"Rack {k}", params={"duration": 1}) for k in range(1, 5)
    ]
    return BotConfig(
        id="bot-c",
        name="Canvas",
        initial="reset",
        recovery_state="reset",
        options=[BotOptionModel(name="Fast", type="bool", default=True)],
        variables=[VariableModel(name="swords", type="int")],
        states=[
            StateModel(
                id="reset",
                node_type="set_variables",
                label="Reset",
                params={"assignments": [{"variable": "swords", "op": "set", "value": 0}]},
            ),
            StateModel(
                id="count",
                node_type="set_variables",
                label="Count",
                params={"assignments": [{"variable": "swords", "op": "add", "value": 1}]},
            ),
            StateModel(id="which", node_type="branch", label="Which"),
            *racks,
        ],
        transitions=[
            TransitionModel(trigger="advance", source="reset", dest="count", when="SUCCESS"),
            TransitionModel(trigger="advance", source="count", dest="which", when="SUCCESS"),
            *(
                TransitionModel(
                    trigger="advance", source="which", dest=f"rack_{k}", when="SUCCESS", flags=[_is("swords", k)]
                )
                for k in (1, 2, 3)
            ),
            TransitionModel(trigger="advance", source="which", dest="rack_4", when="SUCCESS"),
            TransitionModel(
                trigger="advance",
                source="rack_4",
                dest="count",
                flag_groups=[FlagGroup(flags=[FlagCondition(option="Fast"), _is("swords", 4, ">=")])],
            ),
            # Otherwise back through the reset, so the loop has nothing to warn about.
            TransitionModel(trigger="advance", source="rack_4", dest="reset"),
            *(TransitionModel(trigger="advance", source=f"rack_{k}", dest="count") for k in (1, 2, 3)),
        ],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    canvas = BotCanvas()
    yield canvas
    canvas.deleteLater()


def _edges(canvas) -> list[tuple[EdgeItem, list[TransitionModel]]]:
    return [(edge, canvas.scene.transitions_of(edge)) for *_key, edge in canvas.scene.edge_items]


def _edge_to(canvas, source: str, dest: str) -> EdgeItem:
    return next(e for s, _w, d, e in canvas.scene.edge_items if (s, d) == (source, dest))


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_every_conditioned_edge_shows_its_condition_and_no_other_edge_does(canvas):
    canvas.setup(_switch_bot())
    for edge, models in _edges(canvas):
        text = variables.describe_guard(models[0])
        if text is None:
            assert edge.label is None or not edge.label.isVisible()
            continue
        assert edge.label is not None and edge.label.isVisible()
        assert edge.label.full_text == text
        assert edge.label.toolTip() == text
        assert edge.label.shown_text == text or edge.label.shown_text.endswith("…")

    assert _edge_to(canvas, "which", "rack_2").label.shown_text == "swords == 2"
    assert _edge_to(canvas, "rack_4", "count").label.full_text == "Fast AND swords >= 4"


def test_a_label_never_runs_past_its_segment_and_the_tooltip_keeps_the_rest(canvas):
    config = _switch_bot()
    config.options.append(BotOptionModel(name="Defeat Brady the Sneak first", type="bool", default=True))
    config.transitions[2].flags.insert(0, FlagCondition(option="Defeat Brady the Sneak first"))
    canvas.setup(config)

    edge = _edge_to(canvas, "which", "rack_1")
    label = edge.label
    assert label.full_text == "Defeat Brady the Sneak first AND swords == 1"
    assert label.shown_text.endswith("…")
    assert label.toolTip() == label.full_text
    end = edge.path().pointAtPercent(1.0)
    assert label.mapRectToScene(label.boundingRect()).right() < end.x()


def test_the_labels_of_a_switch_do_not_sit_on_each_other(canvas):
    canvas.setup(_switch_bot())
    rects = [
        _edge_to(canvas, "which", f"rack_{k}").label.mapRectToScene(
            _edge_to(canvas, "which", f"rack_{k}").label.boundingRect()
        )
        for k in (1, 2, 3)
    ]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert not a.intersects(b)


def test_a_label_follows_its_edge_when_a_node_moves(canvas):
    canvas.setup(_switch_bot())
    canvas.enter_edit_mode()
    edge = _edge_to(canvas, "which", "rack_2")
    before = edge.label.scenePos()
    node = canvas.scene.node_items["rack_2"]
    node.setPos(node.pos() + QPointF(0, 200))
    assert edge.label.scenePos() != before
    assert edge.label.scenePos().y() > before.y()


def test_editing_a_condition_rewrites_the_label_and_removing_it_takes_the_label_away(canvas):
    canvas.setup(_switch_bot())
    canvas.enter_edit_mode()
    edge = _edge_to(canvas, "which", "rack_3")
    trans = canvas.scene.transitions_of(edge)[0]

    trans.flags = [_is("swords", 3, ">=")]
    canvas.refresh_edge_flags()
    assert edge.label.shown_text == "swords >= 3"
    assert edge.has_flags

    trans.flags = []
    canvas.refresh_edge_flags()
    assert not edge.label.isVisible()
    assert not edge.has_flags


def test_a_rename_rewrites_the_labels_that_name_the_variable(canvas):
    config = _switch_bot()
    canvas.setup(config)
    variables.rename(config, "swords", "blades")
    config.variables[0] = VariableModel(name="blades", type="int")
    canvas.refresh_edge_flags()
    assert _edge_to(canvas, "which", "rack_1").label.shown_text == "blades == 1"


def test_clicking_a_label_selects_its_edge(canvas, qtbot):
    canvas.setup(_switch_bot())
    canvas.enter_edit_mode()
    canvas.resize(1200, 800)
    canvas.show()
    qtbot.waitExposed(canvas)
    edge = _edge_to(canvas, "which", "rack_2")
    view = canvas.view
    view.centerOn(edge.label)
    qtbot.wait(20)
    point = view.mapFromScene(edge.label.mapToScene(edge.label.boundingRect().center()))
    from PyQt6.QtCore import Qt  # noqa: PLC0415

    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert edge.isSelected()


def test_right_clicking_a_label_opens_its_edges_menu(canvas, monkeypatch):
    from PyQt6.QtGui import QTransform  # noqa: PLC0415
    from PyQt6.QtWidgets import QMenu  # noqa: PLC0415

    canvas.setup(_switch_bot())
    canvas.enter_edit_mode()
    label = _edge_to(canvas, "which", "rack_2").label
    at = label.mapToScene(label.boundingRect().center())
    assert canvas.scene.itemAt(at, QTransform()) is label

    shown: list[list[str]] = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a: shown.append([a.text() for a in self.actions()]))
    from PyQt6.QtCore import QPoint  # noqa: PLC0415

    class _Event:  # PyQt6 will not construct a QGraphicsSceneContextMenuEvent
        def scenePos(self):  # noqa: N802 - Qt's name
            return at

        def screenPos(self):  # noqa: N802 - Qt's name
            return QPoint(0, 0)

    canvas.scene.contextMenuEvent(_Event())
    assert shown and "Delete Transition" in shown[0]


def test_a_label_change_and_a_move_leave_nothing_unrepainted(canvas, qtbot, tmp_path):
    """The repaint tracer (#73) on a live canvas: a label's text, size and place all change, and nothing is owed."""
    from bartlebot.gui.canvas.repaint_trace import RepaintTracer  # noqa: PLC0415

    canvas.setup(_switch_bot())
    canvas.enter_edit_mode()
    canvas.resize(1200, 800)
    canvas.show()
    qtbot.waitExposed(canvas)
    tracer = RepaintTracer(canvas.view, screenshot_dir=tmp_path)
    qtbot.wait(50)
    tracer.sample()

    edge = _edge_to(canvas, "which", "rack_2")
    trans = canvas.scene.transitions_of(edge)[0]
    trans.flags = [FlagCondition(option="Fast"), _is("swords", 2, "<=")]
    canvas.refresh_edge_flags()
    node = canvas.scene.node_items["rack_2"]
    node.setPos(node.pos() + QPointF(0, 90))
    trans.flags = []
    canvas.refresh_edge_flags()

    qtbot.wait(100)
    tracer.sample()
    assert tracer.report_expired(time.monotonic() + 60) == []


# ---------------------------------------------------------------------------
# The badge
# ---------------------------------------------------------------------------


def test_a_clean_bot_with_variables_has_nothing_on_the_badge_for_them(canvas):
    canvas.setup(_switch_bot())
    assert not [line for line in canvas.validation_errors() + canvas.validation_warnings() if "Variables" in line]


def test_an_error_is_on_the_badge_marks_its_node_and_disables_run(canvas):
    config = _switch_bot()
    config.transitions[2].flags = [_is("swords", "Moon")]
    canvas.setup(config)
    canvas.set_run_enabled(True)
    canvas._update_validation_badge()

    errors = canvas.validation_errors()
    assert "Variables: The edge from 'Which' compares 'swords' with Moon, which a int cannot hold" in errors
    assert not canvas._run_pause_btn.isEnabled()
    assert "Cannot run" in canvas._run_pause_btn.toolTip()
    canvas.scene.refresh_errors()
    assert "compares 'swords' with Moon" in canvas.scene.node_items["which"].toolTip()


def test_a_warning_is_on_the_badge_and_leaves_run_alone(canvas):
    config = _switch_bot()
    config.variables.append(VariableModel(name="unread", type="bool"))
    config.states[0].params["assignments"].append({"variable": "unread", "op": "set", "value": True})
    canvas.setup(config)
    canvas.set_run_enabled(True)
    canvas._update_validation_badge()

    assert "Variables: Variable 'unread' is set and never read" in canvas.validation_warnings()
    assert canvas.validation_errors() == []
    assert canvas._run_pause_btn.isEnabled()


def test_a_problem_about_no_node_is_on_the_badge_alone(canvas):
    config = _switch_bot()
    config.variables.append(VariableModel(name="swords", type="int"))
    canvas.setup(config)
    assert "Variables: Two variables are named 'swords'" in canvas.validation_errors()


# ---------------------------------------------------------------------------
# The page on show follows a declaration edit
# ---------------------------------------------------------------------------


def test_a_rename_on_the_options_tab_reshows_the_edge_page_with_the_new_name(canvas):
    config = _switch_bot()
    canvas.setup(config)
    canvas.enter_edit_mode()
    edge = _edge_to(canvas, "which", "rack_1")
    canvas.scene.clearSelection()
    edge.setSelected(True)
    panel = canvas.param_panel
    assert panel.shown_transition is canvas.scene.transitions_of(edge)[0]

    variables.rename(config, "swords", "blades")
    config.variables[0] = VariableModel(name="blades", type="int")
    canvas.reshow_properties()

    widget = panel._flag_conditions_widget
    assert [row.condition() for row in widget.rows] == [_is("blades", 1)]


# ---------------------------------------------------------------------------
# Heretical Academy, built with the editors
# ---------------------------------------------------------------------------


def test_heretical_academys_puzzle_can_be_built_without_touching_the_file(qapp, monkeypatch):
    """The plan's shape, at two spots and two racks: declared, written and read through the editors alone.

    Enter sets both variables back; a spot that finds a sword remembers where and counts
    it; Which rack switches on the count; Resume search switches on where to go back to.
    """
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm, _FlagConditionsWidget  # noqa: PLC0415
    from bartlebot.gui.sidebar.sections.variables_section import VariableDialog, VariablesSection  # noqa: PLC0415
    from bartlebot.nodes.utility.set_variables import PARAM_SCHEMA  # noqa: PLC0415

    def state(sid, node_type="sleep", **params):
        return StateModel(id=sid, node_type=node_type, label=sid.replace("_", " ").title(), params=params)

    config = BotConfig(
        id="bot-ha",
        name="Heretical Academy",
        initial="enter",
        recovery_state="enter",
        states=[
            state("enter", "set_variables"),
            state("spot_1", duration=1),
            state("found_1", "set_variables"),
            state("spot_2", duration=1),
            state("found_2", "set_variables"),
            state("which_rack", "branch"),
            state("rack_1", duration=1),
            state("rack_2", duration=1),
            state("resume", "branch"),
            state("open_gate", duration=1),
        ],
        transitions=[],
    )

    # Declare next_spot (starts at 1) and swords, in the Variables section.
    section = VariablesSection()
    section.set_config(config)
    for name, start in (("next_spot", 1), ("swords", 0)):

        def fill(dlg, name=name, start=start):
            dlg._name_edit.setText(name)
            dlg._type_combo.setCurrentIndex(dlg._type_combo.findData("int"))
            dlg.initial_field.control.setValue(start)
            dlg._on_ok()

        monkeypatch.setattr(VariableDialog, "exec", lambda self, fill=fill: (fill(self), self.result())[1])
        section.add_variable()
    assert [v.name for v in config.variables] == ["next_spot", "swords"]

    # Each set_variables node's rows, in its assignments editor.
    def write(state_id, *rows):
        form = _DynamicParamForm()
        form.set_variables_getter(lambda: list(config.variables))
        form.load_schema(PARAM_SCHEMA, {"assignments": []})
        field = form.widget("assignments")
        for name, op, value in rows:
            row = field.add_row()
            row.variable.setCurrentText(name)
            row.op.setCurrentIndex(row.op.findData(op))
            row.value.control.setValue(value)
        next(s for s in config.states if s.id == state_id).params.update(form.get_params())

    write("enter", ("next_spot", "set", 1), ("swords", "set", 0))
    write("found_1", ("next_spot", "set", 2), ("swords", "add", 1))
    write("found_2", ("next_spot", "set", 3), ("swords", "add", 1))

    # Each conditioned edge's rows, in the edge panel's Conditions.
    def edge(source, dest, when="SUCCESS", variable=None, value=None):
        t = TransitionModel(trigger="advance", source=source, dest=dest, when=when)
        if variable is not None:
            widget = _FlagConditionsWidget()
            widget.set_flag_groups([], "OR", [], list(config.variables))
            row = widget.add_condition()
            row.source.setCurrentIndex(row.source.findText(variable))
            row.value.control.setValue(value)
            t.flag_groups, t.flag_groups_op = widget.get_flag_groups()
        config.transitions.append(t)

    edge("enter", "spot_1")
    edge("spot_1", "found_1")
    edge("spot_1", "spot_2", "FAILURE")
    edge("found_1", "which_rack")
    edge("spot_2", "found_2")
    edge("spot_2", "spot_1", "FAILURE")
    edge("found_2", "which_rack")
    edge("which_rack", "rack_1", variable="swords", value=1)
    edge("which_rack", "rack_2")
    edge("rack_1", "resume")
    edge("rack_2", "open_gate")
    edge("resume", "spot_2", variable="next_spot", value=2)
    edge("resume", "spot_1")
    edge("open_gate", "enter")

    report = variables.check(config)
    assert report.errors == []
    assert report.warnings == []
    assert config.transitions[7].flag_groups == [FlagGroup(flags=[_is("swords", 1)])]
    assert next(s for s in config.states if s.id == "found_2").params["assignments"] == [
        {"variable": "next_spot", "op": "set", "value": 3},
        {"variable": "swords", "op": "add", "value": 1},
    ]
