"""ElidedLabel — a single-line QLabel that truncates instead of widening its parent."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import QLabel, QSizePolicy

from bartlebot.gui.theme import THEME


def apply_muted_color(lbl: QLabel) -> None:
    """Set a label's text to ``THEME.text_muted`` via the palette, not a stylesheet.

    :class:`ElidedLabel` paints its own text, and a QSS ``color:`` rule is
    resolved by QStyleSheetStyle at draw time without ever reaching
    ``widget.palette()`` — so a stylesheet would simply be ignored here.  Call
    this again from ``changeEvent`` on ``QEvent.Type.StyleChange``, the same as
    any other theme-dependent styling.
    """
    pal = lbl.palette()
    pal.setColor(lbl.foregroundRole(), QColor(THEME.text_muted))
    lbl.setPalette(pal)


class ElidedLabel(QLabel):
    """QLabel that renders "some very long te…" rather than demanding the width.

    A plain QLabel reports its *full* untruncated text width as both its size
    hint and its minimum size hint, and ``QScrollArea(widgetResizable=True)``
    honours that minimum — so a single long summary line inside a sidebar forces
    a horizontal scrollbar across the whole panel.  The combat tab's per-duel
    assignment summaries were asking for ~1157 px inside a sidebar whose minimum
    is 220.

    Two things are needed to actually fix that, not one:

    * a small ``minimumSizeHint``.  Qt's ``qSmartMinSize`` consults
      ``minimumSizeHint`` *alone* for any policy carrying ``ShrinkFlag`` (which
      ``Preferred`` does), so overriding it is what frees the layout from the
      text width.  ``setMinimumWidth(0)`` does not work: a zero minimum means
      "fall back to minimumSizeHint", which is the full text width again.
    * eliding at paint time, so text that no longer fits degrades to an ellipsis
      instead of being silently clipped mid-glyph.

    ``sizeHint`` is deliberately left as the full text width, and the policy
    stays ``Preferred``: the label should still be *given* its natural width
    whenever the layout has room, and only shrink under pressure.  (An
    ``Ignored`` policy also zeroes the minimum, but it discards the size hint
    too, so a label with no stretch factor collapses to nothing even in a wide
    panel.)

    The untruncated text is always available as the tooltip.
    """

    _MIN_WIDTH = 24  # enough for "…" plus a character or two

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._full_text = text or ""
        # The full string stays reachable even when the visible form is clipped.
        self.setToolTip(self._full_text)
        super().setText(self._full_text)
        self.update()

    def full_text(self) -> str:
        """The complete, untruncated text — what ``text()`` would return on a plain QLabel."""
        return self._full_text

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt naming
        return QSize(self._MIN_WIDTH, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 — Qt naming
        painter = QPainter(self)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), int(self.alignment()), elided)
        painter.end()
