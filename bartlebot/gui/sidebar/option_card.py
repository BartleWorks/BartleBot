"""``OptionCard`` -- one folding group of bot options on Setup and Options (#138).

The two option surfaces draw a bot's options as a column of these rather than one flat
list: *This dungeon*, *Team*, *Quicksell* and *General* (``bartlebot.option_groups``
decides which option goes where). Folding the groups nobody is working in is what puts
a thirteen-option bot on a 1080p screen.

ANATOMY
-------

A bordered card with a header band over a page-colored body::

    ┌──────────────────────────────────────────────────────────┐
    │▌▾ Quicksell ⚠  pets kept · keeping 2 of 58  App settings ◉│  <- header band
    ├──────────────────────────────────────────────────────────┤
    │  ...the ordinary option rows, gutter and all...          │  <- body
    └──────────────────────────────────────────────────────────┘

* The frame is ``CardFrame``'s border and radius, 1px and 4px.
* The band is the surface color, and a rule separates it from the body while the
  card is expanded. The band is what makes each group, and its title, easy to find.
* The stripe at the band's left edge is the override bar's color, lit when any value
  in the card is the user's -- folded or not, which is Unity's rule for a component
  header with prefab overrides. A folded card is the one place an override can be
  out of sight, so the stripe is what stops it being forgotten.
* The fold mark is the 10px disclosure triangle every folding sidebar header draws,
  tinted ``text_muted`` the way a nested header's is; the title is semibold under the
  bold section heading above. Depth is said by tint and weight, never by a different
  glyph.
* A ``⚠`` follows the title while anything in the card has a warning, whether the card
  is folded or not -- the assignment card's mark, and for its reason: a fold that could
  hide a problem would make folding unsafe.
* While folded, the band carries a one-line summary of the card, warning parts in the
  warning color. It is hidden while expanded, so nothing is on screen twice.
* At the right end, whatever the surface adds: Setup puts Enable Quicksell's source
  caption and switch there.

The header has **no option gutter**. The rows keep theirs, so every revert and ``?``
still sits in one column, but a header holding only a title would carry fifty pixels of
empty gutter before it.

WHAT IT DOES NOT DO
-------------------

It holds no option state and builds no rows: the surface fills ``body_layout`` and
drives ``set_collapsed``, ``set_marked``, ``set_summary`` and ``set_warning``. A click on
the band asks; the surface answers. That keeps one owner for the fold state, which is
remembered across bots and shared by both surfaces.

Painted from ``THEME`` at paint time -- the frame, the band, the stripe and the rule --
so a theme switch needs nothing re-derived but the fold button's glyph.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.sidebar.sidebar_section import DISCLOSURE_PX, disclosure_pixmap
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.option_groups import SummaryPart, summary_text

#: The card's outline and corner, ``CardFrame``'s.
CARD_RADIUS_PX = 4
#: The band's padding: the stripe plus a gap on the left, and the row inset elsewhere.
STRIPE_PX = 3
HEADER_MARGINS = (STRIPE_PX + 6, 4, 6, 4)
#: The body's inset on every side, and the gap between its rows.
BODY_MARGIN_PX = 6
BODY_SPACING_PX = 6
#: Between the fold mark and the title, and between the header's items.
FOLD_GAP_PX = 6
WARNING_MARK = "⚠"


class _FoldButton(ThemedMixin, QPushButton):
    """The fold mark and the title, as one button the keyboard can reach.

    Flat, so it reads as the band's heading rather than as a button on it. Focus comes
    from the keyboard only, and Enter or Space folds the card, as a click on the band
    does; the tooltip names what a press does next.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._expanded = True
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setIconSize(QSize(DISCLOSURE_PX, DISCLOSURE_PX))
        font = THEME.ui_font(THEME.font_body_px)
        font.setWeight(font.Weight.DemiBold)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.refresh_theme_styles()

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.refresh_theme_styles()

    def take_modifiers(self) -> Qt.KeyboardModifier:
        """The modifiers held for the click being handled, cleared once read.

        Read off the button's own event rather than ``QApplication.keyboardModifiers()``,
        which reports the last input event anywhere -- a Ctrl+click on another card a
        moment ago, say.
        """
        held, self._modifiers = self._modifiers, Qt.KeyboardModifier.NoModifier
        return held

    _modifiers = Qt.KeyboardModifier.NoModifier

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._modifiers = event.modifiers()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._modifiers = event.modifiers()
        super().keyPressEvent(event)

    def apply_theme_styles(self) -> None:
        # Nested: the muted tint is what says this fold sits inside the section's.
        self.setIcon(QIcon(disclosure_pixmap(self._expanded, tint=THEME.text_muted)))
        verb = "Collapse" if self._expanded else "Expand"
        self.setToolTip(f"{verb} {self.text()}")
        set_style_sheet(
            self,
            f"QPushButton {{ background: transparent; border: none; padding: 0; color: {THEME.text}; "
            f"text-align: left; }}",
        )


