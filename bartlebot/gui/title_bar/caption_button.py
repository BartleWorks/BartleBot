"""The minimize, maximize and close buttons at the right end of the title bar.

Painted rather than drawn from an icon font. Windows 11 draws its own from Segoe Fluent
Icons, which Windows 10 does not have, and a glyph from a font is placed by the font's
metrics rather than on the pixel grid. Four strokes of a cosmetic pen land on whole
physical pixels at every scale factor, which is what makes them read as Windows' own.

Every color is read from ``THEME`` in ``paintEvent``, so a theme switch needs nothing
re-derived and the class needs no ``ThemedMixin``.
"""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QWidget

from bartlebot.gui.theming.theme import THEME

#: Windows' own caption buttons are this wide at every height.
CAPTION_BUTTON_WIDTH = 46

#: The side of a glyph's box, in logical pixels: 10, as Windows draws them.
_GLYPH = 10


class CaptionKind(Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    CLOSE = "close"


_TOOLTIPS = {CaptionKind.MINIMIZE: "Minimize", CaptionKind.MAXIMIZE: "Maximize", CaptionKind.CLOSE: "Close"}


class CaptionButton(QAbstractButton):
    """One window button. A ``MAXIMIZE`` button draws the restore glyph while the window is
    maximized — see :meth:`set_maximized`."""

    def __init__(self, kind: CaptionKind, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._maximized = False
        self.setFixedSize(CAPTION_BUTTON_WIDTH, height)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Repaint on enter and leave: the hover fill is the only feedback these give.
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setToolTip(_TOOLTIPS[kind])
        self.setAccessibleName(_TOOLTIPS[kind])

    @property
    def kind(self) -> CaptionKind:
        return self._kind

    def set_maximized(self, maximized: bool) -> None:
        """Draw the restore glyph while the window is maximized (``MAXIMIZE`` only)."""
        if self._kind is not CaptionKind.MAXIMIZE or maximized == self._maximized:
            return
        self._maximized = maximized
        label = "Restore Down" if maximized else "Maximize"
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(CAPTION_BUTTON_WIDTH, self.height())

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        fill, ink = self._colors()
        if fill is not None:
            painter.fillRect(self.rect(), fill)
        dpr = self.devicePixelRatioF() or 1.0
        pen = QPen(ink)
        pen.setCosmetic(True)
        pen.setWidthF(max(1.0, round(dpr)))
        painter.setPen(pen)
        # Centered, then snapped to the physical pixel grid so each stroke is one crisp line.
        side = round(_GLYPH * dpr) / dpr
        left = round((self.width() - side) / 2 * dpr) / dpr + 0.5 / dpr
        top = round((self.height() - side) / 2 * dpr) / dpr + 0.5 / dpr
        box = QRectF(left, top, side, side)
        if self._kind is CaptionKind.MINIMIZE:
            middle = round((top + side / 2) * dpr) / dpr + 0.5 / dpr
            painter.drawLine(QPointF(box.left(), middle), QPointF(box.right(), middle))
        elif self._kind is CaptionKind.CLOSE:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.drawLine(box.topLeft(), box.bottomRight())
            painter.drawLine(box.topRight(), box.bottomLeft())
        elif self._maximized:
            step = round(2 * dpr) / dpr
            front = box.adjusted(0, step, -step, 0)
            painter.drawRect(front)
            # The back window shows only where the front one does not cover it.
            painter.drawLine(QPointF(front.left() + step, box.top()), QPointF(box.right(), box.top()))
            painter.drawLine(QPointF(box.right(), box.top()), QPointF(box.right(), front.bottom() - step))
        else:
            painter.drawRect(box)
        painter.end()

    def _colors(self) -> tuple[QColor | None, QColor]:
        """The fill (``None`` for none) and the glyph's color for the current state."""
        ink = QColor(THEME.text)
        if not (self.underMouse() or self.isDown()):
            return None, ink
        if self._kind is CaptionKind.CLOSE:
            fill = QColor(THEME.error)
            return (fill.darker(115) if self.isDown() else fill), QColor(THEME.pill_text)
        fill = QColor(THEME.node_fill_hover)
        return (fill.darker(110) if self.isDown() else fill), ink
