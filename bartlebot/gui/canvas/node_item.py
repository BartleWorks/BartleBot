"""NodeItem — QGraphicsItem representing a single FSM state."""

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSequentialAnimationGroup, Qt, QVariantAnimation
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QStyleOptionGraphicsItem,
)

from bartlebot.gui.theme import THEME

# ---------------------------------------------------------------------------
# Geometry constants (also used by EdgeItem for port positions)
# ---------------------------------------------------------------------------

NODE_W = 220  # width of the left info section
NODE_H = 70  # node height — always fixed regardless of return count
NODE_RADIUS = 12
PORT_R = 6  # radius of connection-port circles
_PORT_HIT_R = 16  # hit-test radius for port cursor feedback
_RIGHT_PAD = 16  # horizontal padding inside the right returns section
_BADGE_R = 8  # radius of corner-overlap role badges (initial / terminal / flipped)
_BADGE_GAP = 4  # gap between adjacent badge circles
_BADGE_FONT_PT = 6  # point size for text glyphs drawn inside badges
_BADGE_ICON_PAD = 3  # inset padding when rendering SVG icons inside badge circles

_PLAY_RENDERER: QSvgRenderer | None = None
_STOP_RENDERER: QSvgRenderer | None = None
_PLAY_REVERSED_RENDERER: QSvgRenderer | None = None


def _play_badge_renderer() -> QSvgRenderer:
    """Lazily parse play.svg with white fill. Cached after first call; renders as vector."""
    global _PLAY_RENDERER
    if _PLAY_RENDERER is None:
        from pathlib import Path

        from PyQt6.QtCore import QByteArray

        svg_path = Path(__file__).parent.parent / "icons" / "play.svg"
        svg_bytes = svg_path.read_bytes().replace(b'fill="#404040"', b'fill="#ffffff"')
        _PLAY_RENDERER = QSvgRenderer(QByteArray(svg_bytes))
    return _PLAY_RENDERER


def _stop_badge_renderer() -> QSvgRenderer:
    """Lazily parse stop.svg with white fill. Cached after first call; renders as vector."""
    global _STOP_RENDERER
    if _STOP_RENDERER is None:
        from pathlib import Path

        from PyQt6.QtCore import QByteArray

        svg_path = Path(__file__).parent.parent / "icons" / "stop.svg"
        svg_bytes = svg_path.read_bytes().replace(b'fill="#404040"', b'fill="#ffffff"')
        _STOP_RENDERER = QSvgRenderer(QByteArray(svg_bytes))
    return _STOP_RENDERER


def _play_reversed_badge_renderer() -> QSvgRenderer:
    """Lazily build a horizontally-mirrored play triangle (points left). Cached."""
    global _PLAY_REVERSED_RENDERER
    if _PLAY_REVERSED_RENDERER is None:
        from PyQt6.QtCore import QByteArray

        # play.svg polygon "5,3 21,12 5,21" mirrored around x=12 → "19,3 3,12 19,21"
        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><polygon points="19,3 3,12 19,21" fill="#ffffff"/></svg>'
        _PLAY_REVERSED_RENDERER = QSvgRenderer(QByteArray(svg_bytes))
    return _PLAY_REVERSED_RENDERER


# Grid constants — shared with EditScene for drawBackground and migration script.
# Padding values define the gap between adjacent node edges on the grid.
NODE_GRID_PAD_W = 140  # horizontal gap between adjacent node right/left edges
NODE_GRID_PAD_H = 20  # vertical gap between adjacent node bottom/top edges
GRID_W = NODE_W + NODE_GRID_PAD_W  # = 360
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


def node_height(_n_returns: int) -> int:
    """Node height — always NODE_H regardless of return count."""
    return NODE_H


# ---------------------------------------------------------------------------
# NodeItem
# ---------------------------------------------------------------------------


