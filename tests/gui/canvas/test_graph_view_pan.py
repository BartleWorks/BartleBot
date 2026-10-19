"""A middle-button drag pans the canvas, in Edit and Run mode alike (#270).

``GraphView`` already panned on Space + left-drag.  Both gestures share one pan state, so
these tests pin what is specific to the middle button: it works without Space, in the
interactive (Edit) view and the read-only (Run) view, it leaves the left button's rubber
band alone, and a release of some other button does not end the pan.
"""

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QGraphicsRectItem, QGraphicsScene


@pytest.fixture(params=[True, False], ids=["edit", "run"])
def view(qapp, request):
    from bartlebot.gui.canvas.graph_view import GraphView

    scene = QGraphicsScene(0, 0, 4000, 4000)
    item = QGraphicsRectItem(100, 100, 50, 50)
    item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable)
    scene.addItem(item)
    v = GraphView(scene, interactive=request.param)
    v.resize(400, 300)
    v.show()
    v.horizontalScrollBar().setValue(500)
    v.verticalScrollBar().setValue(500)
    yield v
    v.close()


def _send(view, kind, pos, button, buttons=None):
    vp = view.viewport()
    event = QMouseEvent(
        kind,
        QPointF(*pos),
        QPointF(vp.mapToGlobal(QPointF(*pos).toPoint())),
        button,
        button if buttons is None else buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(vp, event)


def _press(view, pos, button):
    _send(view, QEvent.Type.MouseButtonPress, pos, button)


def _move(view, pos, buttons):
    _send(view, QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton, buttons)


def _release(view, pos, button):
    _send(view, QEvent.Type.MouseButtonRelease, pos, button, Qt.MouseButton.NoButton)


def test_middle_drag_pans_opposite_to_the_pointer(view):
    h, v = view.horizontalScrollBar().value(), view.verticalScrollBar().value()
    _press(view, (200, 150), Qt.MouseButton.MiddleButton)
    assert view.panning
    _move(view, (170, 180), Qt.MouseButton.MiddleButton)
    assert view.horizontalScrollBar().value() == h + 30
    assert view.verticalScrollBar().value() == v - 30
    _release(view, (170, 180), Qt.MouseButton.MiddleButton)
    assert not view.panning


def test_middle_drag_shows_a_closed_hand_then_gives_the_cursor_back(view):
    _press(view, (200, 150), Qt.MouseButton.MiddleButton)
    assert view.viewport().cursor().shape() == Qt.CursorShape.ClosedHandCursor
    _release(view, (200, 150), Qt.MouseButton.MiddleButton)
    assert view.viewport().cursor().shape() != Qt.CursorShape.ClosedHandCursor


def test_middle_drag_over_a_node_pans_instead_of_moving_it(view):
    item = view.scene().items()[0]
    start = item.pos()
    over = view.mapFromScene(QPointF(125, 125))
    _press(view, (over.x(), over.y()), Qt.MouseButton.MiddleButton)
    _move(view, (over.x() + 40, over.y() + 40), Qt.MouseButton.MiddleButton)
    _release(view, (over.x() + 40, over.y() + 40), Qt.MouseButton.MiddleButton)
    assert item.pos() == start


def test_a_plain_move_without_a_press_does_not_pan(view):
    h = view.horizontalScrollBar().value()
    _move(view, (100, 100), Qt.MouseButton.NoButton)
    _move(view, (140, 100), Qt.MouseButton.NoButton)
    assert view.horizontalScrollBar().value() == h


def test_releasing_another_button_does_not_end_a_middle_pan(view):
    _press(view, (200, 150), Qt.MouseButton.MiddleButton)
    _press(view, (200, 150), Qt.MouseButton.RightButton)
    _release(view, (200, 150), Qt.MouseButton.RightButton)
    assert view.panning
    _release(view, (200, 150), Qt.MouseButton.MiddleButton)
    assert not view.panning


def test_space_left_drag_still_pans_and_keeps_the_open_hand_while_space_is_held(view):
    from PyQt6.QtGui import QKeyEvent

    QApplication.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    h = view.horizontalScrollBar().value()
    _press(view, (200, 150), Qt.MouseButton.LeftButton)
    _move(view, (180, 150), Qt.MouseButton.LeftButton)
    assert view.horizontalScrollBar().value() == h + 20
    _release(view, (180, 150), Qt.MouseButton.LeftButton)
    assert not view.panning
    assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_middle_release_after_a_pan_with_space_held_goes_back_to_the_open_hand(view):
    from PyQt6.QtGui import QKeyEvent

    QApplication.sendEvent(view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    _press(view, (200, 150), Qt.MouseButton.MiddleButton)
    _release(view, (200, 150), Qt.MouseButton.MiddleButton)
    assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
