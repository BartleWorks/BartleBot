"""A plain-text editor that is as tall as its text, between a floor and a ceiling.

Qt has no such widget, and neither does Win32 or AppKit: a control whose height
depends on its content *and* its width is ``heightForWidth``, which classic
top-down layout systems support badly.  The pattern is nonetheless the standard
one for a prose field — every chat composer grows to a cap, Unity's inspector
declares it outright as ``[TextArea(minLines, maxLines)]``, and the web finally
got it natively as CSS ``field-sizing: content`` in 2024.  So it is a recipe
rather than a widget, and this is the recipe.

``QTextEdit`` rather than ``QPlainTextEdit`` because its document lays out in
*pixels*: ``documentSizeChanged`` reports the real wrapped height, on an edit and
on a resize alike.  ``QPlainTextDocumentLayout`` reports blocks instead, which
means counting wrapped lines by hand.  Wrapping is what makes the second signal
matter — the shipped bot descriptions run to a median of 2 lines at a 400px
sidebar and 3 at the 300px default, and a 20-line worst case at the narrow end,
so a height measured once at one width is wrong at the other.

Both bounds are in **lines**, resolved to pixels through the widget's own font at
measure time.  Set that font on the widget, never in a stylesheet: a stylesheet
``font-size`` never reaches ``QWidget.font()``, so the clamp would be computed at
one size and the text drawn at another.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QAbstractScrollArea, QScrollArea, QSizePolicy, QTextEdit, QWidget


class GrowingTextEdit(QTextEdit):
    """A plain-text editor sized to its content, clamped to ``[min_lines, max_lines]``.

    Reports the height it wants through ``sizeHint`` and calls ``updateGeometry``
    when that changes, rather than calling ``setFixedHeight`` on itself — the
    layout stays the one thing deciding geometry, which is what lets the widget
    sit in a ``QScrollArea`` page without fighting it.
    """

    def __init__(self, *, min_lines: int = 2, max_lines: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_lines = max(1, min_lines)
        self._max_lines = max(self._min_lines, max_lines)

        # Plain text in, plain text out: callers store this in a bot file, and a
        # paste carrying formatting would arrive as HTML through toHtml-shaped APIs.
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # As-needed, not off: at the ceiling the widget stops growing and the text
        # has to keep going somewhere.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._measuring = False
        # Restarted by every step of a growth — the document relayout, then the
        # resize the layout grants it — so it fires once, after the last of them.
        # Parented to self, so it dies with the widget rather than firing into a
        # destroyed one.
        self._catch_up = QTimer(self)
        self._catch_up.setSingleShot(True)
        self._catch_up.timeout.connect(self._keep_caret_in_view)
        self.document().documentLayout().documentSizeChanged.connect(self._on_document_resized)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.preferred_height())

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self._line_height(self._min_lines))

    def preferred_height(self) -> int:
        """The height the text wants, clamped to the line bounds."""
        self._lay_out_document()
        content = int(self.document().size().height()) + self._chrome()
        return max(self._line_height(self._min_lines), min(content, self._line_height(self._max_lines)))

    @property
    def line_bounds(self) -> tuple[int, int]:
        """``(min_lines, max_lines)`` — the clamp, in lines."""
        return self._min_lines, self._max_lines

    def _chrome(self) -> int:
        """Everything above the document: border and stylesheet padding, both ends.

        ``frameWidth`` already carries a style sheet's border *and* padding, and it
        does so as soon as the sheet is set, so this is right before the first show
        as well as after — which matters, because the section measures itself while
        it is still being built.
        """
        return 2 * self.frameWidth()

    def _text_width(self) -> int:
        """The width the document has to wrap against.

        The viewport once there is one — it is narrower than the frame whenever the
        ceiling has put a scrollbar there.  Before the first show its geometry is
        still the default, which is wider than the widget, and that is the tell.
        """
        available = self.width() - self._chrome()
        viewport = self.viewport().width()
        return max(0, viewport if viewport <= available else available)

    def _lay_out_document(self) -> int | None:
        """Give the document a wrapping width if Qt has not yet.

        ``QTextEdit`` sets one from its viewport on the first resize *after* the
        widget is shown, and until then ``document().size()`` is empty — so a
        section that loads a bot into an editor it has not displayed yet would
        measure every description as the floor.  Setting it here costs nothing when
        Qt already agrees, and is overwritten by Qt the moment it does not.
        """
        if self._measuring:
            return None
        width = self._text_width()
        document = self.document()
        if width <= 0 or abs(document.textWidth() - width) < 0.5:
            return None
        self._measuring = True
        try:
            document.setTextWidth(width)
        finally:
            self._measuring = False
        return width

    def _line_height(self, lines: int) -> int:
        fm = QFontMetrics(self.font())
        return lines * fm.lineSpacing() + int(2 * self.document().documentMargin()) + self._chrome()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_document_resized(self, *_args) -> None:
        if self._measuring:
            return
        self.updateGeometry()
        self._schedule_caret_catch_up()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """The moment the growth is real.

        ``documentSizeChanged`` reports a height the widget only *wants*; the caret
        does not move until a layout has granted it, and scrolling to a caret that
        has not moved yet scrolls to the wrong place — or, in the growing case, to
        nowhere at all.
        """
        super().resizeEvent(event)
        if event.size().height() != event.oldSize().height():
            self._schedule_caret_catch_up()

    def setFont(self, font) -> None:  # noqa: N802 - Qt override
        super().setFont(font)
        self.updateGeometry()

    def _schedule_caret_catch_up(self) -> None:
        """Ask for a scroll-to-caret once this round of layout has settled.

        Not immediately: ``documentSizeChanged`` reports a height the widget only
        *wants*, and the enclosing page has not grown yet — scrolling then asks a
        scroll bar with no range to move, which is silently nothing.
        """
        if self.hasFocus():
            self._catch_up.start(0)

    def _keep_caret_in_view(self) -> None:
        """Scroll an enclosing page so a growing field does not push the caret off it.

        Growth here is top-anchored — unlike a chat composer, which expands upward
        into space above the caret — so the line being typed moves *down*, and past
        a few lines it leaves the sidebar's viewport with nothing to say so.
        """
        if not self.hasFocus():
            return
        self.ensureCursorVisible()
        area = self._enclosing_scroll_area()
        if area is None or area.widget() is None:
            return
        point = self.viewport().mapTo(area.widget(), self.cursorRect().bottomLeft())
        area.ensureVisible(point.x(), point.y(), 0, self.fontMetrics().lineSpacing())

    def _enclosing_scroll_area(self) -> QScrollArea | None:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None
