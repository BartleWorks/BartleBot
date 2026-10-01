"""Inserting a node on an edge: it takes the destination's place, and the columns move over.

Real widget tests (QApplication required), in the style of test_insert_at_port. No
dialog is shown: choosing the node type is separate from the insert, so the insert is
driven directly.
"""

from __future__ import annotations

import pytest

from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel
from bartlebot.gui.canvas.node_item import GRID_H, GRID_W


def _node(state_id: str, column: int, row: int = 0) -> StateModel:
    return StateModel(
        id=state_id, node_type="sleep", params={"duration": 1.0}, position={"x": column * GRID_W, "y": row * GRID_H}
    )


def _edge(source: str, dest: str) -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when="SUCCESS")


def _line() -> BotConfig:
    """a -> b -> c along one row, with ``under_b`` below b and ``under_a`` below a, wired to nothing."""
    return BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=[_node("a", 0), _node("b", 1), _node("c", 2), _node("under_a", 0, 2), _node("under_b", 1, 2)],
        transitions=[_edge("a", "b"), _edge("b", "c")],
    )


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    def _build(config: BotConfig):
        c = BotCanvas()
        c.setup(config)
        c.enter_edit_mode()
        built.append(c)
        return c

    built = []
    yield _build
    for c in built:
        c.prepare_for_shutdown()


def _edge_item(c, source: str, dest: str):
    return next(item for s, _w, d, item in c.scene.edge_items if (s, d) == (source, dest))


def _spot(c, state_id: str) -> tuple[float, float]:
    position = next(s for s in c.config.states if s.id == state_id).position
    return position["x"], position["y"]


def test_the_new_node_takes_the_destinations_place_and_the_columns_move_over(canvas):
    """It used to land a row above or below the line: there is no cell between two neighbors."""
    c = canvas(_line())

    new_id = c.scene.insert_state_on_edge(_edge_item(c, "a", "b"), "sleep")

    assert _spot(c, new_id) == (GRID_W, 0.0), "where b was, on the row the edge ran along"
    assert _spot(c, "b") == (2 * GRID_W, 0.0)
    assert _spot(c, "c") == (3 * GRID_W, 0.0), "every column to the right, not only the next"
    assert _spot(c, "under_b") == (2 * GRID_W, 2 * GRID_H), "all of each column, wired or not"
    assert _spot(c, "a") == (0.0, 0.0) and _spot(c, "under_a") == (0.0, 2 * GRID_H)


def test_the_edge_is_split_through_the_new_node(canvas):
    c = canvas(_line())

    new_id = c.scene.insert_state_on_edge(_edge_item(c, "a", "b"), "sleep")

    wired = {(t.source, t.dest) for t in c.config.transitions}
    assert wired == {("a", new_id), (new_id, "b"), ("b", "c")}


def test_the_drawn_nodes_move_with_the_stored_ones(canvas):
    c = canvas(_line())
    before = c.scene.node_items["c"].pos().x()

    c.scene.insert_state_on_edge(_edge_item(c, "a", "b"), "sleep")

    assert c.scene.node_items["c"].pos().x() == before + GRID_W


def test_the_insert_and_the_move_are_one_undo_step(canvas):
    c = canvas(_line())

    c.scene.insert_state_on_edge(_edge_item(c, "a", "b"), "sleep")
    c.undo()

    assert [s.id for s in c.config.states] == ["a", "b", "c", "under_a", "under_b"]
    assert _spot(c, "b") == (GRID_W, 0.0) and _spot(c, "c") == (2 * GRID_W, 0.0)


def test_an_edge_with_a_column_to_spare_puts_the_node_in_it_and_moves_nothing(canvas):
    config = _line()
    config.transitions = [_edge("a", "c")]
    config.states = [s for s in config.states if s.id in ("a", "c")]
    c = canvas(config)

    new_id = c.scene.insert_state_on_edge(_edge_item(c, "a", "c"), "sleep")

    assert _spot(c, new_id) == (GRID_W, 0.0)
    assert _spot(c, "a") == (0.0, 0.0) and _spot(c, "c") == (2 * GRID_W, 0.0)


def test_a_backward_edge_moves_nothing(canvas):
    """A loop back has no "after" to move over; the node goes to the nearest free cell, as before."""
    config = _line()
    config.transitions.append(_edge("c", "a"))
    c = canvas(config)
    before = {s.id: (s.position["x"], s.position["y"]) for s in c.config.states}

    new_id = c.scene.insert_state_on_edge(_edge_item(c, "c", "a"), "sleep")

    assert new_id is not None
    assert {sid: _spot(c, sid) for sid in before} == before
