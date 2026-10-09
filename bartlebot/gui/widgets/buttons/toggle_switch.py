"""``ToggleSwitch`` -- an on/off switch, for turning a whole feature on or off (#138).

A check box and a switch say different things, and the app needs both. A check box is an
option inside a form; a switch turns a feature on or off and takes effect at once.
Microsoft's and Apple's guidance both draw that line, and so does this app: the Quicksell
card's header holds Enable Quicksell, which decides whether every row under it means
anything at all, and drawing it as one more check box would make it look like one more
of those rows.

It is a ``QCheckBox`` underneath, deliberately. Every option surface reads and writes its
controls through ``option_widgets`` (``set_widget_value``, ``widget_value``,
``connect_widget``), and all three already know a check box: ``setChecked``, ``isChecked``
and ``toggled`` are the switch's API too, so nothing downstream needs a branch for it.
Only the drawing and the hit area are its own.

Painted rather than styled, and from ``THEME`` at paint time, so it holds no baked color
and needs no theme hook (``tests/gui/theming/test_theme_refresh_guard.py`` explains the exemption).

The state is carried by a glyph as well as by color -- a check mark on, a cross off --
so it reads the same to someone who cannot tell the accent from the off fill.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QCheckBox, QWidget

from bartlebot.gui.theming.theme import THEME

SWITCH_WIDTH_PX = 36
SWITCH_HEIGHT_PX = 20
_KNOB_PX = 16
_KNOB_INSET_PX = 2
_GLYPH_PX = 12
# How strongly a disabled switch is drawn: the same "present but not live" weight the
# option rows' disabled bodies read at.
_DISABLED_OPACITY = 0.45


class ToggleSwitch(QCheckBox):
    """A 36x20 switch: accent with the knob right and a check when on, a cross when off.

    Space toggles it, as it does any check box. It takes focus from the keyboard only
    (``TabFocus``), so the focus ring it draws is only ever there because someone tabbed
    to it -- a ring left behind by every mouse click would read as a selected state.

    The ring is drawn *on* the track, in the text color, rather than around it: a ring
    outside would need two pixels of margin on every side, and a 24px switch would make
    the header it sits in taller than every row beneath it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(SWITCH_WIDTH_PX, SWITCH_HEIGHT_PX)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width(), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt override
        """The whole switch, not the style's idea of where a check box's indicator is."""
        return self.rect().contains(pos)

    def _track(self) -> QRectF:
        return QRectF(0.5, 0.5, SWITCH_WIDTH_PX - 1, SWITCH_HEIGHT_PX - 1)

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        on = self.isChecked()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(_DISABLED_OPACITY)

        track = self._track()
        radius = track.height() / 2
        painter.setPen(QPen(QColor(THEME.accent if on else THEME.border), 1))
        painter.setBrush(QColor(THEME.accent if on else THEME.bg))
        painter.drawRoundedRect(track, radius, radius)
        if self.hasFocus():
            painter.setPen(QPen(QColor(THEME.text), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track, radius, radius)

        knob_x = track.right() - _KNOB_INSET_PX - _KNOB_PX if on else track.left() + _KNOB_INSET_PX
        knob = QRectF(knob_x, track.top() + (track.height() - _KNOB_PX) / 2, _KNOB_PX, _KNOB_PX)
        painter.setPen(QPen(QColor(THEME.accent if on else THEME.border), 1))
        painter.setBrush(QColor(THEME.pill_text))
        painter.drawEllipse(knob)

        # The glyph sits in the half the knob is not covering.
        glyph_x = track.left() + 4 if on else track.right() - 4 - _GLYPH_PX
        glyph = QRectF(glyph_x, track.top() + (track.height() - _GLYPH_PX) / 2, _GLYPH_PX, _GLYPH_PX)
        pen = QPen(QColor(THEME.pill_text if on else THEME.text_muted), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_check_path(glyph) if on else _cross_path(glyph))
        painter.end()


def _check_path(box: QRectF) -> QPainterPath:
    path = QPainterPath(QPointF(box.left() + box.width() * 0.18, box.top() + box.height() * 0.55))
    path.lineTo(QPointF(box.left() + box.width() * 0.42, box.top() + box.height() * 0.78))
    path.lineTo(QPointF(box.left() + box.width() * 0.84, box.top() + box.height() * 0.26))
    return path


def _cross_path(box: QRectF) -> QPainterPath:
    inset = box.width() * 0.24
    path = QPainterPath(QPointF(box.left() + inset, box.top() + inset))
    path.lineTo(QPointF(box.right() - inset, box.bottom() - inset))
    path.moveTo(QPointF(box.right() - inset, box.top() + inset))
    path.lineTo(QPointF(box.left() + inset, box.bottom() - inset))
    return path
