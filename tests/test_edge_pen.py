"""A conditional edge is drawn dashed with flat caps; every other edge solid.

Flat caps are the point: a round cap makes every dash a curved shape of its own, and
on the sword puzzle that made a pan frame cost 17 ms at zoom 1 and 96 at zoom 0.5.
"""

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor

from bartlebot.gui.canvas.edge_item import CONDITIONAL_DASHES, EdgeItem, _edge_pen
from bartlebot.gui.canvas.node_item import NodeItem


class _PenRecorder:
    """Stands in for the QPainter EdgeItem.paint draws with, keeping the last pen set."""

    def __init__(self):
        self.pen = None

    def setPen(self, pen):  # noqa: N802 - QPainter's name
        self.pen = pen

    def setRenderHint(self, *_):  # noqa: N802
        pass

    def setBrush(self, *_):  # noqa: N802
        pass

    def drawPath(self, *_):  # noqa: N802
        pass


def test_a_conditional_pen_is_dashed_with_flat_caps():
    pen = _edge_pen(QColor("green"), 2.0, conditional=True)
    assert pen.dashPattern() == CONDITIONAL_DASHES
    assert pen.capStyle() == Qt.PenCapStyle.FlatCap


def test_an_unconditional_pen_is_solid_with_round_caps():
    pen = _edge_pen(QColor("green"), 2.0, conditional=False)
    assert pen.style() == Qt.PenStyle.SolidLine
    assert pen.capStyle() == Qt.PenCapStyle.RoundCap


@pytest.fixture
def conditional_edge(qapp):
    src, dst = NodeItem("a", returns=["SUCCESS"]), NodeItem("b")
    dst.setPos(QPointF(600, 200))
    return EdgeItem(src, dst, when="SUCCESS", has_flags=True)


@pytest.mark.parametrize("hovered", [False, True])
def test_paint_draws_a_conditional_edge_dashed(conditional_edge, hovered):
    conditional_edge._hovered = hovered
    painter = _PenRecorder()
    conditional_edge.paint(painter, None)
    assert painter.pen.dashPattern() == CONDITIONAL_DASHES
    assert painter.pen.capStyle() == Qt.PenCapStyle.FlatCap
