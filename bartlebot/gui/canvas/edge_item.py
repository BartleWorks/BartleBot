"""EdgeItem — directed edge between two NodeItems."""

import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QStyleOptionGraphicsItem

from bartlebot.gui.canvas.node_item import NodeItem
from bartlebot.gui.theme import THEME

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARROW_SIZE = 13

_CORNER_R = 8  # rounded-corner radius on orthogonal paths (px)
_BACK_H_STEP = 20  # how far backward edges extend horizontally beyond node edge
_SELF_LOOP_H_STEP = -15  # vertical bridge clearance for self-loops — must exceed arrowhead back-reach (~11 px)
_SELF_LOOP_ARM = 20  # horizontal arm length for self-loops (visual width)
BACK_PADDING = 30  # minimum gap between backward-edge bridge and the node edges
_BACK_PADDING = BACK_PADDING  # alias kept for internal use
_STRAIGHT_THRESHOLD = 15  # vertical delta (px) below which forward edges go straight


def _edge_color():
    return THEME.qcolor("edge_color")


def _edge_hover():
    return THEME.qcolor("edge_hover")


def _edge_selected():
    return THEME.qcolor("edge_selected")


def _when_edge_color(when: str | None):
    if when is None:
        return _edge_color()
    name = when.upper()
    if name in ("SUCCESS", "TRUE"):
        return THEME.qcolor("chip_success_bg")
    if name in ("FAILURE", "FALSE"):
        return THEME.qcolor("chip_failure_bg")
    return THEME.qcolor("chip_neutral_bg")


# ---------------------------------------------------------------------------
# Shared bridge-Y geometry — single source of truth used by both EdgeItem
# (dynamic rebuilds after node drags) and EditScene.setup() (corridor
# pre-computation).  Keep these three functions in sync with each other.
# ---------------------------------------------------------------------------


def bridge_y_below(src_node: NodeItem, dst_node: NodeItem) -> float:
    """Y for a below-corridor bridge: BACK_PADDING below the bottom of the lower node."""
    src_bot = src_node.scenePos().y() + src_node._height
    dst_bot = dst_node.scenePos().y() + dst_node._height
    return max(src_bot, dst_bot) + BACK_PADDING


def bridge_y_above(src_node: NodeItem, dst_node: NodeItem) -> float:
    """Y for an above-corridor bridge: BACK_PADDING above the top of the higher node."""
    return min(src_node.scenePos().y(), dst_node.scenePos().y()) - BACK_PADDING


def is_above_routed(src_node: NodeItem, when: str | None) -> bool:
    """True when a backward edge from src_node with the given return label should
    route above the node row (topmost-port on a node with ≥ 2 named returns)."""
    returns = src_node._returns
    return when is not None and len(returns) >= 2 and when == returns[0]


# ---------------------------------------------------------------------------
# EdgeItem
# ---------------------------------------------------------------------------