class _SummaryLine(QWidget):
    """The folded card's one-line summary: parts joined by ``·``, warnings in color.

    Painted rather than a ``QLabel`` because it has to do two things a label cannot do
    together: elide to whatever width is left, and color one part differently from the
    rest. Elided rich text is not something ``QLabel`` offers.

    Like ``ElidedLabel``, its minimum width is small and its size hint is the full text,
    so it takes what room the band has and degrades to an ellipsis when there is none.
    The whole line is the tooltip.
    """

    _MIN_WIDTH = 24
    _SEPARATOR = " · "

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parts: list[SummaryPart] = []
        self._shown = False
        self.setFont(THEME.ui_font(THEME.font_subtext_px))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    @property
    def parts(self) -> list[SummaryPart]:
        return list(self._parts)

    @property
    def shown(self) -> bool:
        return self._shown

    def set_parts(self, parts: list[SummaryPart]) -> None:
        if parts == self._parts:
            return
        self._parts = list(parts)
        self._apply_tooltip()
        self.updateGeometry()
        self.update()

    def set_shown(self, shown: bool) -> None:
        """Draw the summary or leave its space blank.

        Never hidden outright: it is the band's stretch, and without it whatever the
        surface put at the right end would slide left to sit beside the title.
        """
        if shown == self._shown:
            return
        self._shown = shown
        self._apply_tooltip()
        self.update()

    def _apply_tooltip(self) -> None:
        self.setToolTip(summary_text(self._parts) if self._shown else "")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(summary_text(self._parts)), metrics.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._MIN_WIDTH, QFontMetrics(self.font()).height())

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        if not self._shown:
            return
        metrics = QFontMetrics(self.font())
        painter = QPainter(self)
        x, width = 0, self.width()
        baseline = (self.height() + metrics.ascent() - metrics.descent()) // 2
        muted, warning = QColor(THEME.text_muted), QColor(THEME.warning)
        for index, part in enumerate(self._parts):
            pieces = [(self._SEPARATOR, muted)] if index else []
            pieces.append((part.text, warning if part.warning else muted))
            for text, color in pieces:
                room = width - x
                if room <= 0:
                    painter.end()
                    return
                shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, room)
                painter.setPen(color)
                painter.drawText(x, baseline, shown)
                if shown != text:
                    painter.end()
                    return
                x += metrics.horizontalAdvance(text)
        painter.end()


