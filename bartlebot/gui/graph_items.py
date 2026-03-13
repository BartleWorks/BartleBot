"""QGraphicsItem subclasses shared by the Editor and Running tabs.

GraphView  — zoomable/pannable QGraphicsView (read-only or interactive).
NodeItem   — rounded-rect representing a single FSM state.
EdgeItem   — directed edge between two NodeItems, including self-loops
             and offset bidirectional pairs.
"""

import math

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QWheelEvent,
)
from PyQt6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
    QStyleOptionGraphicsItem,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_W = 220
NODE_H = 70
NODE_RADIUS = 12
ARROW_SIZE = 13
PORT_R = 6          # radius of connection-port circles
PORT_SPACING = 28   # vertical spacing between named output ports
_HEADER_H = 28      # height reserved for text label when returns are present

_FILL_NORMAL = QColor("#d5dce8")
_FILL_ACTIVE = QColor("#5cb85c")
_FILL_SELECTED = QColor("#b8cce4")
_BORDER_NORMAL = QColor("#8a9ab8")
_BORDER_ACTIVE = QColor("#2e7d32")
_BORDER_SELECTED = QColor("#2255aa")
_BORDER_INITIAL = QColor("#6D1ADA")
_EDGE_COLOR = QColor("#607090")
_PORT_COLOR = QColor("#3366bb")


def node_height(n_returns: int) -> int:
    """Compute the pixel height for a node with *n_returns* named output ports."""
    return max(NODE_H, (max(n_returns, 1) + 1) * PORT_SPACING)


# ---------------------------------------------------------------------------
# GraphView — reusable zoomable/pannable view
# ---------------------------------------------------------------------------

