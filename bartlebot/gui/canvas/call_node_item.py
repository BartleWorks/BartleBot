"""The node shapes a group of states is drawn as, and the Entry/Exit pills.

``FoldedNodeItem`` is any group of states drawn as one node: its entry ports down the
left edge and its exit ports down the right, labelled by name. Nothing in it assumes a
definition exists; its two shapes do:

- ``CallNodeItem`` is a subgraph instance. What marks it as a call is its *shape* --
  double side bars, the flowchart "predefined process", which has always meant a call
  to something defined elsewhere -- and a link badge in its corner carrying the status
  dot. Colour is only a supporting cue: region colour 5 is too close to the accent for
  colour alone to tell a call from a region.
- ``CollapsedRegionItem`` is a plain region folded away (#67), after BPMN's collapsed
  embedded sub-process: a single border in the region's own colour and a boxed-plus
  marker, no side bars and no badge. Ordinary nodes, calls and collapsed regions differ
  by shape, never by colour alone.

``PortNodeItem`` is an Entry or Exit node inside a definition: a pill in the accent,
with an output port only (Entry) or an input port only (Exit).

All are ``NodeItem`` subclasses, so selection, dragging, snapping and edge
bookkeeping work unchanged; what differs is the geometry of their ports.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QStyleOptionGraphicsItem

from bartlebot.gui.canvas.lod import text_is_legible
from bartlebot.gui.canvas.node_item import (
    NODE_H,
    NODE_W,
    PORT_R,
    NodeItem,
    _badge_initial,
    _badge_recovery,
    _play_badge_renderer,
    _recovery_badge_renderer,
)
from bartlebot.gui.theme import THEME

_HEADER_H = 46  # title and sub-line
_ROW_H = 20  # one port row
_FOOT_PAD = 6
_BAR_INSET = 10  # the double side bars sit this far in from each edge
_CALL_RADIUS = 4
_GLYPH = 14  # link badge size
_EDGE_ALPHA = 200  # how strongly an accent pill's edge shows against the canvas
_PORT_HIT_R = 16
_ROLE_BADGE_R = 7
_ROLE_BADGE_GAP = 3  # between two badges on one port, and before its name


def canvas_edge_color() -> QColor:
    """The edge of an accent-filled shape over the canvas: the Entry/Exit pills and the crumb bar.

    It is the canvas's own text colour, so it stands off the canvas in either theme (light in
    the dark one, dark in the light one) and between two accent fills that touch, which a
    darker accent did against neither.
    """
    color = THEME.qcolor("text")
    color.setAlpha(_EDGE_ALPHA)
    return color


#: Link badge states -- see :func:`paint_link_glyph`.
LINK_STATES = ("linked", "outdated", "missing", "open")

_LINK_TOOLTIPS = {
    "linked": "Linked to a library subgraph",
    "outdated": "Linked to a library subgraph; the update is held, see the validation banner",
    "missing": "Linked to a library subgraph the library cannot supply; running the stored snapshot",
    "open": "This subgraph's definition is open",
}


def paint_link_glyph(painter: QPainter, x: float, y: float, size: float, color: QColor, state: str | None) -> None:
    """Two overlapping squares -- an instance of something -- with a status dot.

    The dot is what changes with the state, so the glyph is one shape to learn: no
    dot for a clean link, the warning colour when the update is held, the error
    colour when the library cannot supply it, the accent while its definition is
    open. Colours are read from THEME here, at paint time, so a theme switch needs no
    bookkeeping.
    """
    sq = size * 0.68
    painter.save()
    painter.setBrush(QBrush(THEME.qcolor("node_fill")))
    painter.setPen(QPen(color, 1.4))
    painter.drawRoundedRect(QRectF(x, y + size - sq, sq, sq), 2, 2)
    painter.drawRoundedRect(QRectF(x + size - sq, y, sq, sq), 2, 2)
    dot_token = {"outdated": "warning", "missing": "error", "open": "accent"}.get(state or "")
    if dot_token is not None:
        dot = QColor(THEME.qcolor(dot_token))
        r = size * 0.24
        painter.setPen(QPen(dot.darker(130), 1.0))
        painter.setBrush(QBrush(dot))
        painter.drawEllipse(QPointF(x + size - r * 0.6, y + r * 0.6), r, r)
    painter.restore()


class FoldedNodeItem(NodeItem):
    """A group of states drawn as one node. ``state_id`` is the group's region id.

    ``_returns`` holds the exit port ids, so everything that asks a node for its
    output ports -- starting a drag, routing an edge, the port hit-test -- asks it
    of the exits without knowing it is talking to a folded node. Subclasses give the
    shape: the frame's colour, what marks it, and the corner marker.
    """

    #: How far in from each side the content starts: past a call's side bars.
    _INSET = 0.0

    def __init__(
        self,
        region_id: str,
        label: str,
        *,
        subline: str,
        entries: list[tuple[str, str]],
        exits: list[tuple[str, str]],
        interactive: bool = False,
    ) -> None:
        self._entries = list(entries)
        self._exit_names = dict(exits)
        super().__init__(region_id, label, interactive=interactive, returns=[pid for pid, _ in exits])
        self._subline = subline
        self._initial_port: str | None = None
        self._recovery_port: str | None = None
        self._active_member: str | None = None

    # -- geometry ------------------------------------------------------

    @property
    def _height(self) -> int:
        rows = max(len(self._entries), len(self._returns), 1)
        return max(NODE_H, _HEADER_H + rows * _ROW_H + _FOOT_PAD)

    def _node_rect(self) -> QRectF:
        return QRectF(0, 0, NODE_W, self._height)

    def boundingRect(self) -> QRectF:
        margin = PORT_R + 1
        top = 8 if self._is_search_match else margin
        return QRectF(-margin - 8, -top, NODE_W + margin + PORT_R + 9, self._height + top * 2)

    def center(self) -> QPointF:
        return self.scenePos() + QPointF(NODE_W / 2, self._height / 2)

    def _row_y(self, idx: int) -> float:
        return _HEADER_H + (idx + 0.5) * _ROW_H

    def _port_y(self, idx: int) -> float:
        return self._row_y(idx)

    def input_ports(self) -> list[str | None]:
        return [pid for pid, _ in self._entries]

    def port_in(self) -> QPointF:
        return self.port_in_at(self._entries[0][0] if self._entries else None)

    def port_in_at(self, port: str | None) -> QPointF:
        ids = [pid for pid, _ in self._entries]
        idx = ids.index(port) if port in ids else 0
        return self.scenePos() + QPointF(0, self._row_y(idx) if ids else self._height / 2)

    def port_out(self) -> QPointF:
        return self.scenePos() + QPointF(NODE_W, self._height / 2)

    # -- state ---------------------------------------------------------

    @property
    def entries(self) -> list[tuple[str, str]]:
        """``(port id, name)`` for each entry port, top to bottom."""
        return list(self._entries)

    @property
    def exits(self) -> list[tuple[str, str]]:
        """``(port id, name)`` for each exit port, top to bottom."""
        return [(pid, self._exit_names.get(pid, pid)) for pid in self._returns]

    def exit_name(self, pid: str | None) -> str:
        return self._exit_names.get(pid or "", pid or "")

    @property
    def subline(self) -> str:
        return self._subline

    def set_subline(self, text: str) -> None:
        self._subline = text
        self.update()

    def _update_tooltip(self) -> None:
        lines = list(self._reasons) if (self._is_error or self._is_warning) else []
        extra = self._tooltip_extra()
        if extra:
            lines.append(extra)
        self.setToolTip("\n".join(lines))

    # -- the shape: what a subclass decides ----------------------------

    def _tooltip_extra(self) -> str | None:
        return None

    def _frame_color(self) -> QColor:
        """The border's colour when nothing is wrong."""
        return THEME.qcolor("accent")

    def _paint_shape_marks(self, painter: QPainter, rect: QRectF, border: QColor) -> None:
        """What marks the shape, drawn at every level of detail."""

    def _paint_marker(self, painter: QPainter, x: float, y: float, size: float) -> None:
        """The corner marker, top right, drawn when details are."""

    @property
    def role_ports(self) -> tuple[str | None, str | None]:
        """``(initial, recovery)``: the entry port each bot role points at, or None."""
        return self._initial_port, self._recovery_port

    def set_role_ports(self, initial: str | None, recovery: str | None) -> None:
        if (initial, recovery) == (self._initial_port, self._recovery_port):
            return
        self._initial_port, self._recovery_port = initial, recovery
        self.update()

    @property
    def active_member(self) -> str | None:
        return self._active_member

    def set_active_member(self, label: str | None) -> None:
        """While a member runs, the title reads ``<instance> — <member>``."""
        self._active_member = label
        self.update()

    def hoverMoveEvent(self, event) -> None:
        pos = event.pos()
        near = any(math.hypot(pos.x(), pos.y() - self._row_y(i)) <= _PORT_HIT_R for i in range(len(self._entries)))
        near = near or any(
            math.hypot(pos.x() - NODE_W, pos.y() - self._row_y(i)) <= _PORT_HIT_R for i in range(len(self._returns))
        )
        self.setCursor(Qt.CursorShape.CrossCursor if near else Qt.CursorShape.SizeAllCursor)

    # -- paint ---------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self._interactive and self.isSelected()
        if self._active:
            fill = self._active_fill
        elif self._is_error:
            fill = THEME.qcolor("node_fill_error_hover" if self._hovered else "node_fill_error")
        elif self._is_warning:
            fill = THEME.qcolor("node_fill_warning_hover" if self._hovered else "node_fill_warning")
        elif selected:
            fill = THEME.qcolor("node_fill_selected")
        else:
            fill = THEME.qcolor("node_fill_hover" if self._hovered else "node_fill")
        if self._is_error:
            border = THEME.qcolor("error")
        elif self._is_warning:
            border = THEME.qcolor("warning")
        else:
            border = self._frame_color()
        rect = self._node_rect()
        lod = option.levelOfDetailFromTransform(painter.worldTransform())

        if self._is_search_match:
            ring = THEME.qcolor("search_match_current" if self._is_search_current else "search_match_other")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ring))
            painter.drawRoundedRect(rect.adjusted(-7, -7, 7, 7), _CALL_RADIUS + 5, _CALL_RADIUS + 5)

        painter.setPen(QPen(border, 2.8 if (selected or self._active) else 1.8))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(rect, _CALL_RADIUS, _CALL_RADIUS)
        self._paint_shape_marks(painter, rect, border)

        from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 - as NodeItem.paint

        threshold = SettingsManager.instance().get("canvas", "lod_compact_threshold", default=0.6)
        title = f"{self._label} — {self._active_member}" if self._active_member else self._label
        if lod < threshold:
            painter.setFont(THEME.ui_font(THEME.font_node_px + 4))
            if text_is_legible(painter.fontMetrics().height(), lod):
                painter.setPen(THEME.qcolor("text"))
                flags = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap)
                painter.drawText(rect.adjusted(self._INSET + 6, 4, -self._INSET - 6, -4), flags, title)
            self._paint_role_badges(painter)
            return

        inner_l, inner_r = self._INSET + 8, NODE_W - self._INSET - 8
        painter.setFont(THEME.font_node_main())
        painter.setPen(THEME.qcolor("text"))
        fm = painter.fontMetrics()
        glyph_room = _GLYPH + 6
        painter.drawText(
            QRectF(inner_l, 4, inner_r - inner_l - glyph_room, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(title, Qt.TextElideMode.ElideRight, int(inner_r - inner_l - glyph_room)),
        )
        painter.setFont(THEME.font_node_sub())
        painter.setPen(QColor(THEME.text) if self._active else THEME.qcolor("text_muted"))
        fm2 = painter.fontMetrics()
        painter.drawText(
            QRectF(inner_l, 24, inner_r - inner_l, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm2.elidedText(self._subline, Qt.TextElideMode.ElideRight, int(inner_r - inner_l)),
        )
        self._paint_marker(painter, inner_r - _GLYPH, 6, _GLYPH)

        half = (inner_r - inner_l) / 2
        painter.setPen(THEME.qcolor("text_subtle"))
        for i, (pid, name) in enumerate(self._entries):
            y = self._row_y(i)
            # The port's role badges lead its name, which starts after them.
            indent = len(self._roles_of(pid)) * (_ROLE_BADGE_R * 2 + _ROLE_BADGE_GAP)
            painter.drawText(
                QRectF(inner_l + indent, y - _ROW_H / 2, half - 4 - indent, _ROW_H),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm2.elidedText(name, Qt.TextElideMode.ElideRight, int(half - 4 - indent)),
            )
        for i, pid in enumerate(self._returns):
            y = self._row_y(i)
            painter.drawText(
                QRectF(inner_l + half + 4, y - _ROW_H / 2, half - 4, _ROW_H),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                fm2.elidedText(self.exit_name(pid), Qt.TextElideMode.ElideRight, int(half - 4)),
            )
        if self._interactive:
            port = THEME.qcolor("port_color")
            painter.setPen(QPen(port, 1.5))
            painter.setBrush(QBrush(port))
            for i in range(len(self._entries)):
                painter.drawEllipse(QPointF(0.0, self._row_y(i)), PORT_R, PORT_R)
            for i in range(len(self._returns)):
                painter.drawEllipse(QPointF(float(NODE_W), self._row_y(i)), PORT_R, PORT_R)
        self._paint_role_badges(painter)

    def _roles_of(self, pid: str) -> list[str]:
        """The roles pointing at entry port ``pid``, initial first: none, one, or both."""
        return [
            role
            for role, port in (("initial", self._initial_port), ("recovery", self._recovery_port))
            if port is not None and port == pid
        ]

    def _paint_role_badges(self, painter: QPainter) -> None:
        """Each role badges the entry port it points at, inside the body at the start of the row.

        Inside, so no edge arriving at the port can cross one. A port holding both roles
        wears both, side by side in the order a plain node draws them.
        """
        x0 = self._INSET + 8 + _ROLE_BADGE_R
        for i, (pid, _name) in enumerate(self._entries):
            cy = self._row_y(i)
            for n, role in enumerate(self._roles_of(pid)):
                if role == "initial":
                    color, icon = _badge_initial(), _play_badge_renderer()
                else:
                    color, icon = _badge_recovery(), _recovery_badge_renderer()
                cx = x0 + n * (_ROLE_BADGE_R * 2 + _ROLE_BADGE_GAP)
                painter.save()
                painter.setPen(QPen(color.darker(140), 1.0))
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(cx, cy), _ROLE_BADGE_R, _ROLE_BADGE_R)
                r = _ROLE_BADGE_R - 2.5
                icon.render(painter, QRectF(cx - r, cy - r, r * 2, r * 2))
                painter.restore()


class CallNodeItem(FoldedNodeItem):
    """A subgraph instance, drawn as one node: double side bars and a link badge."""

    _INSET = float(_BAR_INSET)

    def __init__(self, *args, **kwargs) -> None:
        self._link_state: str | None = "linked"
        super().__init__(*args, **kwargs)

    @property
    def link_state(self) -> str | None:
        return self._link_state

    def set_link_state(self, state: str | None) -> None:
        if state is not None and state not in LINK_STATES:
            raise ValueError(f"unknown link state {state!r}")
        if state == self._link_state:
            return
        self._link_state = state
        self._update_tooltip()
        self.update()

    def _tooltip_extra(self) -> str | None:
        return _LINK_TOOLTIPS.get(self._link_state or "")

    def _paint_shape_marks(self, painter: QPainter, rect: QRectF, border: QColor) -> None:
        # The bars are the shape that says "call": drawn at every level of detail.
        painter.setPen(QPen(border, 1.8))
        for x in (_BAR_INSET, NODE_W - _BAR_INSET):
            painter.drawLine(QPointF(x, 0.0), QPointF(x, rect.height()))

    def _paint_marker(self, painter: QPainter, x: float, y: float, size: float) -> None:
        paint_link_glyph(painter, x, y, size, THEME.qcolor("accent"), self._link_state)


def paint_collapse_marker(painter: QPainter, x: float, y: float, size: float, color: QColor) -> None:
    """A boxed plus -- BPMN's mark for a collapsed sub-process: there is more inside."""
    painter.save()
    painter.setBrush(QBrush(THEME.qcolor("node_fill")))
    painter.setPen(QPen(color, 1.4))
    painter.drawRoundedRect(QRectF(x, y, size, size), 2, 2)
    mid, pad = size / 2, size * 0.26
    painter.drawLine(QPointF(x + pad, y + mid), QPointF(x + size - pad, y + mid))
    painter.drawLine(QPointF(x + mid, y + pad), QPointF(x + mid, y + size - pad))
    painter.restore()


class CollapsedRegionItem(FoldedNodeItem):
    """A plain region folded into one node (#67): a single border in its own colour, and a boxed plus.

    The colour is a palette slot read from THEME at paint time, like the region's own
    frame, so a theme switch needs no bookkeeping and a recolour only ``set_color``.
    """

    def __init__(self, *args, color: int, **kwargs) -> None:
        self._color_key = color
        super().__init__(*args, **kwargs)

    @property
    def color_key(self) -> int:
        return self._color_key

    def set_color(self, key: int) -> None:
        self._color_key = key
        self.update()

    def _frame_color(self) -> QColor:
        return QColor(THEME.region_color(self._color_key))

    def _paint_marker(self, painter: QPainter, x: float, y: float, size: float) -> None:
        paint_collapse_marker(painter, x, y, size, self._frame_color())


class PortNodeItem(NodeItem):
    """An Entry or Exit node in a definition: a pill in the accent, ported on one side only."""

    def __init__(self, state_id: str, label: str | None, *, is_entry: bool, interactive: bool = False) -> None:
        super().__init__(state_id, label, interactive=interactive, returns=[])
        self._is_entry = is_entry
        self.has_input_port = not is_entry
        self.has_output_port = is_entry

    @property
    def is_entry(self) -> bool:
        return self._is_entry

    def input_ports(self) -> list[str | None]:
        return [None] if self.has_input_port else []

    def hoverMoveEvent(self, event) -> None:
        pos = event.pos()
        x = float(NODE_W) if self._is_entry else 0.0
        near = math.hypot(pos.x() - x, pos.y() - NODE_H / 2) <= _PORT_HIT_R
        self.setCursor(Qt.CursorShape.CrossCursor if near else Qt.CursorShape.SizeAllCursor)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = THEME.qcolor("accent")
        rect = self._node_rect()
        selected = self._interactive and self.isSelected()
        if self._is_search_match:
            ring = THEME.qcolor("search_match_current" if self._is_search_current else "search_match_other")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ring))
            painter.drawRoundedRect(rect.adjusted(-7, -7, 7, 7), NODE_H / 2 + 7, NODE_H / 2 + 7)
        fill = accent.lighter(115) if (self._hovered or selected) else accent
        if self._is_error:
            border = THEME.qcolor("error")
        elif self._is_warning:
            border = THEME.qcolor("warning")
        else:
            border = canvas_edge_color()
        painter.setPen(QPen(border, 2.5 if selected else 2.0))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(rect, NODE_H / 2, NODE_H / 2)
        painter.setFont(THEME.ui_font(THEME.font_node_px + 2, bold=True))
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        if text_is_legible(painter.fontMetrics().height(), lod):
            painter.setPen(THEME.qcolor("pill_text"))
            text = f"▸ {self._label}" if self._is_entry else f"{self._label} ▸"
            fm = painter.fontMetrics()
            painter.drawText(
                rect.adjusted(NODE_H / 2, 0, -NODE_H / 2, 0),
                Qt.AlignmentFlag.AlignCenter,
                fm.elidedText(text, Qt.TextElideMode.ElideRight, int(NODE_W - NODE_H)),
            )
        if self._interactive:
            port = THEME.qcolor("port_color")
            painter.setPen(QPen(port.darker(130), 1.5))
            painter.setBrush(QBrush(THEME.qcolor("node_fill")))
            x = float(NODE_W) if self._is_entry else 0.0
            painter.drawEllipse(QPointF(x, NODE_H / 2), PORT_R, PORT_R)
