"""QGraphicsItem subclasses shared by the Editor and Running tabs.

GraphView  — zoomable/pannable QGraphicsView (read-only or interactive).
NodeItem   — rounded-rect representing a single FSM state.
EdgeItem   — directed edge between two NodeItems, including self-loops
             and offset bidirectional pairs.
"""

import math

from PyQt6.QtCore import Qt, QRectF, QPointF, QEasingCurve, QSequentialAnimationGroup, QVariantAnimation
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QKeyEvent, QWheelEvent,
)
from PyQt6.QtCore import pyqtSignal

from bartlebot.gui.theme import THEME
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
_PORT_HIT_R = 16    # hit-test radius for port cursor feedback

_FILL_NORMAL        = THEME.qcolor("node_fill_normal")
_FILL_HOVER         = THEME.qcolor("node_fill_hover")
_FILL_ACTIVE        = THEME.qcolor("node_fill_active")
_FILL_ACTIVE_DIM    = THEME.qcolor("node_fill_active_dim")
_FILL_SELECTED      = THEME.qcolor("node_fill_selected")
_FILL_ERROR         = THEME.qcolor("node_fill_error")
_FILL_ERROR_HOVER   = THEME.qcolor("node_fill_error_hover")
_FILL_WARNING       = THEME.qcolor("node_fill_warning")
_FILL_WARNING_HOVER = THEME.qcolor("node_fill_warning_hover")
_BORDER_NORMAL      = THEME.qcolor("node_border_normal")
_BORDER_ACTIVE      = THEME.qcolor("node_border_active")
_BORDER_SELECTED    = THEME.qcolor("node_border_selected")
_BORDER_ERROR       = THEME.qcolor("node_border_error")
_BORDER_WARNING     = THEME.qcolor("node_border_warning")
_BADGE_INITIAL      = THEME.qcolor("badge_initial")
_BADGE_TERMINAL     = THEME.qcolor("badge_terminal")
_EDGE_COLOR         = THEME.qcolor("edge_color")
_EDGE_HOVER_COLOR   = THEME.qcolor("edge_hover_color")
_EDGE_SEL_COLOR     = THEME.qcolor("edge_sel_color")
_PORT_COLOR         = THEME.qcolor("port_color")
_BADGE_FONT         = THEME.font_node_sub()