class EdgeItem(QGraphicsPathItem):
    """Directed edge between two NodeItems.

    - Forward edge (dst to the right): Z-shaped orthogonal path with rounded corners.
    - Backward edge (dst to the left): Z-shaped path mirrored horizontally, bridge
      clamped to clear both node boxes vertically.
    - Self-loop (src_node is dst_node): cubic bezier arc above the node.
    - when: the return value name the edge fires on (e.g. "SUCCESS").
    """

    def __init__(
        self,
        src_node: NodeItem,
        dst_node: NodeItem,
        offset: float = 0.0,
        when: str | None = None,
        bridge_y_override: float | None = None,
    ):
        super().__init__()
        self._src = src_node
        self._dst = dst_node
        self._offset = offset
        self._when = when
        self._is_self_loop = src_node is dst_node
        self._hovered = False
        self._bridge_y_override = bridge_y_override

        self.setZValue(0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._rebuild()
        self._bridge_y_override = None  # only applies to the initial build

    def shape(self):
        """Widen the hit area so thin paths are easier to click."""
        from PyQt6.QtGui import QPainterPathStroker

        ps = QPainterPathStroker()
        ps.setWidth(12)
        return ps.createStroke(self.path())

    def _should_route_above(self) -> bool:
        return is_above_routed(self._src, self._when)

    def _rebuild(self) -> None:
        path = QPainterPath()
        if self._is_self_loop:
            self._build_self_loop(path)
        else:
            self._build_edge(path)
        self.setPath(path)

    # ------------------------------------------------------------------
    # Hover events
    # ------------------------------------------------------------------

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isSelected():
            pen = QPen(_edge_selected(), 2.5, Qt.PenStyle.DashLine)
        elif self._hovered:
            pen = QPen(_edge_hover(), 2.5)
        else:
            pen = QPen(_when_edge_color(self._when), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

    # ------------------------------------------------------------------
    # Path builders
    # ------------------------------------------------------------------

    def _build_edge(self, path: QPainterPath) -> None:
        src_pt = self._src.port_out_for(self._when)
        dst_pt = self._dst.port_in()
        if math.hypot(dst_pt.x() - src_pt.x(), dst_pt.y() - src_pt.y()) < 1:
            return
        src_flipped = self._src._flipped
        dst_flipped = self._dst._flipped
        if src_flipped or dst_flipped:
            # Flipped ports: use direction-aware routing so path extensions always
            # go *outside* the node body regardless of which side the port is on.
            # src exits left (-1) when flipped, right (+1) when normal.
            # dst is approached from right (+1) when flipped, left (-1) when normal.
            src_sign = -1 if src_flipped else 1
            dst_sign = 1 if dst_flipped else -1
            if src_flipped and dst_flipped and dst_pt.x() <= src_pt.x():
                # Both ports face each other (src exits left, dst accepts from right)
                # and dst is to the left — the natural forward direction for flipped
                # nodes. Route as a simple Z-path through the horizontal midpoint;
                # no node-clearing bridge needed.
                self._build_forward_ortho(path, src_pt, dst_pt)
            else:
                self._build_directed_ortho(path, src_pt, dst_pt, src_sign, dst_sign)
        elif dst_pt.x() > src_pt.x():
            self._build_forward_ortho(path, src_pt, dst_pt)
        else:
            self._build_backward_ortho(path, src_pt, dst_pt)

    def _build_forward_ortho(self, path: QPainterPath, src_pt: QPointF, dst_pt: QPointF) -> None:
        """Z-shaped orthogonal path for forward (left-to-right) edges.

        When the vertical difference is small (nearly horizontal), falls back
        to a straight line so the Z elbow doesn't look broken.
        """
        if abs(dst_pt.y() - src_pt.y()) < _STRAIGHT_THRESHOLD:
            path.moveTo(src_pt)
            path.lineTo(dst_pt)
            _add_arrowhead(path, dst_pt, math.atan2(dst_pt.y() - src_pt.y(), dst_pt.x() - src_pt.x()))
            return
        # For bidirectional pairs the offset shifts the vertical segment laterally
        # so the two mirrored paths are visually separated.
        mid_x = (src_pt.x() + dst_pt.x()) / 2 + self._offset * 0.5
        waypoints = [
            src_pt,
            QPointF(mid_x, src_pt.y()),
            QPointF(mid_x, dst_pt.y()),
            dst_pt,
        ]
        _draw_ortho_path(path, waypoints)

    def _build_directed_ortho(
        self,
        path: QPainterPath,
        src_pt: QPointF,
        dst_pt: QPointF,
        src_sign: int,
        dst_sign: int,
    ) -> None:
        """Orthogonal path with parameterized exit/approach directions.

        *src_sign* (+1/-1): direction the departure arm extends from the source
        port (+1 = rightward for normal output, -1 = leftward for flipped output).

        *dst_sign* (+1/-1): direction the arrival arm extends from the destination
        port (-1 = leftward for normal input, +1 = rightward for flipped input).

        When both signs are equal the departure and arrival arms are on the same
        side, so a simple 4-point Z-path suffices — no horizontal bridge needed.
        When the signs differ a 6-point bridge path is used instead.
        """
        if src_sign == dst_sign:
            # Both ports face the same direction: 4-point Z-path.
            # The vertical column sits at the outermost arm on that side.
            if src_sign > 0:
                x_col = max(src_pt.x() + _BACK_H_STEP, dst_pt.x() + _BACK_H_STEP)
            else:
                x_col = min(src_pt.x() - _BACK_H_STEP, dst_pt.x() - _BACK_H_STEP)
            if abs(dst_pt.y() - src_pt.y()) < _STRAIGHT_THRESHOLD:
                path.moveTo(src_pt)
                path.lineTo(dst_pt)
                _add_arrowhead(path, dst_pt, math.atan2(dst_pt.y() - src_pt.y(), dst_pt.x() - src_pt.x()))
                return
            waypoints = [
                src_pt,
                QPointF(x_col, src_pt.y()),
                QPointF(x_col, dst_pt.y()),
                dst_pt,
            ]
            _draw_ortho_path(path, waypoints)
        else:
            # Ports face opposite directions: 6-point bridge path.
            if self._bridge_y_override is not None:
                mid_y = self._bridge_y_override
            elif self._should_route_above():
                mid_y = bridge_y_above(self._src, self._dst)
            else:
                mid_y = bridge_y_below(self._src, self._dst)
            waypoints = [
                src_pt,
                QPointF(src_pt.x() + src_sign * _BACK_H_STEP, src_pt.y()),
                QPointF(src_pt.x() + src_sign * _BACK_H_STEP, mid_y),
                QPointF(dst_pt.x() + dst_sign * _BACK_H_STEP, mid_y),
                QPointF(dst_pt.x() + dst_sign * _BACK_H_STEP, dst_pt.y()),
                dst_pt,
            ]
            _draw_ortho_path(path, waypoints)

    def _build_backward_ortho(self, path: QPainterPath, src_pt: QPointF, dst_pt: QPointF) -> None:
        """Backward (right-to-left) edge routed as a 6-point Z-shape."""
        self._build_directed_ortho(path, src_pt, dst_pt, src_sign=1, dst_sign=-1)

    def _build_self_loop(self, path: QPainterPath) -> None:
        """Orthogonal path departing from the output port, routing above or below
        the node (based on port position), and arriving at the input port."""
        src_pt = self._src.port_out_for(self._when)
        dst_pt = self._src.port_in()
        node_top = self._src.scenePos().y()
        src_h = self._src._height
        src_center_y = node_top + src_h / 2
        if src_pt.y() <= src_center_y:
            bridge_y = node_top - _BACK_PADDING - _SELF_LOOP_H_STEP
        else:
            bridge_y = node_top + src_h + _BACK_PADDING + _SELF_LOOP_H_STEP
        flipped = self._src._flipped
        src_sign = -1 if flipped else 1
        dst_sign = 1 if flipped else -1
        waypoints = [
            src_pt,
            QPointF(src_pt.x() + src_sign * _SELF_LOOP_ARM, src_pt.y()),
            QPointF(src_pt.x() + src_sign * _SELF_LOOP_ARM, bridge_y),
            QPointF(dst_pt.x() + dst_sign * _SELF_LOOP_ARM, bridge_y),
            QPointF(dst_pt.x() + dst_sign * _SELF_LOOP_ARM, dst_pt.y()),
            dst_pt,
        ]
        _draw_ortho_path(path, waypoints)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _draw_ortho_path(path: QPainterPath, waypoints: list) -> None:
    """Draw an orthogonal path through *waypoints* with rounded corners and an arrowhead.

    Corners are rounded with a quadratic bezier of radius _CORNER_R so the
    path looks like PowerPoint's elbow connectors rather than a sharp polygon.
    The arrowhead is appended at the final waypoint pointing along the last segment.
    """
    r = _CORNER_R
    path.moveTo(waypoints[0])

    for i in range(1, len(waypoints) - 1):
        prev = waypoints[i - 1]
        curr = waypoints[i]
        nxt = waypoints[i + 1]

        d1 = math.hypot(curr.x() - prev.x(), curr.y() - prev.y())
        d2 = math.hypot(nxt.x() - curr.x(), nxt.y() - curr.y())
        eff_r = min(r, d1 / 2.0, d2 / 2.0)

        if d1 < 1 or d2 < 1 or eff_r < 0.5:
            path.lineTo(curr)
            continue

        # Point eff_r before the corner (along incoming segment)
        t1 = eff_r / d1
        approach = QPointF(
            curr.x() + (prev.x() - curr.x()) * t1,
            curr.y() + (prev.y() - curr.y()) * t1,
        )
        # Point eff_r after the corner (along outgoing segment)
        t2 = eff_r / d2
        depart = QPointF(
            curr.x() + (nxt.x() - curr.x()) * t2,
            curr.y() + (nxt.y() - curr.y()) * t2,
        )
        path.lineTo(approach)
        path.quadTo(curr, depart)

    path.lineTo(waypoints[-1])

    tip = waypoints[-1]
    pre = waypoints[-2]
    _add_arrowhead(path, tip, math.atan2(tip.y() - pre.y(), tip.x() - pre.x()))


def _add_arrowhead(path: QPainterPath, tip: QPointF, angle: float) -> None:
    s = ARROW_SIZE
    a1 = angle + math.pi * 5 / 6
    a2 = angle - math.pi * 5 / 6
    p1 = QPointF(tip.x() + s * math.cos(a1), tip.y() + s * math.sin(a1))
    p2 = QPointF(tip.x() + s * math.cos(a2), tip.y() + s * math.sin(a2))
    path.moveTo(p1)
    path.lineTo(tip)
    path.lineTo(p2)