class _CardHeader(QWidget):
    """The band: a click anywhere on it that no child control took folds the card.

    Activation is on release and only for a pointer that stayed put, as on every card
    in the app, so a press-and-sweep across the band does nothing.
    """

    def __init__(self, card: OptionCard) -> None:
        super().__init__(card)
        self._card = card
        self._press: QPoint | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        pressed, self._press = self._press, None
        if (
            event.button() == Qt.MouseButton.LeftButton
            and pressed is not None
            and self.rect().contains(event.position().toPoint())
            and (event.position().toPoint() - pressed).manhattanLength() < QApplication.startDragDistance()
        ):
            self._card.request_fold(event.modifiers())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Reached by a right-click on the band, and by Shift+F10 or the Menu key while
        # the fold button has focus: a button does not take the event, so Qt hands it
        # up to here.
        self._card.menu_requested.emit(event.globalPos())
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        """The band, the stripe, and the rule under the band while the card is open.

        The band's top corners follow the card's, less its 1px border; folded, its
        bottom corners do too, since it is then the whole card.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = CARD_RADIUS_PX - 1
        expanded = not self._card.is_collapsed

        band = _rounded(rect, radius, round_bottom=not expanded)
        painter.fillPath(band, QColor(THEME.surface))
        if self._card.is_marked:
            painter.save()
            painter.setClipPath(band)
            painter.fillRect(QRectF(rect.left(), rect.top(), STRIPE_PX, rect.height()), QColor(THEME.accent))
            painter.restore()
        if expanded:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(QColor(THEME.border), 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


def _rounded(rect: QRectF, radius: float, *, round_bottom: bool) -> QPainterPath:
    """*rect* with its top corners rounded, and its bottom corners too if asked."""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if not round_bottom:
        square = QPainterPath()
        square.addRect(QRectF(rect.left(), rect.center().y(), rect.width(), rect.height() / 2))
        path = path.united(square)
    return path


class OptionCard(QFrame):
    """A folding card of bot options: a header band and a body the surface fills.

    The card never folds itself. A click on the band, or on its title, emits
    ``fold_requested`` with whether the user held Ctrl, and the surface -- which owns
    the remembered fold state for every card -- calls ``set_collapsed`` back.
    """

    #: The user asked to fold or unfold this card; *solo* is True for Ctrl+click,
    #: which keeps this one open and collapses the rest (Blender's panels).
    fold_requested = pyqtSignal(bool)
    #: A right-click on the band, or the keyboard's menu key, at this global point.
    menu_requested = pyqtSignal(QPoint)

    def __init__(self, group_id: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group_id = group_id
        self._title = title
        self._collapsed = False
        self._marked = False
        # Preferred, not Maximum: a folded-out description makes the body height-for-
        # width, and Maximum caps the card at a size hint computed at no particular
        # width -- the trap ``SidebarSection._expanded_v_policy`` records.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        # Inside the 1px border the card paints, so neither the band nor the body
        # covers it.
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self._header = _CardHeader(self)
        head = QHBoxLayout(self._header)
        head.setContentsMargins(*HEADER_MARGINS)
        head.setSpacing(FOLD_GAP_PX)

        self._fold_btn = _FoldButton(title)
        self._fold_btn.clicked.connect(lambda: self.request_fold(self._fold_btn.take_modifiers()))
        head.addWidget(self._fold_btn, 0)

        self._warning = ThemedTextLabel(WARNING_MARK, color="warning")
        head.addWidget(self._warning, 0)

        self._summary = _SummaryLine()
        head.addWidget(self._summary, 1)
        self._head_layout = head

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(BODY_MARGIN_PX, BODY_MARGIN_PX, BODY_MARGIN_PX, BODY_MARGIN_PX)
        self._body_layout.setSpacing(BODY_SPACING_PX)

        outer.addWidget(self._header)
        outer.addWidget(self._body)

        # Visibility only once everything is parented: see "Never show a widget before
        # it has a parent" in gui/CLAUDE.md.
        self._warning.setVisible(False)

    # ------------------------------------------------------------------
    # What the surface reads
    # ------------------------------------------------------------------

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def title(self) -> str:
        return self._title

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    @property
    def is_marked(self) -> bool:
        """Whether the stripe is lit: a value somewhere in this card is the user's."""
        return self._marked

    @property
    def header(self) -> QWidget:
        """The band. Public so a test can click it as a user would."""
        return self._header

    @property
    def fold_button(self) -> QPushButton:
        return self._fold_btn

    @property
    def body(self) -> QWidget:
        return self._body

    @property
    def body_layout(self) -> QVBoxLayout:
        """Where the surface puts the card's rows."""
        return self._body_layout

    @property
    def summary_parts(self) -> list[SummaryPart]:
        return self._summary.parts

    @property
    def summary_shown(self) -> bool:
        """Whether the band is drawing its summary -- only while the card is folded."""
        return self._summary.shown

    @property
    def warning(self) -> str | None:
        """The warning the mark's tooltip carries, or None while there is none."""
        return self._warning.toolTip() if self._warning.isVisibleTo(self) else None

    # ------------------------------------------------------------------
    # What the surface sets
    # ------------------------------------------------------------------

    def add_header_widget(self, widget: QWidget, *, gap_before: int = 0) -> None:
        """Append *widget* to the right end of the band -- a caption, a switch."""
        if gap_before:
            self._head_layout.addSpacing(gap_before)
        self._head_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._summary.set_shown(collapsed)
        self._fold_btn.set_expanded(not collapsed)
        self._header.update()

    def set_marked(self, marked: bool) -> None:
        if marked == self._marked:
            return
        self._marked = marked
        self._header.update()

    def set_summary(self, parts: list[SummaryPart]) -> None:
        self._summary.set_parts(parts)

    def set_warning(self, text: str | None) -> None:
        self._warning.setToolTip(text or "")
        self._warning.setVisible(bool(text))

    def request_fold(self, modifiers) -> None:
        """Ask the surface to fold or unfold this card, as a click on the band does."""
        self.fold_requested.emit(bool(modifiers & Qt.KeyboardModifier.ControlModifier))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(THEME.border), 1))
        painter.setBrush(QColor(THEME.bg))
        painter.drawRoundedRect(rect, CARD_RADIUS_PX, CARD_RADIUS_PX)
        painter.end()
