"""Region overlay items for the bot canvas.

RegionItem — colored translucent background rect drawn behind member nodes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsSceneMouseEvent, QStyleOptionGraphicsItem

from bartlebot.gui.canvas.lod import text_is_legible
from bartlebot.gui.theme import THEME

if TYPE_CHECKING:
    from bartlebot.bot.schema import RegionModel
    from bartlebot.gui.canvas.node_item import NodeItem

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PADDING = 60
_REGION_RADIUS = 12
_FILL_ALPHA = 40  # ~16% opacity for the region fill
_LABEL_DARKER = 130  # QColor.darker() factor for label text / pill border
_PILL_H = 20  # pill height in px
_PILL_PAD_X = 10  # horizontal padding inside pill
_PILL_GAP = 4  # gap between pill bottom and region top
_PILL_ALPHA = 80  # fill alpha for pill (~31% opacity)
_BORDER_ALPHA = 100  # ~39% opacity for region border — recedes behind full-opacity edges
_GLYPH_PX = 12  # the collapse button's box, in screen px
_GLYPH_GAP = 6  # between the label and the collapse button
_GLYPH_DIM_ALPHA = 90  # a collapse that is refused: the button is drawn, faded
_GLYPH_HIT_PAD = 3  # screen px of slack around the button for the pointer


class RegionItem(QGraphicsItem):
    """A colored translucent rect drawn behind a set of member NodeItems.

    The item is fixed at scene origin (pos = 0,0); its bounding rect is
    expressed in scene coordinates.  It follows its members because the scene
    calls ``recompute_rect`` (through ``RegionManager.recompute_all``) whenever a
    node moves -- never through ``scene.changed``; ``RegionManager`` says why.

    ``node_items`` is anything with ``get(state_id)``: the scene hands over a lookup
    that answers a subgraph member with the call node it is drawn as, so a region
    holding an instance wraps the call node.

    Clicking the region's pill label in edit mode selects all member nodes. The pill
    also carries a collapse button (#67), a boxed minus at its right end, in both modes:
    the scene hands over what a click does and, when a collapse would be refused, why --
    the button is then drawn faded and says so instead.
    """

    def __init__(
        self,
        region: RegionModel,
        node_items: dict[str, NodeItem],
    ) -> None:
        super().__init__()
        self._region_id = region.id
        self._label = region.label
        self._color_key = region.color
        self._member_ids: list[str] = list(region.members)
        self._node_items = node_items

        self._rect = QRectF()
        self._pill_w_nominal: float = 0.0  # pill width in screen px (zoom-invariant); set on first paint
        self._interactive: bool = False
        self._on_collapse: Callable[[], None] | None = None
        self._collapse_blocker: str | None = None
        self.setAcceptHoverEvents(True)

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setPos(0.0, 0.0)
        self.setZValue(-1)
        self.fit_exclude = True  # excluded from fit_scene() bounds to avoid pill top-margin inflation

    # ------------------------------------------------------------------
    # QGraphicsItem interface
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        if self._rect.isNull():
            return self._rect
        # Top margin is 8× the nominal pill size: covers constant-screen-size scaling
        # down to ~12% zoom without clipping the pill above the region rect.
        return self._rect.adjusted(0, -(_PILL_H + _PILL_GAP) * 8, 0, 0)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        if self._rect.isNull():
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = THEME.region_color(self._color_key)

        # Semi-transparent fill
        fill = QColor(color)
        fill.setAlpha(_FILL_ALPHA)
        painter.setBrush(QBrush(fill))

        border_color = QColor(color)
        border_color.setAlpha(_BORDER_ALPHA)
        pen = QPen(border_color, 2.0)
        painter.setPen(pen)

        path = QPainterPath()
        path.addRoundedRect(self._rect, _REGION_RADIUS, _REGION_RADIUS)
        painter.drawPath(path)

        # Label pill — constant screen size regardless of zoom level.
        raw_lod = option.levelOfDetailFromTransform(painter.worldTransform())
        lod = max(0.1, raw_lod)
        s = 1.0 / lod  # scene-unit scale: larger when zoomed out to keep screen size constant
        pill_h = _PILL_H * s
        pill_pad_x = _PILL_PAD_X * s
        pill_gap = _PILL_GAP * s

        label_color = QColor(color).darker(_LABEL_DARKER)
        font = THEME.scaled_ui_font(THEME.font_node_px - 3, s)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(self._label)
        glyph_room = (_GLYPH_GAP + _GLYPH_PX) * s if self._on_collapse is not None else 0.0
        pill_w = text_w + pill_pad_x * 2 + glyph_room
        pill_x = self._rect.left() + (self._rect.width() - pill_w) / 2
        pill_y = self._rect.top() - pill_h - pill_gap
        pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)
        self._pill_w_nominal = pill_w * lod  # screen-px width; zoom-invariant (pill_w ∝ 1/lod)

        pill_fill = QColor(color)
        pill_fill.setAlpha(_PILL_ALPHA)
        painter.setBrush(QBrush(pill_fill))
        painter.setPen(QPen(label_color, 1.5 * s))
        pill_path = QPainterPath()
        pill_path.addRoundedRect(pill_rect, pill_h / 2, pill_h / 2)
        painter.drawPath(pill_path)

        # Past the LOD clamp the pill stops compensating for zoom, so the label
        # shrinks with the real transform and eventually collapses Qt's glyph
        # buffer — see canvas.lod.  Test against raw_lod, not the clamped one.
        text_rect = pill_rect.adjusted(0, 0, -glyph_room, 0)
        if text_is_legible(fm.height(), raw_lod):
            painter.setPen(label_color)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._label)
        if self._on_collapse is not None:
            glyph = self._glyph_rect(pill_rect, s)
            glyph_color = QColor(label_color)
            if self._collapse_blocker is not None:
                glyph_color.setAlpha(_GLYPH_DIM_ALPHA)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(glyph_color, 1.4 * s))
            painter.drawRoundedRect(glyph, 2 * s, 2 * s)
            inset = glyph.width() * 0.26
            mid = glyph.center().y()
            painter.drawLine(QPointF(glyph.left() + inset, mid), QPointF(glyph.right() - inset, mid))

    @staticmethod
    def _glyph_rect(pill_rect: QRectF, s: float) -> QRectF:
        """The collapse button inside a pill drawn at scale ``s``: a square at its right end."""
        size = _GLYPH_PX * s
        return QRectF(pill_rect.right() - _PILL_PAD_X * s - size, pill_rect.center().y() - size / 2, size, size)

    # ------------------------------------------------------------------
    # The collapse button
    # ------------------------------------------------------------------

    def set_collapse_action(self, on_collapse: Callable[[], None] | None, blocker: str | None = None) -> None:
        """What clicking the pill's collapse button does, or None for no button; ``blocker`` fades it."""
        if on_collapse is self._on_collapse and blocker == self._collapse_blocker:
            return
        self._on_collapse = on_collapse
        self._collapse_blocker = blocker
        self.update()

    @property
    def collapse_blocker(self) -> str | None:
        return self._collapse_blocker

    def collapse_button_rect(self) -> QRectF:
        """The collapse button in scene coordinates at the current zoom, or empty with none."""
        if self._on_collapse is None:
            return QRectF()
        pill = self._pill_hit_rect()
        if pill.isNull():
            return QRectF()
        s = pill.height() / _PILL_H
        pad = _GLYPH_HIT_PAD * s
        return self._glyph_rect(pill, s).adjusted(-pad, -pad, pad, pad)

    def hoverMoveEvent(self, event) -> None:
        on_button = self.collapse_button_rect().contains(event.pos())
        if on_button:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(self._collapse_blocker or "Collapse region")
        else:
            self.unsetCursor()
            self.setToolTip("")
        super().hoverMoveEvent(event)

    def shape(self) -> QPainterPath:
        # boundingRect() is intentionally oversized (8× pill height) so Qt never clips the
        # pill at low zoom levels. shape() governs hit-testing separately, so we restrict
        # it to the region rect plus the pill's actual rendered bounds at the current zoom.
        path = QPainterPath()
        if not self._rect.isNull():
            path.addRoundedRect(self._rect, _REGION_RADIUS, _REGION_RADIUS)
            pill = self._pill_hit_rect()
            if not pill.isNull():
                path.addRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        return path

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._on_collapse is not None
            and self.collapse_button_rect().contains(event.pos())
        ):
            # In either mode: collapsing is a view in Run mode. A refused one does nothing.
            if self._collapse_blocker is None:
                self._on_collapse()
            event.accept()
            return
        if not self._interactive or self._rect.isNull():
            event.ignore()
            return
        if self._pill_hit_rect().contains(event.pos()):
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.scene().clearSelection()
            for sid in self._member_ids:
                node = self._node_items.get(sid)
                if node is not None and not node.isSelected():
                    node.setSelected(True)
            event.accept()
        else:
            event.ignore()

    def _pill_hit_rect(self) -> QRectF:
        """Pill bounds in scene coordinates at the current view zoom.

        Computed dynamically so shape() and mousePressEvent stay accurate across zoom
        changes without relying on a cached value from the last paint() call.
        Font-metric-dependent pill width is not available here, so the full region
        width is used — this is intentionally generous for hit-testing.
        """
        if self._rect.isNull():
            return QRectF()
        lod = 1.0
        scene = self.scene()
        if scene and scene.views():
            lod = max(0.1, scene.views()[0].transform().m11())
        s = 1.0 / lod
        pill_h = _PILL_H * s
        pill_gap = _PILL_GAP * s
        if self._pill_w_nominal > 0:
            pill_w = self._pill_w_nominal * s
            pill_x = self._rect.left() + (self._rect.width() - pill_w) / 2
        else:
            pill_w = self._rect.width()
            pill_x = self._rect.left()
        return QRectF(pill_x, self._rect.top() - pill_h - pill_gap, pill_w, pill_h)

    # ------------------------------------------------------------------
    # Rect recompute
    # ------------------------------------------------------------------

    def recompute_rect(self) -> None:
        """Union all member node bounding rects + padding.  Called whenever a member may have moved."""
        rects = []
        seen: set[int] = set()
        for sid in self._member_ids:
            node = self._node_items.get(sid)
            if node is not None and id(node) not in seen:
                seen.add(id(node))
                rects.append(node.body_scene_rect())

        if not rects:
            new_rect = QRectF()
        else:
            union = rects[0]
            for r in rects[1:]:
                union = union.united(r)
            new_rect = union.adjusted(-_PADDING, -_PADDING, _PADDING, _PADDING)

        if new_rect != self._rect:
            self.prepareGeometryChange()
            self._rect = new_rect
            self.update()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def region_id(self) -> str:
        return self._region_id

    @property
    def member_ids(self) -> list[str]:
        return self._member_ids

    def set_interactive(self, interactive: bool) -> None:
        self._interactive = interactive

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    @property
    def scene_bounds(self) -> QRectF:
        """The region's box in scene coordinates, as drawn (padding included)."""
        return QRectF(self._rect)

    def set_color(self, key: int) -> None:
        self._color_key = key
        self.update()
