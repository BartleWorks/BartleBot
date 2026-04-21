"""Region overlay items for the bot canvas.

_RegionHandle  — draggable grip at the top-left of a region; moves all member nodes.
RegionItem     — colored translucent background rect drawn behind member nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

from bartlebot.gui.theme import THEME

if TYPE_CHECKING:
    from bartlebot.bot.schema import RegionModel
    from bartlebot.gui.canvas.node_item import NodeItem

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PADDING = 20
_HANDLE_SIZE = 16
_REGION_RADIUS = 12
_FILL_ALPHA = 40  # ~16% opacity for the region fill
_LABEL_FONT_SIZE = 9
_LABEL_DARKER = 130  # QColor.darker() factor for label text / pill border
_PILL_H = 20  # pill height in px
_PILL_PAD_X = 10  # horizontal padding inside pill
_PILL_GAP = 4  # gap between pill bottom and region top
_PILL_ALPHA = 80  # fill alpha for pill (~31% opacity)


class _RegionHandle(QGraphicsItem):
    """A small draggable grip pinned to the top-left of a RegionItem.

    Dragging the handle translates all member NodeItems by the same delta.
    The handle itself never actually moves — it resets to its fixed offset
    after each position change, making the drag feel natural while keeping
    the handle stationary relative to the region rect.
    """

    def __init__(self, color_key: int, node_items_ref: dict, member_ids_ref: list, parent: QGraphicsItem) -> None:
        super().__init__(parent)
        self._color_key = color_key
        self._node_items = node_items_ref  # live dict reference from RegionItem
        self._member_ids = member_ids_ref  # live list reference from RegionItem
        self._fixed_offset = QPointF(0.0, 0.0)
        self._last_pos = QPointF(0.0, 0.0)

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(2)

    # ------------------------------------------------------------------
    # QGraphicsItem interface
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, _HANDLE_SIZE, _HANDLE_SIZE)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        color = THEME.region_color(self._color_key)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        path = QPainterPath()
        path.addRoundedRect(self.boundingRect(), 4, 4)
        painter.drawPath(path)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            new_pos: QPointF = value
            # Compute scene-space delta
            delta = self.mapToScene(new_pos) - self.mapToScene(self._last_pos)
            if abs(delta.x()) > 0.001 or abs(delta.y()) > 0.001:
                # Translate all live member nodes
                for sid in self._member_ids:
                    node = self._node_items.get(sid)
                    if node is not None:
                        node.setPos(node.pos() + delta)
            # Always reset to fixed offset — handle never actually moves
            return self._fixed_offset
        return super().itemChange(change, value)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_interactive(self, interactive: bool) -> None:
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, interactive)

    def update_offset(self, offset: QPointF) -> None:
        """Reposition the handle to track the top-left of the region rect."""
        self._fixed_offset = offset
        self._last_pos = offset
        self.setPos(offset)


class RegionItem(QGraphicsItem):
    """A colored translucent rect drawn behind a set of member NodeItems.

    The item is fixed at scene origin (pos = 0,0); its bounding rect is
    expressed in scene coordinates.  Member positions are tracked via a
    ``scene.changed`` connection wired by EditScene.setup().
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

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setPos(0.0, 0.0)
        self.setZValue(-1)
        self.fit_exclude = True  # excluded from fit_scene() bounds to avoid pill top-margin inflation

        self._handle = _RegionHandle(
            color_key=self._color_key,
            node_items_ref=self._node_items,
            member_ids_ref=self._member_ids,
            parent=self,
        )

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

        # Full-opacity border
        pen = QPen(color, 2.0)
        painter.setPen(pen)

        path = QPainterPath()
        path.addRoundedRect(self._rect, _REGION_RADIUS, _REGION_RADIUS)
        painter.drawPath(path)

        # Label pill — constant screen size regardless of zoom level.
        lod = max(0.1, option.levelOfDetailFromTransform(painter.worldTransform()))
        s = 1.0 / lod  # scene-unit scale: larger when zoomed out to keep screen size constant
        pill_h = _PILL_H * s
        pill_pad_x = _PILL_PAD_X * s
        pill_gap = _PILL_GAP * s

        label_color = QColor(color).darker(_LABEL_DARKER)
        font = QFont(THEME.font_ui_family)
        font.setPointSizeF(max(1.0, _LABEL_FONT_SIZE * s))
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(self._label)
        pill_w = text_w + pill_pad_x * 2
        pill_x = self._rect.left() + (self._rect.width() - pill_w) / 2
        pill_y = self._rect.top() - pill_h - pill_gap
        pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)

        pill_fill = QColor(color)
        pill_fill.setAlpha(_PILL_ALPHA)
        painter.setBrush(QBrush(pill_fill))
        painter.setPen(QPen(label_color, 1.5 * s))
        pill_path = QPainterPath()
        pill_path.addRoundedRect(pill_rect, pill_h / 2, pill_h / 2)
        painter.drawPath(pill_path)

        painter.setPen(label_color)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, self._label)

    # ------------------------------------------------------------------
    # Rect recompute
    # ------------------------------------------------------------------

    def _recompute_rect(self) -> None:
        """Union all member node bounding rects + padding.  Called by scene.changed."""
        rects = []
        for sid in self._member_ids:
            node = self._node_items.get(sid)
            if node is not None:
                rects.append(node.mapRectToScene(node.boundingRect()))

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
            self._handle.update_offset(QPointF(self._rect.left(), self._rect.top()))
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
        self._handle.set_interactive(interactive)

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def set_color(self, key: int) -> None:
        self._color_key = key
        self._handle._color_key = key
        self._handle.update()
        self.update()
