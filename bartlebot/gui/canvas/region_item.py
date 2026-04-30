"""Region overlay items for the bot canvas.

RegionItem — colored translucent background rect drawn behind member nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsSceneMouseEvent, QStyleOptionGraphicsItem

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
_LABEL_FONT_SIZE = 9
_LABEL_DARKER = 130  # QColor.darker() factor for label text / pill border
_PILL_H = 20  # pill height in px
_PILL_PAD_X = 10  # horizontal padding inside pill
_PILL_GAP = 4  # gap between pill bottom and region top
_PILL_ALPHA = 80  # fill alpha for pill (~31% opacity)
_BORDER_ALPHA = 100  # ~39% opacity for region border — recedes behind full-opacity edges


class RegionItem(QGraphicsItem):
    """A colored translucent rect drawn behind a set of member NodeItems.

    The item is fixed at scene origin (pos = 0,0); its bounding rect is
    expressed in scene coordinates.  Member positions are tracked via a
    ``scene.changed`` connection wired by EditScene.setup().

    Clicking the region's pill label in edit mode selects all member nodes.
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
        self._interactive: bool = False

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

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if not self._interactive or self._rect.isNull():
            event.ignore()
            return
        # The pill sits above self._rect.top() in item/scene coordinates (item is at origin).
        if event.pos().y() < self._rect.top():
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.scene().clearSelection()
            for sid in self._member_ids:
                node = self._node_items.get(sid)
                if node is not None:
                    node.setSelected(True)
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Rect recompute
    # ------------------------------------------------------------------

    def _recompute_rect(self) -> None:
        """Union all member node bounding rects + padding.  Called by scene.changed."""
        rects = []
        for sid in self._member_ids:
            node = self._node_items.get(sid)
            if node is not None:
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

    def set_color(self, key: int) -> None:
        self._color_key = key
        self.update()
