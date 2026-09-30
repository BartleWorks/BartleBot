"""Labels that truncate instead of widening their parent.

:class:`ElidedLabel` is one line, elided at its end. :class:`WrappingElidedLabel`
word-wraps, and elides only a single word too wide for a line of its own.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QEvent, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import QLabel, QSizePolicy

from bartlebot.gui.theme import THEME

#: Taller than any label, so a wrap measurement is never cut short by its own rect.
_UNBOUNDED_HEIGHT = 1 << 20

_WHITESPACE = re.compile(r"(\s+)")


def apply_muted_color(lbl: QLabel) -> None:
    """Set a label's text to ``THEME.text_muted`` via the palette, not a stylesheet.

    :class:`ElidedLabel` paints its own text, and a QSS ``color:`` rule is
    resolved by QStyleSheetStyle at draw time without ever reaching
    ``widget.palette()`` — so a stylesheet would simply be ignored here.  Call
    this again from ``changeEvent`` on ``QEvent.Type.StyleChange``, the same as
    any other theme-dependent styling.
    """
    if isinstance(lbl, ElidedLabel):
        lbl.set_text_color(THEME.text_muted)
        return
    pal = lbl.palette()
    pal.setColor(lbl.foregroundRole(), QColor(THEME.text_muted))
    lbl.setPalette(pal)


class ElidedLabel(QLabel):
    """QLabel that renders "some very long te…" rather than demanding the width.

    A plain QLabel reports its *full* untruncated text width as both its size
    hint and its minimum size hint, and ``QScrollArea(widgetResizable=True)``
    honors that minimum — so a single long summary line inside a sidebar forces
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

    *elide_mode* chooses which end is sacrificed.  The default ``ElideRight``
    suits text whose head identifies it; pass ``ElideLeft`` for values like a
    zone path, where the tail (``…/WC_Streets/Interior``) is what tells one
    apart from another and the shared prefix is the disposable part.
    """

    _MIN_WIDTH = 24  # enough for "…" plus a character or two

    def __init__(
        self,
        text: str = "",
        parent=None,
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ):
        super().__init__(parent)
        self._full_text = ""
        self._elide_mode = elide_mode
        self._text_color: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._full_text = text or ""
        # The full string stays reachable even when the visible form is clipped.
        self.setToolTip(self._full_text)
        super().setText(self._full_text)
        self.update()

    def set_text_color(self, color: str) -> None:
        """Paint the text in *color*, and keep painting it in that color across a theme switch.

        Held rather than just written to the palette, because a palette written from
        outside does not survive the switch. ``QApplication.setStyleSheet`` repolishes
        every widget, and the stylesheet style's repolish of *this* label restores the
        palette it saved at the previous polish -- the old theme's color. The card that
        recolored the label is usually repolished first, so its write is undone and the
        text stays in the old theme until something restyles the card again (a hover).
        The label's own ``StyleChange`` arrives after its repolish, so re-applying from
        there is the one moment the color sticks whatever order Qt visits them in.
        """
        self._text_color = color
        self._apply_text_color()

    def _apply_text_color(self) -> None:
        if self._text_color is None:
            return
        color = QColor(self._text_color)
        palette = self.palette()
        if palette.color(self.foregroundRole()) == color:
            return
        palette.setColor(self.foregroundRole(), color)
        self.setPalette(palette)

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_text_color()

    def full_text(self) -> str:
        """The complete, untruncated text — what ``text()`` would return on a plain QLabel."""
        return self._full_text

    def elide_mode(self) -> Qt.TextElideMode:
        """Which end of the text is dropped when it does not fit."""
        return self._elide_mode

    def elided_text(self, width: int) -> str:
        """The visible form of the text at *width* pixels.

        Split out of ``paintEvent`` so the truncation rule can be exercised
        directly rather than inferred from a rendered pixmap.
        """
        return QFontMetrics(self.font()).elidedText(self._full_text, self._elide_mode, width)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt naming
        return QSize(self._MIN_WIDTH, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 — Qt naming
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), int(self.alignment()), self.elided_text(self.contentsRect().width()))
        painter.end()


def elide_long_words(text: str, metrics: QFontMetrics, width: int) -> str:
    """*text* with each word wider than *width* on its own elided to fit it, and nothing else touched.

    Word wrap breaks only between words, so a word longer than a line is the one thing
    it cannot place: a ``QLabel`` then asks for that word's width as its minimum, and
    the column it sits in grows to match. Eliding just that word keeps every line
    within *width* and leaves text that already wraps exactly as it was. The
    whitespace is kept as it was too, so line breaks and double spaces survive.

    A *width* of zero or less means the label has not been laid out yet, and the text
    comes back whole rather than as a column of ellipses.
    """
    if width <= 0:
        return text
    parts = _WHITESPACE.split(text)
    for i, part in enumerate(parts):
        if part and not part.isspace() and metrics.horizontalAdvance(part) > width:
            # Too narrow for even a character and the ellipsis: the ellipsis alone still
            # says something was here, where elidedText's empty string would not.
            parts[i] = metrics.elidedText(part, Qt.TextElideMode.ElideRight, width) or "…"
    return "".join(parts)


class WrappingElidedLabel(QLabel):
    """A word-wrapped label whose minimum width is not its longest word.

    A word-wrapped ``QLabel`` can wrap a sentence to any width, but not a word: its
    minimum width is its longest word, so one unbroken name -- a strategy called
    ``PoseidonBossDuel_v2``, a spell token in a DSL preview -- widens the card, and
    then the list the card is in (#118). This wraps as ``QLabel`` does and elides only
    the words that cannot fit a line on their own (:func:`elide_long_words`).

    Plain text only, and three things differ from a ``QLabel``:

    * ``text()`` is the full text, as on :class:`ElidedLabel`, so a caller reading the
      label back never sees an ellipsis. What is on screen is :meth:`shown_text`.
    * ``heightForWidth`` measures the text as it would be shown at that width, not as
      it is shown now. A list that sizes its rows before the card is laid out asks
      at a width the label has not had yet, and measuring the current text would give
      the row the height of the wrong wrap.
    * The full text is the tooltip while a word is elided, ahead of any tooltip the
      caller set; with nothing elided, only the caller's shows.

    Color and font come from the stylesheet as on any ``QLabel`` -- this paints nothing
    of its own, unlike :class:`ElidedLabel`.
    """

    _MIN_WIDTH = ElidedLabel._MIN_WIDTH

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self._own_tooltip = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 — Qt naming
        self._full_text = text or ""
        self._reflow()
        # The shown text can come out the same while the full text changed, and the
        # height at another width depends on the full one.
        self.updateGeometry()

    def text(self) -> str:
        """The complete text, whatever is elided on screen."""
        return self._full_text

    def full_text(self) -> str:
        """The complete, unelided text — the same as ``text()``, named as on :class:`ElidedLabel`."""
        return self._full_text

    def shown_text(self) -> str:
        """What is on screen at the current width."""
        return super().text()

    def is_elided(self) -> bool:
        return self.shown_text() != self._full_text

    def elided_text(self, width: int) -> str:
        """The text as it would be shown in *width* pixels of content."""
        return elide_long_words(self._full_text, self.fontMetrics(), width)

    def setToolTip(self, tip: str) -> None:  # noqa: N802 — Qt naming
        self._own_tooltip = tip or ""
        self._apply_tooltip()

    def _apply_tooltip(self) -> None:
        parts = [self._full_text] if self.is_elided() else []
        if self._own_tooltip:
            parts.append(self._own_tooltip)
        super().setToolTip("\n\n".join(parts))

    def _content_width(self, width: int) -> int:
        margins = self.contentsMargins()
        return width - margins.left() - margins.right() - 2 * self.margin()

    def _reflow(self) -> None:
        shown = self.elided_text(self._content_width(self.width()))
        if shown != super().text():
            super().setText(shown)
        self._apply_tooltip()

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — Qt naming
        inner = max(self._content_width(width), 1)
        # Horizontal alignment only, as QLabel measures: centring vertically in a rect this
        # tall rounds the text's height up a pixel.
        horizontal = self.alignment().value & Qt.AlignmentFlag.AlignHorizontal_Mask.value
        flags = horizontal | Qt.TextFlag.TextWordWrap.value
        rect = self.fontMetrics().boundingRect(QRect(0, 0, inner, _UNBOUNDED_HEIGHT), flags, self.elided_text(inner))
        margins = self.contentsMargins()
        return rect.height() + margins.top() + margins.bottom() + 2 * self.margin()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt naming
        # QLabel's own minimum width is its longest word, which is the whole problem.
        return QSize(self._MIN_WIDTH, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        super().resizeEvent(event)
        self._reflow()

    def changeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        super().changeEvent(event)
        # A stylesheet's font-size lands after construction, and the words that fit change with it.
        if event.type() == QEvent.Type.FontChange:
            self._reflow()
            self.updateGeometry()