_CORNER_R = 8           # rounded-corner radius on orthogonal paths (px)
_BACK_H_STEP = 30       # how far backward edges extend horizontally beyond node edge
_BACK_PADDING = 10      # minimum gap between backward-edge bridge and the node edges
_STRAIGHT_THRESHOLD = 15  # vertical delta (px) below which forward edges go straight


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

    view_changed = pyqtSignal()  # emitted after zoom so overlays can repaint
    _ZOOM_FACTOR = 1.15

    def __init__(self, scene: QGraphicsScene, interactive: bool = False, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._user_zoomed = False
        self._pan_origin: QPointF | None = None
        self._space_held: bool = False

        if interactive:
            self.setInteractive(True)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            self.setInteractive(False)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def fit_scene(self) -> None:
        """Fit the full scene content into the viewport. Resets user-zoom state."""
        self._user_zoomed = False
        scene = self.scene()
        if scene is None:
            return
        bounds = scene.itemsBoundingRect()
        if bounds.isEmpty():
            return
        self.fitInView(bounds.adjusted(-80, -60, 80, 60), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._user_zoomed = True
        factor = self._ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / self._ZOOM_FACTOR
        self.scale(factor, factor)
        self.view_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._user_zoomed:
            self.fit_scene()

    # Space + left-drag panning
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            if self._pan_origin is None:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if self._pan_origin is None:
                self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._space_held and event.button() == Qt.MouseButton.LeftButton:
            self._pan_origin = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
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
        if event.button() == Qt.MouseButton.LeftButton and self._pan_origin is not None:
            self._pan_origin = None
            if self._space_held:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.viewport().unsetCursor()
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
        is_terminal: bool = False,
        is_error: bool = False,
        is_warning: bool = False,
        node_type: str = "",
    ):
        super().__init__()
        self.state_id = state_id
        self._label = label or state_id
        self._node_type = node_type
        self._active = False
        self._active_fill: QColor = _FILL_ACTIVE
        self._hovered = False

        _anim_fwd = QVariantAnimation()
        _anim_fwd.setStartValue(_FILL_ACTIVE)
        _anim_fwd.setEndValue(_FILL_ACTIVE_DIM)
        _anim_fwd.setDuration(800)
        _anim_fwd.setEasingCurve(QEasingCurve.Type.InOutSine)
        _anim_fwd.valueChanged.connect(self._on_pulse_value)

        _anim_rev = QVariantAnimation()
        _anim_rev.setStartValue(_FILL_ACTIVE_DIM)
        _anim_rev.setEndValue(_FILL_ACTIVE)
        _anim_rev.setDuration(800)
        _anim_rev.setEasingCurve(QEasingCurve.Type.InOutSine)
        _anim_rev.valueChanged.connect(self._on_pulse_value)

        self._pulse_group = QSequentialAnimationGroup()
        self._pulse_group.addAnimation(_anim_fwd)
        self._pulse_group.addAnimation(_anim_rev)
        self._pulse_group.setLoopCount(-1)
        self._reasons: list[str] = []
        self._interactive = interactive
        self._is_initial = is_initial
        self._is_terminal = is_terminal
        self._is_error = is_error
        self._is_warning = is_warning
        self._returns: list[str] = list(returns) if returns else []
        self._edges: list = []
        self._is_search_match: bool = False
        self._is_search_current: bool = False
        self.setZValue(1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, interactive)
        if interactive:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
            self.setAcceptHoverEvents(True)
            self.setCursor(Qt.CursorShape.SizeAllCursor)

    @property
    def _height(self) -> int:
        return node_height(len(self._returns))

    def boundingRect(self) -> QRectF:
        h = self._height
        margin = PORT_R + 1 if self._interactive else 0
        if self._is_search_match:
            margin = max(margin, 8)
        return QRectF(-margin, -margin, NODE_W + margin * 2, h + margin * 2)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._active_fill = _FILL_ACTIVE
            self._pulse_group.start()
        else:
            self._pulse_group.stop()
            self._active_fill = _FILL_ACTIVE
        self.update()

    def _on_pulse_value(self, color: QColor) -> None:
        self._active_fill = color
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

    def set_node_type(self, node_type: str) -> None:
        """Update the displayed node type sub-label."""
        self._node_type = node_type
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
            self.setAcceptHoverEvents(True)
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setAcceptHoverEvents(False)
            self._hovered = False
            self.unsetCursor()
        self.update()

    def set_initial(self, is_initial: bool) -> None:
        """Mark this node as the FSM initial state (draws a purple border)."""
        self._is_initial = is_initial
        self.update()

    def set_terminal(self, is_terminal: bool) -> None:
        """Mark this node as a terminal state with no exit transitions (draws a ■ badge)."""
        self._is_terminal = is_terminal
        self.update()

    def set_error(self, is_error: bool, reasons: list[str] | None = None) -> None:
        """Mark this node as having a configuration error (red tint + border + tooltip)."""
        self._is_error = is_error
        if reasons is not None:
            self._reasons = list(reasons)
        self._update_tooltip()
        self.update()

    def set_warning(self, is_warning: bool, reasons: list[str] | None = None) -> None:
        """Mark this node as having a configuration warning (amber tint + border + tooltip)."""
        self._is_warning = is_warning
        if reasons is not None:
            self._reasons = list(reasons)
        self._update_tooltip()
        self.update()

    def set_search_match(self, is_match: bool, is_current: bool = False) -> None:
        """Highlight this node as a quick-search match (cyan ring overlay)."""
        if self._is_search_match == is_match and self._is_search_current == is_current:
            return
        self.prepareGeometryChange()
        self._is_search_match = is_match
        self._is_search_current = is_current
        self.update()

    def _update_tooltip(self) -> None:
        if (self._is_error or self._is_warning) and self._reasons:
            self.setToolTip("\n".join(self._reasons))
        else:
            self.setToolTip("")

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

    # ------------------------------------------------------------------
    # Hover events
    # ------------------------------------------------------------------

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event) -> None:
        pos = event.pos()
        h = self._height
        near_port = False
        if self._returns:
            for i in range(len(self._returns)):
                if math.hypot(pos.x() - NODE_W, pos.y() - self._port_y(i)) <= _PORT_HIT_R:
                    near_port = True
                    break
        else:
            if math.hypot(pos.x() - NODE_W, pos.y() - h / 2) <= _PORT_HIT_R:
                near_port = True
        self.setCursor(Qt.CursorShape.CrossCursor if near_port else Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget=None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        selected = self._interactive and self.isSelected()
        if self._active:
            fill = self._active_fill
            border, border_w = _BORDER_ACTIVE, 2.5
        elif selected:
            fill, border, border_w = _FILL_SELECTED, _BORDER_SELECTED, 2.5
        elif self._is_error:
            fill = _FILL_ERROR_HOVER if self._hovered else _FILL_ERROR
            border, border_w = _BORDER_ERROR, 2.5
        elif self._is_warning:
            fill = _FILL_WARNING_HOVER if self._hovered else _FILL_WARNING
            border, border_w = _BORDER_WARNING, 2.5
        elif self._hovered:
            fill, border, border_w = _FILL_HOVER, _BORDER_NORMAL, 2.0
        else:
            fill, border, border_w = _FILL_NORMAL, _BORDER_NORMAL, 1.5

        h = self._height
        lod = option.levelOfDetailFromTransform(painter.worldTransform())

        # Search match ring — drawn first as a solid filled rect so it sits
        # behind the node background with no gap.
        if self._is_search_match:
            ring_color = (
                THEME.qcolor("search_match_current")
                if self._is_search_current
                else THEME.qcolor("search_match_other")
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ring_color))
            painter.drawRoundedRect(QRectF(-7, -7, NODE_W + 14, h + 14), NODE_RADIUS + 5, NODE_RADIUS + 5)

        painter.setPen(QPen(border, border_w))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(0, 0, NODE_W, h), NODE_RADIUS, NODE_RADIUS)

        # --- Compact LOD: label-only with word-wrap when zoomed out ---
        from bartlebot.settings_manager import SettingsManager
        _lod_threshold = SettingsManager.instance().get(
            "canvas", "lod_compact_threshold", default=0.6
        )
        if lod < _lod_threshold:
            compact_font = QFont(THEME.font_ui_family, 14)
            painter.setFont(compact_font)
            painter.setPen(Qt.GlobalColor.black)
            _wrap_flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter) | int(Qt.TextFlag.TextWordWrap)
            painter.drawText(QRectF(6, 4, NODE_W - 12, h - 8), _wrap_flags, self._label)
        else:
            # --- Full LOD ---
            # Sub-label text uses black on colored fills for readability
            sub_label_color = Qt.GlobalColor.black if self._active else THEME.qcolor("node_sublabel_inactive")

            if self._returns:
                # ----------------------------------------------------------
                # Header: label + "state_id · node_type" crammed into _HEADER_H pixels
                # ----------------------------------------------------------
                painter.setFont(THEME.font_node_hdr())
                fm = painter.fontMetrics()
                label_text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.setPen(Qt.GlobalColor.black)
                painter.drawText(QRectF(8, 1, NODE_W - 16, 14), Qt.AlignmentFlag.AlignCenter, label_text)

                sub_text = f"{self.state_id}  ·  {self._node_type}" if self._node_type else self.state_id
                painter.setPen(sub_label_color)
                painter.setFont(THEME.font_node_sub())
                fm2 = painter.fontMetrics()
                sub_elided = fm2.elidedText(sub_text, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.drawText(QRectF(8, 15, NODE_W - 16, 12), Qt.AlignmentFlag.AlignCenter, sub_elided)

                # Divider line between header and port area
                painter.setPen(QPen(border, 0.5))
                painter.drawLine(QPointF(4, _HEADER_H), QPointF(NODE_W - 4, _HEADER_H))

                # Named output ports + labels
                for i, name in enumerate(self._returns):
                    py = self._port_y(i)
                    painter.setPen(THEME.qcolor("port_label_color"))
                    painter.setFont(THEME.font_node_sub())
                    label_rect = QRectF(4, py - 8, NODE_W - PORT_R * 2 - 10, 16)
                    painter.drawText(
                        label_rect,
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        name,
                    )
                    if self._interactive:
                        painter.setPen(QPen(_PORT_COLOR, 1.5))
                        painter.setBrush(QBrush(_PORT_COLOR))
                        painter.drawEllipse(QPointF(NODE_W, py), PORT_R, PORT_R)
            else:
                # ----------------------------------------------------------
                # 2-line layout: main label + "state_id · node_type" sub-label
                # ----------------------------------------------------------
                painter.setFont(THEME.font_node_main())
                fm = painter.fontMetrics()
                label_text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.setPen(Qt.GlobalColor.black)
                painter.drawText(QRectF(8, 6, NODE_W - 16, 26), Qt.AlignmentFlag.AlignCenter, label_text)

                sub_text = f"{self.state_id}  ·  {self._node_type}" if self._node_type else self.state_id
                painter.setPen(sub_label_color)
                painter.setFont(THEME.font_node_sub())
                fm2 = painter.fontMetrics()
                sub_elided = fm2.elidedText(sub_text, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.drawText(QRectF(8, 34, NODE_W - 16, 14), Qt.AlignmentFlag.AlignCenter, sub_elided)

                if self._interactive:
                    painter.setPen(QPen(_PORT_COLOR, 1.5))
                    painter.setBrush(QBrush(_PORT_COLOR))
                    painter.drawEllipse(QPointF(NODE_W, h / 2), PORT_R, PORT_R)

            # Structural-role badges drawn at bottom center.
            # When both are present they sit side-by-side; when only one, it is centred.
            _BADGE_SIZE = 14
            _BADGE_MARGIN = 4
            _BADGE_GAP = 4
            if self._is_initial or self._is_terminal:
                both = self._is_initial and self._is_terminal
                badge_y = h - _BADGE_SIZE - _BADGE_MARGIN
                if both:
                    init_x = NODE_W / 2 - _BADGE_SIZE - _BADGE_GAP / 2
                    term_x = NODE_W / 2 + _BADGE_GAP / 2
                else:
                    init_x = term_x = NODE_W / 2 - _BADGE_SIZE / 2

                painter.setFont(_BADGE_FONT)
                if self._is_initial:
                    r = QRectF(init_x, badge_y, _BADGE_SIZE, _BADGE_SIZE)
                    painter.save()
                    painter.setPen(QPen(Qt.GlobalColor.black, 1.0))
                    painter.setBrush(QBrush(_BADGE_INITIAL))
                    painter.drawRoundedRect(r, 3, 3)
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "▶")
                    painter.restore()
                if self._is_terminal:
                    r = QRectF(term_x, badge_y, _BADGE_SIZE, _BADGE_SIZE)
                    painter.save()
                    painter.setPen(QPen(Qt.GlobalColor.black, 1.0))
                    painter.setBrush(QBrush(_BADGE_TERMINAL))
                    painter.drawRoundedRect(r, 3, 3)
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "■")
                    painter.restore()

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
    ):
        super().__init__()
        self._src = src_node
        self._dst = dst_node
        self._offset = offset
        self._when = when
        self._is_self_loop = src_node is dst_node
        self._hovered = False

        self.setZValue(0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
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
            pen = QPen(_EDGE_SEL_COLOR, 2.5, Qt.PenStyle.DashLine)
        elif self._hovered:
            pen = QPen(_EDGE_HOVER_COLOR, 2.5)
        else:
            pen = QPen(_EDGE_COLOR, 1.5)
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
        if dst_pt.x() > src_pt.x():
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

    def _build_backward_ortho(self, path: QPainterPath, src_pt: QPointF, dst_pt: QPointF) -> None:
        """Backward (right-to-left) edge routed as a 6-point Z-shape.

        The horizontal bridge sits at the midpoint of the two port y-values.
        When the ports are close enough vertically that the bridge would clip a
        node (within one node height of each other), mid_y is snapped to just
        above or below both nodes instead.
        """
        mid_y = (src_pt.y() + dst_pt.y()) / 2 + self._offset * 0.5

        src_h = self._src._height
        dst_h = self._dst._height

        # Only clamp when ports are close — when they are far apart the midpoint
        # naturally lands between the nodes and no adjustment is needed.
        if abs(dst_pt.y() - src_pt.y()) < max(src_h, dst_h):
            above_limit = min(src_pt.y() - src_h / 2, dst_pt.y() - dst_h / 2) - _BACK_PADDING
            below_limit = max(src_pt.y() + src_h / 2, dst_pt.y() + dst_h / 2) + _BACK_PADDING
            if mid_y - above_limit > below_limit - mid_y:
                mid_y = above_limit
            else:
                mid_y = below_limit

        waypoints = [
            src_pt,
            QPointF(src_pt.x() + _BACK_H_STEP, src_pt.y()),
            QPointF(src_pt.x() + _BACK_H_STEP, mid_y),
            QPointF(dst_pt.x() - _BACK_H_STEP, mid_y),
            QPointF(dst_pt.x() - _BACK_H_STEP, dst_pt.y()),
            dst_pt,
        ]
        _draw_ortho_path(path, waypoints)

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
        nxt  = waypoints[i + 1]

        d1 = math.hypot(curr.x() - prev.x(), curr.y() - prev.y())
        d2 = math.hypot(nxt.x()  - curr.x(), nxt.y()  - curr.y())
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


# ---------------------------------------------------------------------------
# _MinimapView — thumbnail overview of the full scene
# ---------------------------------------------------------------------------

class _MinimapView(QGraphicsView):
    """Small read-only thumbnail of the EditScene, shown as an overlay.

    Renders the same QGraphicsScene as the main view.  Clicking or dragging
    inside the minimap pans the main view to that scene position.  A
    translucent rectangle is drawn over the minimap to indicate the portion
    of the scene currently visible in the main view.
    """

    _W = 160
    _H = 110

    def __init__(self, scene: QGraphicsScene, main_view: GraphView, parent=None):
        super().__init__(scene, parent)
        self._main_view = main_view

        self.setFixedSize(self._W, self._H)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setInteractive(False)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setStyleSheet(
            f"QGraphicsView {{"
            f"  border: 1px solid {THEME.minimap_border};"
            f"  border-radius: 4px;"
            f"  background: {THEME.minimap_bg};"
            f"}}"
        )

        # Repaint viewport rect whenever main view scrolls or zooms
        main_view.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        main_view.verticalScrollBar().valueChanged.connect(self.viewport().update)
        main_view.view_changed.connect(self.viewport().update)

    def fit_to_scene(self) -> None:
        """Fit the minimap to show the full scene bounds."""
        bounds = self.scene().itemsBoundingRect()
        if not bounds.isEmpty():
            self.fitInView(
                bounds.adjusted(-60, -60, 60, 60),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # Draw the main view's visible scene rect as a translucent overlay
        visible_scene = self._main_view.mapToScene(
            self._main_view.viewport().rect()
        ).boundingRect()
        mini_rect = self.mapFromScene(visible_scene).boundingRect()

        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(60, 120, 210, 200), 1.5))
        painter.setBrush(QBrush(QColor(60, 120, 210, 40)))
        painter.drawRect(mini_rect)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._main_view.centerOn(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._main_view.centerOn(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseMoveEvent(event)
