"""NodeItem — QGraphicsItem representing a single FSM state."""

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSequentialAnimationGroup, Qt, QVariantAnimation
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QStyleOptionGraphicsItem,
)

from bartlebot.gui.theme import THEME

# ---------------------------------------------------------------------------
# Geometry constants (also used by EdgeItem for port positions)
# ---------------------------------------------------------------------------

NODE_W = 220
NODE_H = 70
NODE_RADIUS = 12
PORT_R = 6  # radius of connection-port circles
PORT_SPACING = 28  # vertical spacing between named output ports
_HEADER_H = 28  # height reserved for text label when returns are present
_PORT_HIT_R = 16  # hit-test radius for port cursor feedback

# Grid constants — shared with EditScene for drawBackground and migration script.
# Padding values define the gap between adjacent node edges on the grid.
NODE_GRID_PAD_W = 80  # horizontal gap between adjacent node right/left edges
NODE_GRID_PAD_H = 20  # vertical gap between adjacent node bottom/top edges
GRID_W = NODE_W + NODE_GRID_PAD_W  # = 300
GRID_H = NODE_H + NODE_GRID_PAD_H  # = 90

# ---------------------------------------------------------------------------
# Color helpers — read from THEME at paint time so theme switches take effect
# ---------------------------------------------------------------------------


def _fill_normal() -> QColor:
    return THEME.qcolor("node_fill")


def _fill_hover() -> QColor:
    return THEME.qcolor("node_fill_hover")


def _fill_active() -> QColor:
    return THEME.qcolor("node_fill_active")


def _fill_active_dim() -> QColor:
    return THEME.qcolor("node_fill_active_dim")


def _fill_selected() -> QColor:
    return THEME.qcolor("node_fill_selected")


def _fill_error() -> QColor:
    return THEME.qcolor("node_fill_error")


def _fill_error_hover() -> QColor:
    return THEME.qcolor("node_fill_error_hover")


def _fill_warning() -> QColor:
    return THEME.qcolor("node_fill_warning")


def _fill_warning_hover() -> QColor:
    return THEME.qcolor("node_fill_warning_hover")


def _border_normal() -> QColor:
    return THEME.qcolor("node_border")


def _border_active() -> QColor:
    return THEME.qcolor("node_border_active")


def _border_selected() -> QColor:
    return THEME.qcolor("accent")


def _border_error() -> QColor:
    return THEME.qcolor("error")


def _border_warning() -> QColor:
    return THEME.qcolor("warning")


def _badge_initial() -> QColor:
    return THEME.qcolor("success")


def _badge_terminal() -> QColor:
    return THEME.qcolor("error")


def _badge_flipped() -> QColor:
    return THEME.qcolor("warning")


def _port_color() -> QColor:
    return THEME.qcolor("port_color")


