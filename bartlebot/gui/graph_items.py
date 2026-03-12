"""QGraphicsItem subclasses shared by the Editor and Running tabs.

NodeItem  — rounded-rect representing a single FSM state.
EdgeItem  — directed edge between two NodeItems, including self-loops
            and offset bidirectional pairs.
"""

import math

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath,
)
from PyQt6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem, QStyleOptionGraphicsItem,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_W = 220
NODE_H = 70
NODE_RADIUS = 12
ARROW_SIZE = 13

_FILL_NORMAL = QColor("#d5dce8")
_FILL_ACTIVE = QColor("#5cb85c")
_BORDER_NORMAL = QColor("#8a9ab8")
_BORDER_ACTIVE = QColor("#2e7d32")
_EDGE_COLOR = QColor("#607090")


# ---------------------------------------------------------------------------
# NodeItem
# ---------------------------------------------------------------------------

class NodeItem(QGraphicsItem):
    """Rounded-rect node showing a state's label."""

    def __init__(
        self,
        state_id: str,
        label: str | None = None,
        interactive: bool = False,
    ):
        super().__init__()
        self.state_id = state_id
        self._label = label or state_id
        self._active = False
        self.setZValue(1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, interactive)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, NODE_W, NODE_H)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def center(self) -> QPointF:
        """Scene-coordinate center of this node."""
        return self.scenePos() + QPointF(NODE_W / 2, NODE_H / 2)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget=None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        fill = _FILL_ACTIVE if self._active else _FILL_NORMAL
        border = _BORDER_ACTIVE if self._active else _BORDER_NORMAL
        border_w = 2.5 if self._active else 1.5

        painter.setPen(QPen(border, border_w))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(0, 0, NODE_W, NODE_H), NODE_RADIUS, NODE_RADIUS)

        painter.setPen(Qt.GlobalColor.black)
        painter.setFont(QFont("Segoe UI", 9))
        fm = painter.fontMetrics()
        text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
        painter.drawText(
            QRectF(8, 0, NODE_W - 16, NODE_H),
            Qt.AlignmentFlag.AlignCenter,
            text,
        )


# ---------------------------------------------------------------------------
# EdgeItem
# ---------------------------------------------------------------------------

class EdgeItem(QGraphicsPathItem):
    """Directed edge between two NodeItems.

    - Normal edge: straight line (offset=0) or quadratic bezier (offset≠0).
    - Self-loop (src_node is dst_node): cubic bezier arc above the node.
    """

    def __init__(
        self,
        src_node: NodeItem,
        dst_node: NodeItem,
        offset: float = 0.0,
    ):
        super().__init__()
        self._src = src_node
        self._dst = dst_node
        self._offset = offset
        self._is_self_loop = src_node is dst_node

        pen = QPen(_EDGE_COLOR, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setZValue(0)
        self._rebuild()

    def _rebuild(self) -> None:
        path = QPainterPath()
        if self._is_self_loop:
            self._build_self_loop(path)
        else:
            self._build_edge(path)
        self.setPath(path)

    # ------------------------------------------------------------------
    # Path builders
    # ------------------------------------------------------------------

    def _build_edge(self, path: QPainterPath) -> None:
        src_c = self._src.center()
        dst_c = self._dst.center()

        dx = dst_c.x() - src_c.x()
        dy = dst_c.y() - src_c.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return

        # Perpendicular unit normal for the lateral offset
        nx = -dy / length
        ny = dx / length

        src_off = QPointF(src_c.x() + nx * self._offset, src_c.y() + ny * self._offset)
        dst_off = QPointF(dst_c.x() + nx * self._offset, dst_c.y() + ny * self._offset)

        # Clamp endpoints to the node bounding-box boundary
        src_pt = _boundary_point(src_off, dst_off)
        dst_pt = _boundary_point(dst_off, src_off)

        if abs(self._offset) > 0:
            # Quadratic bezier for bidirectional pairs — bulges out
            mid = QPointF(
                (src_pt.x() + dst_pt.x()) / 2 + nx * abs(self._offset) * 1.5,
                (src_pt.y() + dst_pt.y()) / 2 + ny * abs(self._offset) * 1.5,
            )
            path.moveTo(src_pt)
            path.quadTo(mid, dst_pt)
            angle = math.atan2(dst_pt.y() - mid.y(), dst_pt.x() - mid.x())
        else:
            path.moveTo(src_pt)
            path.lineTo(dst_pt)
            angle = math.atan2(dst_pt.y() - src_pt.y(), dst_pt.x() - src_pt.x())

        _add_arrowhead(path, dst_pt, angle)

    def _build_self_loop(self, path: QPainterPath) -> None:
        """Small cubic bezier arc above the node."""
        c = self._src.center()
        r = 30
        top = QPointF(c.x(), c.y() - NODE_H / 2)

        path.moveTo(top)
        path.cubicTo(
            QPointF(top.x() - r, top.y() - r * 2.2),
            QPointF(top.x() + r, top.y() - r * 2.2),
            top,
        )
        # Arrowhead points downward (into the top of the node)
        _add_arrowhead(path, top, math.pi / 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _boundary_point(origin: QPointF, toward: QPointF) -> QPointF:
    """Find the point where the ray from *origin* toward *toward* exits
    the node's bounding box (centred at *origin*)."""
    dx = toward.x() - origin.x()
    dy = toward.y() - origin.y()
    length = math.hypot(dx, dy)
    if length < 1:
        return origin
    ux, uy = dx / length, dy / length
    hw, hh = NODE_W / 2, NODE_H / 2
    tx = hw / abs(ux) if abs(ux) > 1e-4 else float("inf")
    ty = hh / abs(uy) if abs(uy) > 1e-4 else float("inf")
    t = min(tx, ty)
    return QPointF(origin.x() + ux * t, origin.y() + uy * t)


def _add_arrowhead(path: QPainterPath, tip: QPointF, angle: float) -> None:
    s = ARROW_SIZE
    a1 = angle + math.pi * 5 / 6
    a2 = angle - math.pi * 5 / 6
    p1 = QPointF(tip.x() + s * math.cos(a1), tip.y() + s * math.sin(a1))
    p2 = QPointF(tip.x() + s * math.cos(a2), tip.y() + s * math.sin(a2))
    path.moveTo(p1)
    path.lineTo(tip)
    path.lineTo(p2)
