"""NodeItem — QGraphicsItem representing a single FSM state."""

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSequentialAnimationGroup, Qt, QVariantAnimation
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QStyleOptionGraphicsItem,
)

from bartlebot.gui.canvas.lod import text_is_legible
from bartlebot.gui.theme import THEME

# ---------------------------------------------------------------------------
# Geometry constants (also used by EdgeItem for port positions)
# ---------------------------------------------------------------------------

NODE_INFO_W = 220  # width of the left info section
NODE_PORT_W = 80  # width of the right port section (fixed; return labels elided to fit)
NODE_W = NODE_INFO_W + NODE_PORT_W  # = 300, full node width
NODE_H = 70  # node height — always fixed regardless of return count
NODE_RADIUS = 12
PORT_R = 6  # radius of connection-port circles
_PORT_HIT_R = 16  # hit-test radius for port cursor feedback
_BADGE_R = 8  # radius of corner-overlap role badges (initial / terminal)
_BADGE_GAP = 4  # gap between adjacent badge circles
_BADGE_ICON_PAD = 3  # inset padding when rendering SVG icons inside badge circles

# What a port with no edge on it does anyway. A marked port is drawn as a filled
# badge in place of the plain dot, reusing the node-badge color and glyph of
# whatever it stands for, so the two ends of an undrawn route carry one symbol.
PORT_MARK_RECOVERY = "recovery"
PORT_MARK_STOP = "stop"
_PORT_MARK_ICON_PAD = 2  # inset when rendering a glyph inside a port-sized badge

_PLAY_RENDERER: QSvgRenderer | None = None
_STOP_RENDERER: QSvgRenderer | None = None
_RECOVERY_RENDERER: QSvgRenderer | None = None


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


def _recovery_badge_renderer() -> QSvgRenderer:
    """Lazily build a refresh-arrow SVG with white fill for the recovery badge.

    White fill is safe: both light and dark themes define warning="#9a6c00" (dark amber),
    which provides >=4.5:1 contrast against #ffffff (WCAG AA). This matches the pattern
    used by the initial/terminal badge renderers, which also bake white fill at init time.
    """
    global _RECOVERY_RENDERER
    if _RECOVERY_RENDERER is None:
        from PyQt6.QtCore import QByteArray

        svg_bytes = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            b'<path d="M17.65 6.35A7.96 7.96 0 0 0 12 4C7.58 4 4 7.58 4 12s3.58 8 8 8'
            b' 8-3.58 8-8h-2a6 6 0 1 1-1.76-4.24L14 10h6V4l-2.35 2.35z" fill="#ffffff"/>'
            b"</svg>"
        )
        _RECOVERY_RENDERER = QSvgRenderer(QByteArray(svg_bytes))
    return _RECOVERY_RENDERER


# Grid constants — shared with EditScene for drawBackground and migration script.
# Padding values define the gap between adjacent node edges on the grid.
NODE_GRID_PAD_W = 140  # horizontal gap between adjacent node right/left edges
NODE_GRID_PAD_H = 20  # vertical gap between adjacent node bottom/top edges
GRID_W = NODE_W + NODE_GRID_PAD_W  # = 440
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


def _badge_recovery() -> QColor:
    return THEME.qcolor("warning")


def _port_color() -> QColor:
    return THEME.qcolor("port_color")


def node_height(_n_returns: int) -> int:
    """Node height — always NODE_H regardless of return count."""
    return NODE_H


# ---------------------------------------------------------------------------
# Scene rect
# ---------------------------------------------------------------------------


def set_scene_rect_and_repaint(scene, rect) -> None:
    """Set the scene rect, repainting every view if it actually moved.

    Changing the scene rect moves the scene->viewport mapping for the whole view,
    which strands any update queued against the old one: those updates land on the
    wrong pixels and the ones that needed repainting stay stale.  That is half of
    #73.  Every caller mutates the scene first — dropping a drag preview, clearing
    a snap highlight, rerouting paths, moving a node — and corrects the rect only
    afterwards, so the repaint is what makes the new mapping whole again.

    This is not a blanket cover-up for a lost update: an unchanged rect, which is
    the common case, returns below and costs nothing.
    """
    if rect == scene.sceneRect():
        return
    scene.setSceneRect(rect)
    for view in scene.views():
        view.viewport().update()