class NodeItem(QGraphicsItem):
    """Rounded-rect node showing a state's label.

    Layout: a fixed-height left info section (NODE_W wide) containing the
    label and state_id/node_type, plus an optional right section containing
    named output port labels. The right section only takes the minimum width
    needed to display the longest return label.

    For flipped nodes the port section extends to the LEFT of the info
    section (into negative local x), so the scenePos anchor always refers
    to the top-left of the info section.
    """

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
        self._right_w: int = self._compute_right_w()
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

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _compute_right_w(self) -> int:
        """Minimum width for the right port-label section; 0 when no returns."""
        if not self._returns:
            return 0
        fm = QFontMetrics(THEME.font_node_sub())
        max_label_w = max(fm.horizontalAdvance(r) for r in self._returns)
        return max_label_w + PORT_R * 2 + _RIGHT_PAD

    @property
    def _total_w(self) -> int:
        """Total node width: info section + port section."""
        return NODE_W + self._right_w

    @property
    def _height(self) -> int:
        return NODE_H

    def _node_rect(self) -> QRectF:
        """Local bounding rect of the node body (excluding margin)."""
        if self._flipped and self._right_w:
            return QRectF(-self._right_w, 0, self._total_w, NODE_H)
        return QRectF(0, 0, self._total_w, NODE_H)

    def boundingRect(self) -> QRectF:
        margin = PORT_R + 1 if self._interactive else 0
        if self._is_search_match:
            margin = max(margin, 8)
        nr = self._node_rect()
        top_extra = _BADGE_R if (self._is_initial or self._is_terminal or self._flipped) else 0
        return QRectF(nr.x() - margin, -(margin + top_extra), nr.width() + margin * 2, NODE_H + margin * 2 + top_extra)

    # ------------------------------------------------------------------
    # State setters
    # ------------------------------------------------------------------

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
        nr = self._node_rect()
        return self.scenePos() + QPointF(nr.x() + nr.width() / 2, NODE_H / 2)

    # ------------------------------------------------------------------
    # Port positions
    # ------------------------------------------------------------------

    def port_in(self) -> QPointF:
        """Scene-coordinate input port.

        Non-flipped: left edge of info section (x=0).
        Flipped: right edge of info section (x=NODE_W).
        """
        x = NODE_W if self._flipped else 0
        return self.scenePos() + QPointF(x, NODE_H / 2)

    def port_out(self) -> QPointF:
        """Scene-coordinate output port (center-edge fallback when no named returns).

        Non-flipped: right edge of node (x=_total_w).
        Flipped: left edge of node (x=-_right_w, which is 0 when no returns).
        """
        if self._flipped:
            x = -self._right_w
        else:
            x = self._total_w
        return self.scenePos() + QPointF(x, NODE_H / 2)

    def _port_y(self, idx: int) -> float:
        """Y coordinate (local) for the idx-th named output port, evenly distributed over NODE_H."""
        n = len(self._returns)
        return (idx + 0.5) * NODE_H / n

    def port_out_for(self, return_name: str | None) -> QPointF:
        """Scene-coordinate output port for the given return value name.

        Falls back to the center-edge port when *return_name* is None or
        not found in this node's returns list.
        """
        if return_name and return_name in self._returns:
            idx = self._returns.index(return_name)
            if self._flipped:
                x = -self._right_w
            else:
                x = self._total_w
            return self.scenePos() + QPointF(x, self._port_y(idx))
        return self.port_out()

    # ------------------------------------------------------------------
    # Public mutators
    # ------------------------------------------------------------------

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
        self._right_w = self._compute_right_w()
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
        if self._is_initial == is_initial:
            return
        self.prepareGeometryChange()
        self._is_initial = is_initial
        self.update()

    def set_terminal(self, is_terminal: bool) -> None:
        """Mark this node as a terminal state with no exit transitions (draws a ■ badge)."""
        if self._is_terminal == is_terminal:
            return
        self.prepareGeometryChange()
        self._is_terminal = is_terminal
        self.update()

    def set_flipped(self, flipped: bool) -> None:
        """Swap input/output port sides: input moves to right, outputs to left."""
        self._flipped = flipped
        self.prepareGeometryChange()
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
            # Snap the left edge of the node body and the vertical center to grid positions.
            # For flipped nodes the item origin is not the left edge — it's offset by _right_w.
            left_offset = self._right_w if self._flipped else 0
            snapped_x = round((value.x() - left_offset) / GRID_W) * GRID_W + left_offset
            return QPointF(
                snapped_x,
                round((value.y() + NODE_H / 2) / GRID_H) * GRID_H - NODE_H / 2,
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
        near_port = False
        if self._returns:
            port_x = -self._right_w if self._flipped else self._total_w
            for i in range(len(self._returns)):
                if math.hypot(pos.x() - port_x, pos.y() - self._port_y(i)) <= _PORT_HIT_R:
                    near_port = True
                    break
        else:
            port_x = 0.0 if self._flipped else float(NODE_W)
            if math.hypot(pos.x() - port_x, pos.y() - NODE_H / 2) <= _PORT_HIT_R:
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

        nr = self._node_rect()
        lod = option.levelOfDetailFromTransform(painter.worldTransform())

        # Search match ring — drawn first as a solid filled rect so it sits
        # behind the node background with no gap.
        if self._is_search_match:
            ring_color = (
                THEME.qcolor("search_match_current") if self._is_search_current else THEME.qcolor("search_match_other")
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ring_color))
            painter.drawRoundedRect(
                QRectF(nr.x() - 7, -7, nr.width() + 14, NODE_H + 14),
                NODE_RADIUS + 5,
                NODE_RADIUS + 5,
            )

        painter.setPen(QPen(border, border_w))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(nr, NODE_RADIUS, NODE_RADIUS)

        # --- Compact LOD: label-only in info section when zoomed out ---
        from bartlebot.settings_manager import SettingsManager

        _lod_threshold = SettingsManager.instance().get("canvas", "lod_compact_threshold", default=0.6)
        if lod < _lod_threshold:
            compact_font = QFont(THEME.font_ui_family, 14)
            painter.setFont(compact_font)
            painter.setPen(THEME.qcolor("text"))
            _wrap_flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter) | int(
                Qt.TextFlag.TextWordWrap
            )
            painter.drawText(QRectF(nr.x() + 6, 4, nr.width() - 12, NODE_H - 8), _wrap_flags, self._label)
            badges: list[tuple[QColor, QSvgRenderer | str]] = []
            if self._is_initial:
                badges.append((_badge_initial(), _play_badge_renderer()))
            if self._is_terminal:
                badges.append((_badge_terminal(), _stop_badge_renderer()))
            if self._flipped:
                badges.append((_badge_flipped(), _play_reversed_badge_renderer()))
            for i, (color, icon) in enumerate(badges):
                cx = float(i * (_BADGE_R * 2 + _BADGE_GAP))
                badge_rect = QRectF(cx - _BADGE_R, -_BADGE_R, _BADGE_R * 2, _BADGE_R * 2)
                icon_rect = badge_rect.adjusted(_BADGE_ICON_PAD, _BADGE_ICON_PAD, -_BADGE_ICON_PAD, -_BADGE_ICON_PAD)
                painter.save()
                painter.setPen(QPen(color.darker(140), 1.0))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(cx, 0.0), _BADGE_R, _BADGE_R)
                if isinstance(icon, QSvgRenderer):
                    icon.render(painter, icon_rect)
                else:
                    painter.setFont(QFont(THEME.font_ui_family, _BADGE_FONT_PT))
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, icon)
                painter.restore()
        else:
            # --- Full LOD ---
            sub_label_color = QColor(THEME.text) if self._active else THEME.qcolor("text_muted")

            # Info section: 2-line label layout in [0..NODE_W], always at full NODE_H height
            painter.setFont(THEME.font_node_main())
            fm = painter.fontMetrics()
            label_text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, NODE_W - 16)
            painter.setPen(THEME.qcolor("text"))
            painter.drawText(QRectF(8, 5, NODE_W - 16, 30), Qt.AlignmentFlag.AlignCenter, label_text)

            sub_text = self._node_type
            painter.setPen(sub_label_color)
            painter.setFont(THEME.font_node_sub())
            fm2 = painter.fontMetrics()
            sub_elided = fm2.elidedText(sub_text, Qt.TextElideMode.ElideRight, NODE_W - 16)
            painter.drawText(QRectF(8, 37, NODE_W - 16, 17), Qt.AlignmentFlag.AlignCenter, sub_elided)

            if self._returns:
                # Port section: to the right of info for non-flipped, to the left for flipped.
                n = len(self._returns)
                row_h = NODE_H / n

                if self._flipped:
                    sect_left = float(-self._right_w)
                    dot_x = float(-self._right_w)
                    div_x = 0.0
                else:
                    sect_left = float(NODE_W)
                    dot_x = float(self._total_w)
                    div_x = float(NODE_W)

                # Vertical divider between info and port sections
                painter.setPen(QPen(border, 0.5))
                painter.drawLine(QPointF(div_x, 4), QPointF(div_x, NODE_H - 4))

                # Port labels and dots
                for i, name in enumerate(self._returns):
                    py = self._port_y(i)
                    label_rect = QRectF(
                        sect_left + PORT_R + 4,
                        py - row_h / 2,
                        self._right_w - PORT_R * 2 - 8,
                        row_h,
                    )
                    painter.setPen(THEME.qcolor("text_subtle"))
                    painter.setFont(THEME.font_node_sub())
                    painter.drawText(
                        label_rect,
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        name,
                    )
                    if self._interactive:
                        painter.setPen(QPen(_port_color(), 1.5))
                        painter.setBrush(QBrush(_port_color()))
                        painter.drawEllipse(QPointF(dot_x, py), PORT_R, PORT_R)
            else:
                # No named returns: single output port at the center edge of the info section
                if self._interactive:
                    port_x = 0.0 if self._flipped else float(NODE_W)
                    painter.setPen(QPen(_port_color(), 1.5))
                    painter.setBrush(QBrush(_port_color()))
                    painter.drawEllipse(QPointF(port_x, NODE_H / 2), PORT_R, PORT_R)

            # Structural-role badges: small circles overlapping the top-left border
            badges: list[tuple[QColor, QSvgRenderer | str]] = []
            if self._is_initial:
                badges.append((_badge_initial(), _play_badge_renderer()))
            if self._is_terminal:
                badges.append((_badge_terminal(), _stop_badge_renderer()))
            if self._flipped:
                badges.append((_badge_flipped(), _play_reversed_badge_renderer()))
            for i, (color, icon) in enumerate(badges):
                cx = float(i * (_BADGE_R * 2 + _BADGE_GAP))
                badge_rect = QRectF(cx - _BADGE_R, -_BADGE_R, _BADGE_R * 2, _BADGE_R * 2)
                icon_rect = badge_rect.adjusted(_BADGE_ICON_PAD, _BADGE_ICON_PAD, -_BADGE_ICON_PAD, -_BADGE_ICON_PAD)
                painter.save()
                painter.setPen(QPen(color.darker(140), 1.0))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(cx, 0.0), _BADGE_R, _BADGE_R)
                if isinstance(icon, QSvgRenderer):
                    icon.render(painter, icon_rect)
                else:
                    painter.setFont(QFont(THEME.font_ui_family, _BADGE_FONT_PT))
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, icon)
                painter.restore()

            # Input port dot (opposite side from outputs)
            if self._interactive:
                in_x = float(NODE_W) if self._flipped else 0.0
                painter.setPen(QPen(_port_color(), 1.5))
                painter.setBrush(QBrush(_port_color()))
                painter.drawEllipse(QPointF(in_x, NODE_H / 2), PORT_R, PORT_R)