class GraphView(QGraphicsView):
    """Zoomable, pannable QGraphicsView.

    interactive=False (default): read-only, left-drag pans.
    interactive=True: items are selectable/movable, middle-mouse pans,
                      left-drag produces a rubber-band selection rect.
    """

    _ZOOM_FACTOR = 1.15

    def __init__(self, scene: QGraphicsScene, interactive: bool = False, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._user_zoomed = False
        self._pan_origin: QPointF | None = None

        if interactive:
            self.setInteractive(True)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            self.setInteractive(False)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def fit_scene(self) -> None:
        """Fit the full scene into the viewport. Resets user-zoom state."""
        self._user_zoomed = False
        if self.scene() and not self.scene().sceneRect().isEmpty():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._user_zoomed = True
        factor = self._ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / self._ZOOM_FACTOR
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._user_zoomed:
            self.fit_scene()

    # Middle-mouse panning for the interactive view
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_origin = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_origin is not None:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_origin is not None:
            self._pan_origin = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        returns: list[str] | None = None,
        is_initial: bool = False,
    ):
        super().__init__()
        self.state_id = state_id
        self._label = label or state_id
        self._active = False
        self._interactive = interactive
        self._is_initial = is_initial
        self._returns: list[str] = list(returns) if returns else []
        self._edges: list = []
        self.setZValue(1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, interactive)
        if interactive:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    @property
    def _height(self) -> int:
        return node_height(len(self._returns))

    def boundingRect(self) -> QRectF:
        h = self._height
        margin = PORT_R + 1 if self._interactive else 0
        return QRectF(-margin, -margin, NODE_W + margin * 2, h + margin * 2)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def center(self) -> QPointF:
        """Scene-coordinate center of this node."""
        return self.scenePos() + QPointF(NODE_W / 2, self._height / 2)

    def port_in(self) -> QPointF:
        """Scene-coordinate input port (left center)."""
        return self.scenePos() + QPointF(0, self._height / 2)

    def port_out(self) -> QPointF:
        """Scene-coordinate output port (right center, fallback for no named ports)."""
        return self.scenePos() + QPointF(NODE_W, self._height / 2)

    def _port_y(self, idx: int) -> float:
        """Y coordinate (local) for the idx-th named output port."""
        n = len(self._returns)
        h = self._height
        usable = h - _HEADER_H
        return _HEADER_H + (idx + 0.5) * usable / n

    def port_out_for(self, return_name: str | None) -> QPointF:
        """Scene-coordinate output port for the given return value name.

        Falls back to the center-right port when *return_name* is None or
        not found in this node's returns list.
        """
        if return_name and return_name in self._returns:
            idx = self._returns.index(return_name)
            return self.scenePos() + QPointF(NODE_W, self._port_y(idx))
        return self.port_out()

    def set_label(self, label: str | None) -> None:
        """Update the displayed label; falls back to state_id when None."""
        self._label = label or self.state_id
        self.update()

    def set_returns(self, returns: list[str]) -> None:
        """Update named output ports and redraw."""
        self._returns = list(returns)
        self.prepareGeometryChange()
        self.update()

    def set_interactive(self, interactive: bool) -> None:
        """Toggle movable/selectable flags at runtime (used for mode switching)."""
        self._interactive = interactive
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, interactive)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        if interactive:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.update()

    def set_initial(self, is_initial: bool) -> None:
        """Mark this node as the FSM initial state (draws a blue border)."""
        self._is_initial = is_initial
        self.update()

    def add_edge(self, edge) -> None:
        if edge not in self._edges:
            self._edges.append(edge)

    def remove_edge(self, edge) -> None:
        if edge in self._edges:
            self._edges.remove(edge)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in list(self._edges):
                try:
                    edge._rebuild()
                except RuntimeError:
                    pass
        return super().itemChange(change, value)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget=None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        selected = self._interactive and self.isSelected()
        if self._active:
            fill, border, border_w = _FILL_ACTIVE, _BORDER_ACTIVE, 2.5
        elif selected:
            fill, border, border_w = _FILL_SELECTED, _BORDER_SELECTED, 2.5
        elif self._is_initial:
            fill, border, border_w = _FILL_NORMAL, _BORDER_INITIAL, 2.5
        else:
            fill, border, border_w = _FILL_NORMAL, _BORDER_NORMAL, 1.5

        h = self._height
        painter.setPen(QPen(border, border_w))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(0, 0, NODE_W, h), NODE_RADIUS, NODE_RADIUS)

        painter.setPen(Qt.GlobalColor.black)

        if self._returns:
            # Header: label text in top _HEADER_H pixels
            painter.setFont(QFont("Segoe UI", 8))
            fm = painter.fontMetrics()
            text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
            painter.drawText(
                QRectF(8, 0, NODE_W - 16, _HEADER_H),
                Qt.AlignmentFlag.AlignCenter,
                text,
            )
            # Divider line between header and port area
            painter.setPen(QPen(border, 0.5))
            painter.drawLine(QPointF(4, _HEADER_H), QPointF(NODE_W - 4, _HEADER_H))
            # Named output ports + labels
            for i, name in enumerate(self._returns):
                py = self._port_y(i)
                # Port label text (right-aligned, inside node)
                painter.setPen(QColor("#444444"))
                painter.setFont(QFont("Segoe UI", 6))
                label_rect = QRectF(4, py - 8, NODE_W - PORT_R * 2 - 10, 16)
                painter.drawText(
                    label_rect,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    name,
                )
                # Port circle (interactive mode only)
                if self._interactive:
                    painter.setPen(QPen(_PORT_COLOR, 1.5))
                    painter.setBrush(QBrush(_PORT_COLOR))
                    painter.drawEllipse(QPointF(NODE_W, py), PORT_R, PORT_R)
        else:
            # No named ports — original centered-text layout
            if self._label != self.state_id:
                painter.setFont(QFont("Segoe UI", 9))
                fm = painter.fontMetrics()
                label_text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.drawText(QRectF(8, 4, NODE_W - 16, 40), Qt.AlignmentFlag.AlignCenter, label_text)
                painter.setPen(QColor("#666666"))
                painter.setFont(QFont("Segoe UI", 7))
                fm2 = painter.fontMetrics()
                id_text = fm2.elidedText(self.state_id, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.drawText(QRectF(8, 46, NODE_W - 16, 18), Qt.AlignmentFlag.AlignCenter, id_text)
            else:
                painter.setFont(QFont("Segoe UI", 9))
                fm = painter.fontMetrics()
                text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.drawText(QRectF(0, 0, NODE_W, NODE_H), Qt.AlignmentFlag.AlignCenter, text)
            # Single output port at right center (interactive only)
            if self._interactive:
                painter.setPen(QPen(_PORT_COLOR, 1.5))
                painter.setBrush(QBrush(_PORT_COLOR))
                painter.drawEllipse(QPointF(NODE_W, h / 2), PORT_R, PORT_R)

        # Input port always at left center
        if self._interactive:
            painter.setPen(QPen(_PORT_COLOR, 1.5))
            painter.setBrush(QBrush(_PORT_COLOR))
            painter.drawEllipse(QPointF(0, h / 2), PORT_R, PORT_R)


# ---------------------------------------------------------------------------
# EdgeItem
# ---------------------------------------------------------------------------

class EdgeItem(QGraphicsPathItem):
    """Directed edge between two NodeItems.

    - Normal edge: straight line (offset=0) or quadratic bezier (offset≠0).
    - Self-loop (src_node is dst_node): cubic bezier arc above the node.
    - when: the return value name the edge fires on (e.g. "SUCCESS"); drawn
      as a label near the midpoint when selected or when the edge is long.
    """

    def __init__(
        self,
        src_node: NodeItem,
        dst_node: NodeItem,
        offset: float = 0.0,
        when: str | None = None,
    ):
        super().__init__()
        self._src = src_node
        self._dst = dst_node
        self._offset = offset
        self._when = when
        self._is_self_loop = src_node is dst_node

        pen = QPen(_EDGE_COLOR, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)
        self.setZValue(0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._rebuild()

    def shape(self):
        """Widen the hit area so thin paths are easier to click."""
        from PyQt6.QtGui import QPainterPathStroker
        ps = QPainterPathStroker()
        ps.setWidth(12)
        return ps.createStroke(self.path())

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
        src_pt = self._src.port_out_for(self._when)
        dst_pt = self._dst.port_in()

        dx = dst_pt.x() - src_pt.x()
        dy = dst_pt.y() - src_pt.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return

        if dx < 0:
            # Backward edge: destination is left of source. Route below both
            # nodes with a cubic bezier to avoid passing through them.
            node_h = max(self._src._height, self._dst._height)
            arc = node_h * 0.8 + 60
            cp1 = QPointF(src_pt.x(), src_pt.y() + arc)
            cp2 = QPointF(dst_pt.x(), dst_pt.y() + arc)
            path.moveTo(src_pt)
            path.cubicTo(cp1, cp2, dst_pt)
            angle = math.atan2(dst_pt.y() - cp2.y(), dst_pt.x() - cp2.x())
        elif abs(self._offset) > 0:
            # Perpendicular unit normal for the lateral offset (bidirectional pairs)
            nx = -dy / length
            ny = dx / length
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
        top = QPointF(c.x(), c.y() - self._src._height / 2)

        path.moveTo(top)
        path.cubicTo(
            QPointF(top.x() - r, top.y() - r * 2.2),
            QPointF(top.x() + r, top.y() - r * 2.2),
            top,
        )
        # Arrowhead points downward (into the top of the node)
        _add_arrowhead(path, top, math.pi / 2)

    # ------------------------------------------------------------------
    # Label painting
    # ------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        super().paint(painter, option, widget)

        if not self._when:
            return

        path = self.path()
        p0 = path.pointAtPercent(0)
        p1 = path.pointAtPercent(1)
        length = math.hypot(p1.x() - p0.x(), p1.y() - p0.y())
        if not self.isSelected() and length < 150:
            return

        mid = path.pointAtPercent(0.5)
        text = self._when

        painter.save()
        painter.setFont(QFont("Segoe UI", 7))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text) + 6
        th = fm.height() + 2
        rect = QRectF(mid.x() - tw / 2, mid.y() - th / 2, tw, th)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor("#333333"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_arrowhead(path: QPainterPath, tip: QPointF, angle: float) -> None:
    s = ARROW_SIZE
    a1 = angle + math.pi * 5 / 6
    a2 = angle - math.pi * 5 / 6
    p1 = QPointF(tip.x() + s * math.cos(a1), tip.y() + s * math.sin(a1))
    p2 = QPointF(tip.x() + s * math.cos(a2), tip.y() + s * math.sin(a2))
    path.moveTo(p1)
    path.lineTo(tip)
    path.lineTo(p2)