def node_height(n_returns: int) -> int:
    """Compute the pixel height for a node with *n_returns* named output ports."""
    return max(NODE_H, (max(n_returns, 1) + 1) * PORT_SPACING)


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
        flipped: bool = False,
    ):
        super().__init__()
        self.state_id = state_id
        self._label = label or state_id
        self._node_type = node_type
        self._active = False
        self._active_fill: QColor = _fill_active()
        self._hovered = False

        _anim_fwd = QVariantAnimation()
        _anim_fwd.setStartValue(_fill_active())
        _anim_fwd.setEndValue(_fill_active_dim())
        _anim_fwd.setDuration(800)
        _anim_fwd.setEasingCurve(QEasingCurve.Type.InOutSine)
        _anim_fwd.valueChanged.connect(self._on_pulse_value)

        _anim_rev = QVariantAnimation()
        _anim_rev.setStartValue(_fill_active_dim())
        _anim_rev.setEndValue(_fill_active())
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
        self._flipped: bool = flipped
        self._returns: list[str] = list(returns) if returns else []
        self._edges: list = []
        self._is_search_match: bool = False
        self._is_search_current: bool = False
        self._snap_enabled: bool = False
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
            self._active_fill = _fill_active()
            self._pulse_group.start()
        else:
            self._pulse_group.stop()
            self._active_fill = _fill_active()
        self.update()

    def _on_pulse_value(self, color: QColor) -> None:
        self._active_fill = color
        self.update()

    def center(self) -> QPointF:
        """Scene-coordinate center of this node."""
        return self.scenePos() + QPointF(NODE_W / 2, self._height / 2)

    def port_in(self) -> QPointF:
        """Scene-coordinate input port (left center normally, right center when flipped)."""
        x = NODE_W if self._flipped else 0
        return self.scenePos() + QPointF(x, self._height / 2)

    def port_out(self) -> QPointF:
        """Scene-coordinate output port (right center normally, left center when flipped)."""
        x = 0 if self._flipped else NODE_W
        return self.scenePos() + QPointF(x, self._height / 2)

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
            x = 0 if self._flipped else NODE_W
            return self.scenePos() + QPointF(x, self._port_y(idx))
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

    def set_flipped(self, flipped: bool) -> None:
        """Swap input/output port sides: input moves to right, outputs to left."""
        self._flipped = flipped
        for edge in self._edges:
            edge._rebuild()
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

    def set_snap_enabled(self, enabled: bool) -> None:
        """Enable or disable grid snapping for this node."""
        self._snap_enabled = enabled

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._snap_enabled:
            # Round the top-left position to the nearest grid cell.
            # Using ItemPositionChange (not HasChanged) means we return the modified
            # value directly — Qt applies it without re-triggering this handler.
            return QPointF(
                round(value.x() / GRID_W) * GRID_W,
                round(value.y() / GRID_H) * GRID_H,
            )
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
        port_x = 0 if self._flipped else NODE_W
        near_port = False
        if self._returns:
            for i in range(len(self._returns)):
                if math.hypot(pos.x() - port_x, pos.y() - self._port_y(i)) <= _PORT_HIT_R:
                    near_port = True
                    break
        else:
            if math.hypot(pos.x() - port_x, pos.y() - h / 2) <= _PORT_HIT_R:
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
            border, border_w = _border_active(), 2.5
        elif selected:
            fill, border, border_w = _fill_selected(), _border_selected(), 2.5
        elif self._is_error:
            fill = _fill_error_hover() if self._hovered else _fill_error()
            border, border_w = _border_error(), 2.5
        elif self._is_warning:
            fill = _fill_warning_hover() if self._hovered else _fill_warning()
            border, border_w = _border_warning(), 2.5
        elif self._hovered:
            fill, border, border_w = _fill_hover(), _border_normal(), 2.0
        else:
            fill, border, border_w = _fill_normal(), _border_normal(), 1.5

        h = self._height
        lod = option.levelOfDetailFromTransform(painter.worldTransform())

        # Search match ring — drawn first as a solid filled rect so it sits
        # behind the node background with no gap.
        if self._is_search_match:
            ring_color = (
                THEME.qcolor("search_match_current") if self._is_search_current else THEME.qcolor("search_match_other")
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ring_color))
            painter.drawRoundedRect(QRectF(-7, -7, NODE_W + 14, h + 14), NODE_RADIUS + 5, NODE_RADIUS + 5)

        painter.setPen(QPen(border, border_w))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(QRectF(0, 0, NODE_W, h), NODE_RADIUS, NODE_RADIUS)

        # --- Compact LOD: label-only with word-wrap when zoomed out ---
        from bartlebot.settings_manager import SettingsManager

        _lod_threshold = SettingsManager.instance().get("canvas", "lod_compact_threshold", default=0.6)
        if lod < _lod_threshold:
            compact_font = QFont(THEME.font_ui_family, 14)
            painter.setFont(compact_font)
            painter.setPen(THEME.qcolor("text"))
            _wrap_flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter) | int(
                Qt.TextFlag.TextWordWrap
            )
            painter.drawText(QRectF(6, 4, NODE_W - 12, h - 8), _wrap_flags, self._label)
            _BADGE_SIZE = 14
            _BADGE_MARGIN = 4
            _BADGE_GAP = 4
            badges = []
            if self._is_initial:
                badges.append((_badge_initial(), "▶"))
            if self._is_terminal:
                badges.append((_badge_terminal(), "■"))
            if self._flipped:
                badges.append((_badge_flipped(), "◀"))
            if badges:
                n = len(badges)
                total_w = n * _BADGE_SIZE + (n - 1) * _BADGE_GAP
                x_start = (NODE_W - total_w) / 2
                badge_y = h - _BADGE_SIZE - _BADGE_MARGIN
                painter.setFont(THEME.font_node_sub())
                for i, (color, icon) in enumerate(badges):
                    bx = x_start + i * (_BADGE_SIZE + _BADGE_GAP)
                    r = QRectF(bx, badge_y, _BADGE_SIZE, _BADGE_SIZE)
                    painter.save()
                    painter.setPen(QPen(Qt.GlobalColor.black, 1.0))
                    painter.setBrush(QBrush(color))
                    painter.drawRoundedRect(r, 3, 3)
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, icon)
                    painter.restore()
        else:
            # --- Full LOD ---
            # Sub-label text uses black on colored fills for readability
            sub_label_color = QColor(THEME.text) if self._active else THEME.qcolor("text_muted")

            if self._returns:
                # ----------------------------------------------------------
                # Header: label + "state_id · node_type" crammed into _HEADER_H pixels
                # ----------------------------------------------------------
                painter.setFont(THEME.font_node_hdr())
                fm = painter.fontMetrics()
                label_text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.setPen(THEME.qcolor("text"))
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
                port_x = 0 if self._flipped else NODE_W
                for i, name in enumerate(self._returns):
                    py = self._port_y(i)
                    painter.setPen(THEME.qcolor("text_subtle"))
                    painter.setFont(THEME.font_node_sub())
                    if self._flipped:
                        label_rect = QRectF(PORT_R * 2 + 6, py - 8, NODE_W - PORT_R * 2 - 10, 16)
                        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    else:
                        label_rect = QRectF(4, py - 8, NODE_W - PORT_R * 2 - 10, 16)
                        align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    painter.drawText(label_rect, align, name)
                    if self._interactive:
                        painter.setPen(QPen(_port_color(), 1.5))
                        painter.setBrush(QBrush(_port_color()))
                        painter.drawEllipse(QPointF(port_x, py), PORT_R, PORT_R)
            else:
                # ----------------------------------------------------------
                # 2-line layout: main label + "state_id · node_type" sub-label
                # ----------------------------------------------------------
                painter.setFont(THEME.font_node_main())
                fm = painter.fontMetrics()
                label_text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.setPen(THEME.qcolor("text"))
                painter.drawText(QRectF(8, 6, NODE_W - 16, 26), Qt.AlignmentFlag.AlignCenter, label_text)

                sub_text = f"{self.state_id}  ·  {self._node_type}" if self._node_type else self.state_id
                painter.setPen(sub_label_color)
                painter.setFont(THEME.font_node_sub())
                fm2 = painter.fontMetrics()
                sub_elided = fm2.elidedText(sub_text, Qt.TextElideMode.ElideRight, NODE_W - 16)
                painter.drawText(QRectF(8, 34, NODE_W - 16, 14), Qt.AlignmentFlag.AlignCenter, sub_elided)

                if self._interactive:
                    port_x = 0 if self._flipped else NODE_W
                    painter.setPen(QPen(_port_color(), 1.5))
                    painter.setBrush(QBrush(_port_color()))
                    painter.drawEllipse(QPointF(port_x, h / 2), PORT_R, PORT_R)

            # Structural-role badges drawn at bottom center, auto-spaced for 1–3 badges.
            _BADGE_SIZE = 14
            _BADGE_MARGIN = 4
            _BADGE_GAP = 4
            badges = []
            if self._is_initial:
                badges.append((_badge_initial(), "▶"))
            if self._is_terminal:
                badges.append((_badge_terminal(), "■"))
            if self._flipped:
                badges.append((_badge_flipped(), "◀"))
            if badges:
                n = len(badges)
                total_w = n * _BADGE_SIZE + (n - 1) * _BADGE_GAP
                x_start = (NODE_W - total_w) / 2
                badge_y = h - _BADGE_SIZE - _BADGE_MARGIN
                painter.setFont(THEME.font_node_sub())
                for i, (color, icon) in enumerate(badges):
                    bx = x_start + i * (_BADGE_SIZE + _BADGE_GAP)
                    r = QRectF(bx, badge_y, _BADGE_SIZE, _BADGE_SIZE)
                    painter.save()
                    painter.setPen(QPen(Qt.GlobalColor.black, 1.0))
                    painter.setBrush(QBrush(color))
                    painter.drawRoundedRect(r, 3, 3)
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, icon)
                    painter.restore()

            # Input port (right when flipped, left otherwise)
            if self._interactive:
                in_x = NODE_W if self._flipped else 0
                painter.setPen(QPen(_port_color(), 1.5))
                painter.setBrush(QBrush(_port_color()))
                painter.drawEllipse(QPointF(in_x, h / 2), PORT_R, PORT_R)