# ---------------------------------------------------------------------------
# NodeItem
# ---------------------------------------------------------------------------


class NodeItem(QGraphicsItem):
    """Rounded-rect node showing a state's label.

    Layout: a fixed-height left info section (NODE_INFO_W wide) containing the
    label and node_type, plus a right port section (NODE_PORT_W wide) containing
    named output port labels. Input port is on the left edge; output ports on the right.
    """

    def __init__(
        self,
        state_id: str,
        label: str | None = None,
        interactive: bool = False,
        returns: list[str] | None = None,
        is_initial: bool = False,
        is_terminal: bool = False,
        is_recovery: bool = False,
        is_error: bool = False,
        is_warning: bool = False,
        node_type: str = "",
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
        self._is_recovery = is_recovery
        self._is_error = is_error
        self._is_warning = is_warning
        self._returns: list[str] = list(returns) if returns else []
        self._port_marks: dict[str, str] = {}
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

    @property
    def _height(self) -> int:
        return NODE_H

    @property
    def body_height(self) -> int:
        """The node body's height: a call node's grows with its port rows."""
        return self._height

    def _node_rect(self) -> QRectF:
        """Local bounding rect of the node body (excluding margin)."""
        return QRectF(0, 0, NODE_W, NODE_H)

    def body_scene_rect(self) -> QRectF:
        """Scene-coordinate rect of the node body, excluding badges and hit-test margin."""
        return self.mapRectToScene(self._node_rect())

    def boundingRect(self) -> QRectF:
        # Port marks are drawn whether or not the node is interactive -- an undrawn
        # route still has to be visible while the bot is running -- so they need the
        # port margin even when the plain dots are not being painted.
        # Never zero: the body's outline is stroked on its edge, so half the widest
        # pen (2.5, the run highlight) plus antialiasing lies outside the body.
        margin = PORT_R + 1 if (self._interactive or self._port_marks) else 2
        if self._is_search_match:
            margin = max(margin, 8)
        nr = self._node_rect()
        badged = self._is_initial or self._is_terminal or self._is_recovery
        top_extra = _BADGE_R if badged else 0
        # The first badge is centered on the left edge, so it reaches half a badge (and
        # its pen) past it. Anything painted outside these bounds is never erased.
        left = max(margin, _BADGE_R + 1) if badged else margin
        return QRectF(nr.x() - left, -(margin + top_extra), NODE_W + left + margin, NODE_H + margin * 2 + top_extra)

    # ------------------------------------------------------------------
    # State setters
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Whether this node carries the run highlight."""
        return self._active

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

    #: Whether edges may start from / end on this node. An Exit port node has no
    #: output and an Entry none going in; a call node has several of each.
    has_input_port = True
    has_output_port = True

    def port_in(self) -> QPointF:
        """Scene-coordinate input port — left edge of info section (x=0)."""
        return self.scenePos() + QPointF(0, NODE_H / 2)

    def input_ports(self) -> list[str | None]:
        """The named input ports an edge can land on; ``[None]`` for the one plain one."""
        return [None] if self.has_input_port else []

    def port_in_at(self, port: str | None) -> QPointF:
        """Scene-coordinate input port *port*; an ordinary node has only the one."""
        return self.port_in()

    def port_out(self) -> QPointF:
        """Scene-coordinate output port (center-edge fallback when no named returns)."""
        return self.scenePos() + QPointF(NODE_W, NODE_H / 2)

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
            return self.scenePos() + QPointF(NODE_W, self._port_y(idx))
        return self.port_out()

    # ------------------------------------------------------------------
    # Public mutators
    # ------------------------------------------------------------------

    def set_label(self, label: str | None) -> None:
        """Update the displayed label; falls back to state_id when None."""
        self._label = label or self.state_id
        self.update()

    @property
    def label(self) -> str:
        """The text drawn as the node's title."""
        return self._label

    @property
    def node_type(self) -> str:
        """The node type currently drawn as the sub-label."""
        return self._node_type

    @property
    def returns(self) -> list[str]:
        """The named output ports currently drawn on the right edge."""
        return list(self._returns)

    def set_node_type(self, node_type: str) -> None:
        """Update the displayed node type sub-label."""
        self._node_type = node_type
        self.update()

    def set_returns(self, returns: list[str]) -> None:
        """Update named output ports and redraw."""
        self._returns = list(returns)
        self.prepareGeometryChange()
        self.update()

    @property
    def port_marks(self) -> dict[str, str]:
        """Return name -> PORT_MARK_* for every port that acts without an edge."""
        return dict(self._port_marks)

    def set_port_marks(self, marks: dict[str, str]) -> None:
        """Set which output ports draw a mark instead of a plain connection dot.

        Keys are return names, values PORT_MARK_RECOVERY or PORT_MARK_STOP. A port
        not named here draws the ordinary dot.
        """
        if marks == self._port_marks:
            return
        had_any = bool(self._port_marks)
        self._port_marks = dict(marks)
        if had_any != bool(self._port_marks):
            self.prepareGeometryChange()  # the mark margin appears or goes away
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

    @property
    def is_hovered(self) -> bool:
        """Whether the pointer is currently over this node."""
        return self._hovered

    @property
    def is_initial(self) -> bool:
        """Whether this node currently draws the FSM initial-state badge."""
        return self._is_initial

    @property
    def is_recovery(self) -> bool:
        """Whether this node currently draws the FSM recovery-state badge."""
        return self._is_recovery

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

    def set_recovery(self, is_recovery: bool) -> None:
        """Mark this node as the FSM recovery state (draws an amber ↺ badge)."""
        if self._is_recovery == is_recovery:
            return
        self.prepareGeometryChange()
        self._is_recovery = is_recovery
        self.update()

    def set_error(self, is_error: bool, reasons: list[str] | None = None) -> None:
        """Mark this node as having a configuration error (red tint + border + tooltip)."""
        new_reasons = list(reasons) if reasons is not None else self._reasons
        if self._is_error == is_error and self._reasons == new_reasons:
            return
        self._is_error = is_error
        self._reasons = new_reasons
        self._update_tooltip()
        self.update()

    def set_warning(self, is_warning: bool, reasons: list[str] | None = None) -> None:
        """Mark this node as having a configuration warning (amber tint + border + tooltip)."""
        new_reasons = list(reasons) if reasons is not None else self._reasons
        if self._is_warning == is_warning and self._reasons == new_reasons:
            return
        self._is_warning = is_warning
        self._reasons = new_reasons
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
        lines = list(self._reasons) if (self._is_error or self._is_warning) else []
        self.setToolTip("\n".join(lines))

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
            # The grid row is the body's vertical center, as the anchor is: a call node
            # is taller than NODE_H, so its own height has to be used here too.
            half = self.body_height / 2
            return QPointF(
                round(value.x() / GRID_W) * GRID_W,
                round((value.y() + half) / GRID_H) * GRID_H - half,
            )
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in list(self._edges):
                try:
                    edge._rebuild()
                except RuntimeError:
                    pass
            scene = self.scene()
            if scene is not None:
                item_rect = self.mapToScene(self.boundingRect()).boundingRect().adjusted(-500, -500, 500, 500)
                # The union keeps this cheap on a drag, where itemsBoundingRect() would
                # be O(items) on every position change.
                set_scene_rect_and_repaint(scene, scene.sceneRect().united(item_rect))
                # Regions follow their members from here, not from scene.changed: see
                # RegionManager. A plain QGraphicsScene (the tests') has no regions.
                note_moved = getattr(scene, "note_node_moved", None)
                if note_moved is not None:
                    note_moved()
        return super().itemChange(change, value)

    def _paint_port_mark(self, painter: QPainter, center: QPointF, mark: str, lod: float) -> None:
        """Draw a port that routes without an edge, as a badge in place of the dot.

        Same color and glyph as the node badge for the thing it stands for: the
        amber recovery arrow for a failure that falls through to the recovery node,
        the red stop square for a return the author marked fatal. Reading the pair
        as one route is the whole point of not drawing the edge.
        """
        if mark == PORT_MARK_STOP:
            color, icon = _badge_terminal(), _stop_badge_renderer()
        else:
            color, icon = _badge_recovery(), _recovery_badge_renderer()
        rect = QRectF(center.x() - PORT_R, center.y() - PORT_R, PORT_R * 2, PORT_R * 2)
        painter.save()
        painter.setPen(QPen(color.darker(140), 1.0))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(center, float(PORT_R), float(PORT_R))
        # Below a couple of device pixels the glyph is mush; the colored disc still
        # distinguishes the two marks, so it is dropped rather than drawn illegibly.
        if lod >= 0.75:
            icon.render(
                painter,
                rect.adjusted(_PORT_MARK_ICON_PAD, _PORT_MARK_ICON_PAD, -_PORT_MARK_ICON_PAD, -_PORT_MARK_ICON_PAD),
            )
        painter.restore()

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
            for i in range(len(self._returns)):
                if math.hypot(pos.x() - float(NODE_W), pos.y() - self._port_y(i)) <= _PORT_HIT_R:
                    near_port = True
                    break
        else:
            if math.hypot(pos.x() - float(NODE_INFO_W), pos.y() - NODE_H / 2) <= _PORT_HIT_R:
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
            compact_font = THEME.ui_font(THEME.font_node_px + 4)
            painter.setFont(compact_font)
            # Below a couple of device pixels the label renders to nothing and
            # Qt's glyph buffer collapses to a null QImage — see canvas.lod.
            if text_is_legible(painter.fontMetrics().height(), lod):
                painter.setPen(THEME.qcolor("text"))
                _wrap_flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter) | int(
                    Qt.TextFlag.TextWordWrap
                )
                painter.drawText(QRectF(nr.x() + 6, 4, NODE_W - 12, NODE_H - 8), _wrap_flags, self._label)
            badges: list[tuple[QColor, QSvgRenderer | str]] = []
            if self._is_initial:
                badges.append((_badge_initial(), _play_badge_renderer()))
            if self._is_terminal:
                badges.append((_badge_terminal(), _stop_badge_renderer()))
            if self._is_recovery:
                badges.append((_badge_recovery(), _recovery_badge_renderer()))
            badge_x0 = nr.x()
            for i, (color, icon) in enumerate(badges):
                cx = badge_x0 + float(i * (_BADGE_R * 2 + _BADGE_GAP))
                badge_rect = QRectF(cx - _BADGE_R, -_BADGE_R, _BADGE_R * 2, _BADGE_R * 2)
                icon_rect = badge_rect.adjusted(_BADGE_ICON_PAD, _BADGE_ICON_PAD, -_BADGE_ICON_PAD, -_BADGE_ICON_PAD)
                painter.save()
                painter.setPen(QPen(color.darker(140), 1.0))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(cx, 0.0), _BADGE_R, _BADGE_R)
                if isinstance(icon, QSvgRenderer):
                    icon.render(painter, icon_rect)
                else:
                    painter.setFont(THEME.ui_font(THEME.font_node_px - 7))
                    if text_is_legible(painter.fontMetrics().height(), lod):
                        painter.setPen(Qt.GlobalColor.white)
                        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, icon)
                painter.restore()
        else:
            # --- Full LOD ---
            sub_label_color = QColor(THEME.text) if self._active else THEME.qcolor("text_muted")

            # Info section: name wraps to 2 lines in [0..NODE_INFO_W], type label below
            painter.setFont(THEME.font_node_main())
            painter.setPen(THEME.qcolor("text"))
            _name_flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter) | int(
                Qt.TextFlag.TextWordWrap
            )
            painter.drawText(QRectF(8, 5, NODE_INFO_W - 16, 40), _name_flags, self._label)

            sub_text = self._node_type
            painter.setPen(sub_label_color)
            painter.setFont(THEME.font_node_sub())
            fm2 = painter.fontMetrics()
            sub_w = NODE_INFO_W - 16
            sub_elided = fm2.elidedText(sub_text, Qt.TextElideMode.ElideRight, sub_w)
            painter.drawText(QRectF(8, 47, sub_w, 17), Qt.AlignmentFlag.AlignCenter, sub_elided)

            if self._returns:
                n = len(self._returns)
                row_h = NODE_H / n

                # Vertical divider between info and port sections
                painter.setPen(QPen(border, 0.5))
                painter.drawLine(QPointF(float(NODE_INFO_W), 4), QPointF(float(NODE_INFO_W), NODE_H - 4))

                # Port labels and dots
                port_label_w = NODE_PORT_W - PORT_R * 2 - 8
                painter.setFont(THEME.font_node_sub())
                fm_port = painter.fontMetrics()
                for i, name in enumerate(self._returns):
                    py = self._port_y(i)
                    label_rect = QRectF(
                        float(NODE_INFO_W) + PORT_R + 4,
                        py - row_h / 2,
                        port_label_w,
                        row_h,
                    )
                    elided_name = fm_port.elidedText(name, Qt.TextElideMode.ElideRight, port_label_w)
                    painter.setPen(THEME.qcolor("text_subtle"))
                    painter.drawText(
                        label_rect,
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        elided_name,
                    )
                    mark = self._port_marks.get(name)
                    if mark is not None:
                        self._paint_port_mark(painter, QPointF(float(NODE_W), py), mark, lod)
                    elif self._interactive:
                        painter.setPen(QPen(_port_color(), 1.5))
                        painter.setBrush(QBrush(_port_color()))
                        painter.drawEllipse(QPointF(float(NODE_W), py), PORT_R, PORT_R)
            else:
                # No named returns: single output port at the right edge of the info section
                if self._interactive:
                    painter.setPen(QPen(_port_color(), 1.5))
                    painter.setBrush(QBrush(_port_color()))
                    painter.drawEllipse(QPointF(float(NODE_INFO_W), NODE_H / 2), PORT_R, PORT_R)

            # Structural-role badges: small circles overlapping the top-left corner of the node
            badges: list[tuple[QColor, QSvgRenderer | str]] = []
            if self._is_initial:
                badges.append((_badge_initial(), _play_badge_renderer()))
            if self._is_terminal:
                badges.append((_badge_terminal(), _stop_badge_renderer()))
            if self._is_recovery:
                badges.append((_badge_recovery(), _recovery_badge_renderer()))
            badge_x0 = nr.x()
            for i, (color, icon) in enumerate(badges):
                cx = badge_x0 + float(i * (_BADGE_R * 2 + _BADGE_GAP))
                badge_rect = QRectF(cx - _BADGE_R, -_BADGE_R, _BADGE_R * 2, _BADGE_R * 2)
                icon_rect = badge_rect.adjusted(_BADGE_ICON_PAD, _BADGE_ICON_PAD, -_BADGE_ICON_PAD, -_BADGE_ICON_PAD)
                painter.save()
                painter.setPen(QPen(color.darker(140), 1.0))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(cx, 0.0), _BADGE_R, _BADGE_R)
                if isinstance(icon, QSvgRenderer):
                    icon.render(painter, icon_rect)
                else:
                    painter.setFont(THEME.ui_font(THEME.font_node_px - 7))
                    painter.setPen(Qt.GlobalColor.white)
                    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, icon)
                painter.restore()

            # Input port dot at the left edge
            if self._interactive:
                painter.setPen(QPen(_port_color(), 1.5))
                painter.setBrush(QBrush(_port_color()))
                painter.drawEllipse(QPointF(0.0, NODE_H / 2), PORT_R, PORT_R)
