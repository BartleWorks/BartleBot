"""A searchable, sectioned, multi-select popup — and the button that opens it.

Qt's native ``QMenu`` is drawn by the platform theme, so it ignores the app
stylesheet almost entirely: ``THEME`` never reaches it, and ``addSection()``
degrades to a hairline with small text. That is fine for a short list of commands
and wrong for a picker with sections, a filter box and per-row state, which is why
this is a plain widget rather than a menu.

It is a ``Qt.WindowType.Popup``, which buys three things a ``QDialog`` would not:

* click-outside-to-dismiss and Esc, handled by Qt;
* the app stylesheet, because it is an ordinary styled widget;
* no ``exec()``, so it cannot trip the qasync task re-entrancy rule in CLAUDE.md.

Being a popup *window* also puts it outside the sidebar's widget tree, so the
native-window transient-parent bug that ``menu_utils`` exists to work around does
not apply here.

It can also be built ``embedded``, as an ordinary child widget with the same header,
search and rows, for a question that has to be answered rather than dismissed: the
Combat tab's fights step, where a click elsewhere used to drop the card being added
(#272). The dialog around it then owns Esc and the commit.

``PickerRow.description`` is supported but optional, and the tag picker does not
use it: a second line of muted text under every name doubled the row height, made
the heights ragged, and read as an underline once the list was scrolling. Rows are
one line unless a caller has something to say that the name does not.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import set_style_sheet
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.widgets.inputs.search_field import SearchLineEdit
from bartlebot.gui.widgets.layout.scroll_gutter import reserve_scrollbar_gutter

POPUP_WIDTH = 400

#: Tallest a popup may be even with room to spare. The bot-tag vocabulary is split
#: across two buttons, so nothing offered today needs the 560 this was before.
POPUP_MAX_HEIGHT = 420

#: Shortest a popup may be shrunk to when neither direction can hold it whole. Below
#: about this the search box and a couple of rows stop fitting and the scroll area is
#: all that is left, which is worse than overhanging a screen edge.
POPUP_MIN_HEIGHT = 160

#: The dropdown caret drawn on every :class:`PickerButton`, in px.
CARET_W = 7
CARET_H = 4
CARET_PAD = 6  # gap between the label and the caret


@dataclass(frozen=True)
class PickerRow:
    """One selectable row.

    ``key`` is the stable identity the popup reports; ``label`` is what is shown.
    They differ for callers whose display text is localized or decorated.

    ``meta`` is the right-aligned slot. Give it a unit — "49 bots", not "49" — since
    a bare number beside a name reads as ambiguous until you work out what it counts.

    ``dim`` renders the row as present-but-empty. The library uses it for a tag no
    bot carries: dropping the row entirely reads as a bug, while a dimmed row with
    "0 bots" is an answer.

    ``icon`` is drawn where the dot would be, and replaces it: a row with artwork does not
    also need a color swatch, and is drawn :data:`ROW_ICON_PX` square. Left out of equality
    and hashing, since an icon is neither.
    """

    key: str
    label: str
    description: str = ""
    meta: str = ""
    accent: str | None = None  # colored dot; None draws no dot
    dim: bool = False
    icon: QIcon | None = field(default=None, compare=False)


Section = tuple[str, list[PickerRow]]


def filter_sections(sections: list[Section], query: str, *, broad: bool = False) -> list[Section]:
    """Narrow *sections* to rows matching *query*, dropping sections left empty.

    Matches the description too, for callers that set one — free when they do not,
    since an empty description matches no non-empty query.

    ``broad`` is for a picker whose rows are only told apart by their section and
    ``meta`` — the zone picker, where a zone's world is its section and its id its
    meta. The query splits on whitespace and every term has to match somewhere in the
    row's label, description or meta, or its section's title: "kroko entr" finds
    Entrance Hall under Krokotopia, and a world's name alone keeps its whole section.
    Off by default, since a tag picker's meta is a count ("49 bots") that every row
    would match.

    Pure and module level, so the matching can be tested without a QApplication.
    """
    if broad:
        terms = query.casefold().split()
        if not terms:
            return list(sections)
        out: list[Section] = []
        for title, rows in sections:
            heading = title.casefold()
            hits = [
                r
                for r in rows
                if all(
                    t in heading or t in r.label.casefold() or t in r.description.casefold() or t in r.meta.casefold()
                    for t in terms
                )
            ]
            if hits:
                out.append((title, hits))
        return out

    q = query.strip().casefold()
    if not q:
        return list(sections)
    out = []
    for title, rows in sections:
        hits = [r for r in rows if q in r.label.casefold() or q in r.description.casefold()]
        if hits:
            out.append((title, hits))
    return out


def summarize(labels: list[str], placeholder: str, *, limit: int = 2) -> str:
    """Anchor-button text for a selection: names while short, a count once long.

    A button that grows with its selection reflows the form around it, so past
    *limit* it collapses to "3 selected" and stops changing width.
    """
    if not labels:
        return placeholder
    if len(labels) <= limit:
        return ", ".join(labels)
    return f"{len(labels)} selected"


def selection_caption(count: int) -> str:
    """The popup header's right-hand status text."""
    return "" if count == 0 else f"{count} selected"


def apply_toggle(selected: set[str], key: str, checked: bool, *, single_select: bool = False) -> set[str]:
    """The selection after the row *key* is toggled to *checked*.

    Single-select has no "off": the row clicked is the selection, including the one already
    chosen, whose click only closes the popup. Pure, so it can be tested without a QApplication.
    """
    if single_select:
        return {key}
    return selected | {key} if checked else selected - {key}


#: How long after a popup closes a click on its own button is treated as the click
#: that dismissed it, rather than as a request to open it again.
REOPEN_GUARD_SECONDS = 0.20


def should_open(now: float, closed_at: float | None, *, guard: float = REOPEN_GUARD_SECONDS) -> bool:
    """Whether a click on the anchor button should open the popup.

    Clicking the button while the popup is open dismisses it — Qt closes a
    ``Qt.Popup`` on any press outside it — and then delivers the press to the button
    underneath, which promptly opens a second one. Qt's own combo boxes solve this
    the same way: ignore a click that lands within a moment of the close.
    """
    return closed_at is None or (now - closed_at) > guard


def popup_position(anchor: QRect, size: QSize, avail: QRect) -> QPoint:
    """Where to put a popup of *size* opened from *anchor*, both in screen coordinates.

    Pure and module level so the geometry can be tested without a screen — which is
    the only way to check the 4K case from a 1080p machine and vice versa.

    Two rules, in this order:

    * **Right edges line up** with the anchor's, whether or not a left-aligned popup
      would have fit. Aligning left and flipping right only on overflow means the
      popup jumps sides as the window is resized, or between two machines, for no
      reason the user can see; GitHub's own pickers right-align unconditionally, and a
      placement that never moves is easier to aim at than one that is locally optimal.
      The popup is wider than any button that opens it, so left alignment would in
      practice overflow anyway.

      Where right alignment would run off the left of *avail*, the popup flips to
      **left** edges flush instead. That is not the flip-flop above: it fires only
      where the alternative is a popup with no edge in common with its button at all.
      One edge always meets the anchor, which is the property a second call site needs
      — the sidebars are 300px minimum against a 400px popup, so a picker anchored in
      the *left* one right-aligns to a negative x, and a screen clamp would slide it
      until it pointed at nothing. That is the bug this function was written to fix,
      one edge over.
    * **Prefer opening downward**, flipping above the anchor only when the popup
      does not fit below. A 1080p screen flips where a 4K one does not; both are
      correct, and neither should also drift sideways.

    Clamping to *avail* is the last resort, for a popup wider or taller than either
    alignment can satisfy — a screen edge is the one thing allowed to win.
    """
    max_x = avail.left() + max(0, avail.width() - size.width())
    x = anchor.left() + anchor.width() - size.width()  # right edges flush
    if x < avail.left() and anchor.left() <= max_x:
        x = anchor.left()  # left edges flush
    x = max(avail.left(), min(x, max_x))

    max_y = avail.top() + max(0, avail.height() - size.height())
    y = anchor.top() + anchor.height()
    if y > max_y:
        above = anchor.top() - size.height()
        y = above if above >= avail.top() else max_y
    y = max(avail.top(), min(y, max_y))

    return QPoint(x, y)


def popup_height(anchor: QRect, ideal: int, avail: QRect, *, minimum: int = POPUP_MIN_HEIGHT) -> int:
    """How tall a popup opened from *anchor* may actually be.

    ``popup_position`` picks a *direction* at a fixed size and clamps to the screen
    when neither direction fits. Clamping slides the popup until it no longer meets
    the button, which is the failure this shortens the popup to avoid: a list that
    is already scrolling loses nothing by scrolling two rows sooner, and Qt's own
    ``QMenu`` shortens itself for the same reason.

    The bot library is the case that needs it. Its top bar is the top of the *bottom*
    splitter pane (``main_window``'s ``_outer_splitter``), so on a 1080p screen the
    button sits low enough that a full-height popup fits in neither direction, and
    where it opens is a function of where the user last dragged the splitter.

    Never shrinks below *minimum* — past that the header and search box stop fitting
    and overhanging a screen edge is the lesser evil. Pure, and module level, for the
    same reason as :func:`popup_position`.
    """
    # top + height, not QRect.bottom(), which is inclusive and one px short.
    below = (avail.top() + avail.height()) - (anchor.top() + anchor.height())
    above = anchor.top() - avail.top()
    return max(minimum, min(ideal, max(below, above)))


ROW_HEIGHT = 26
_BOX = 13  # checkbox indicator, px
_PAD_X = 10
_GAP = 7
_DOT = 9
#: Side of a row's icon. The tag medallions are what rows carry, and 16 was too small to
#: read one.
ROW_ICON_PX = 20


class _RowWidget(QWidget):
    """One row, painted in a single ``paintEvent`` rather than assembled from
    child widgets.

    Everything here was child widgets once — a QCheckBox, two QLabels, a dot — and
    every one of them was a way for the platform style, the app palette or fractional
    display scaling to put something on screen this module did not ask for: a gray
    slab behind the checkbox, an indicator the same color as the background, hairlines
    under each child's bottom edge. Painting it directly is both smaller and
    deterministic, and since it reads ``THEME`` at paint time it follows a theme change
    with no ``changeEvent`` plumbing (see CLAUDE.md, GUI theming).

    Three visual states stack: selected, hovered, and the keyboard cursor.
    """

    toggled = pyqtSignal(str, bool)

    def __init__(self, row: PickerRow, checked: bool, parent=None, *, indicator: bool = True) -> None:
        super().__init__(parent)
        self._row = row
        self._selected = checked
        # A single-select picker draws no checkbox: the highlight alone says which row is chosen.
        self._indicator = indicator
        self._hover = False
        self._cursor = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if row.description:
            self.setToolTip(row.description)

    # -- state ---------------------------------------------------------

    @property
    def key(self) -> str:
        return self._row.key

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool) -> None:
        if self._selected == on:
            return
        self._selected = on
        self.update()

    def set_cursor_row(self, on: bool) -> None:
        if self._cursor == on:
            return
        self._cursor = on
        self.update()

    def toggle(self) -> None:
        self._selected = not self._selected
        self.update()
        self.toggled.emit(self._row.key, self._selected)

    # -- painting ------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        if self._hover or self._cursor:
            painter.fillRect(rect, QColor(THEME.node_fill_hover))
        elif self._selected:
            painter.fillRect(rect, QColor(THEME.node_fill_selected))

        # The keyboard cursor has to stay distinct from a plain hover, so it adds a
        # rule down the leading edge rather than another background shade.
        if self._cursor:
            painter.fillRect(0, 0, 2, rect.height(), QColor(THEME.accent))

        dim = self._row.dim
        x = _PAD_X
        if self._indicator:
            box = QRect(x, (rect.height() - _BOX) // 2, _BOX, _BOX)
            self._paint_indicator(painter, box)
            x = box.right() + 1 + _GAP

        if self._row.icon is not None:
            icon = self._row.icon
            if dim:
                painter.setOpacity(0.45)
            icon.paint(painter, QRect(x, (rect.height() - ROW_ICON_PX) // 2, ROW_ICON_PX, ROW_ICON_PX))
            painter.setOpacity(1.0)
            x += ROW_ICON_PX + _GAP
        elif self._row.accent:
            dot = QRect(x, (rect.height() - _DOT) // 2, _DOT, _DOT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._row.accent))
            painter.drawEllipse(dot)
            x = dot.right() + 1 + _GAP

        meta_w = 0
        if self._row.meta:
            meta_font = QFont(self.font())
            meta_font.setPixelSize(THEME.font_small_px)
            painter.setFont(meta_font)
            meta_w = painter.fontMetrics().horizontalAdvance(self._row.meta) + _PAD_X
            painter.setPen(QColor(THEME.text_disabled if dim else THEME.text_subtle))
            painter.drawText(
                QRect(rect.width() - meta_w, 0, meta_w - _PAD_X, rect.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                self._row.meta,
            )

        name_font = QFont(self.font())
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor(THEME.text_disabled if dim else THEME.text))
        name_rect = QRect(x, 0, max(rect.width() - x - meta_w - _GAP, 0), rect.height())
        elided = painter.fontMetrics().elidedText(self._row.label, Qt.TextElideMode.ElideRight, name_rect.width())
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elided)
        painter.end()

    def _paint_indicator(self, painter: QPainter, box: QRect) -> None:
        """A checkbox, drawn rather than delegated.

        Deliberately not a QCheckBox: touching one with a stylesheet at all forfeits
        the platform's own indicator, and what replaces it is drawn from the palette —
        which is how it ended up the same color as the background.
        """
        fill = QColor(THEME.accent) if self._selected else QColor(THEME.bg)
        edge = QColor(THEME.accent if self._selected else THEME.border)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(box).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        if not self._selected:
            return
        pen = QPen(QColor(THEME.pill_text), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        w, h = box.width(), box.height()
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(box.x() + w * 0.24, box.y() + h * 0.52),
                    QPointF(box.x() + w * 0.43, box.y() + h * 0.72),
                    QPointF(box.x() + w * 0.78, box.y() + h * 0.28),
                ]
            )
        )

    # -- interaction ---------------------------------------------------

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        # The whole row is the hit target — a 13px indicator is not.
        self.toggle()
        super().mousePressEvent(event)


#: Side of a section header's icon. The world emblems are detailed 128px vignettes:
#: unrecognizable at 16px, and 48px was too big in real use. At 32px the header is 38px
#: tall, not much more than a 26px zone row, so the list stays dense.
SECTION_ICON_PX = 32

#: Padding around a section header's text, matching the margins the plain QLabel carried.
_SECTION_PAD_Y = 3


class _SectionHeader(QWidget):
    """A section's caption, optionally with an icon ahead of it.

    Painted rather than a ``QLabel``, for the reason the rows and the title bar are: a
    label's frame is the style's to draw, and under fractional scaling it lands as a
    hairline along the bottom edge -- which under a *section* heading reads as a divider
    the design did not ask for. It also means the icon and the text are one widget rather
    than a layout of two, so nothing can wrap or drift between them.

    An icon is artwork in its own colors, so this reads no ``THEME`` value for it and
    needs neither ``ThemedMixin`` nor a ``changeEvent`` -- the same exemption the card art
    tiles take. The text color *is* a ``THEME`` value and is read at paint time.
    """

    def __init__(self, title: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._icon = icon
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(THEME.font_small_px)
        self._font = font
        text_h = QFontMetrics(font).height()
        self.setFixedHeight(max(text_h, SECTION_ICON_PX if icon else 0) + _SECTION_PAD_Y * 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @property
    def title(self) -> str:
        return self._title

    @property
    def icon(self) -> QIcon | None:
        """The icon drawn ahead of the caption, or None where none resolved."""
        return self._icon

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()
        painter.fillRect(rect, QColor(THEME.surface))

        x = _PAD_X
        if self._icon is not None:
            top = (rect.height() - SECTION_ICON_PX) // 2
            self._icon.paint(painter, QRect(x, top, SECTION_ICON_PX, SECTION_ICON_PX))
            x += SECTION_ICON_PX + _GAP

        painter.setFont(self._font)
        painter.setPen(QColor(THEME.text_subtle))
        text_rect = QRect(x, 0, max(rect.width() - x - _PAD_X, 0), rect.height())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            painter.fontMetrics().elidedText(self._title, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        painter.end()


HEADER_HEIGHT = 28


class _HeaderBar(QWidget):
    """The popup's title row: name on the left, selection count and Clear on the right.

    Painted rather than assembled, for the reason the rows are: a QLabel's frame is
    the style's to draw, and between that and fractional display scaling the labels
    were leaving hairlines along their bottom edges — one the width of the title, one
    the width of an empty count label parked on the right. Text that is only ever
    drawn cannot grow an edge.

    Clear is hit-tested against the rectangle the paint pass measured, so there is no
    child widget for it either.
    """

    clear_clicked = pyqtSignal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._caption = ""
        self._clear_rect = QRect()
        self._clear_hover = False
        self.setMouseTracking(True)
        self.setFixedHeight(HEADER_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, caption: str, show_clear: bool) -> None:
        self._caption = caption
        self._show_clear = show_clear
        self.update()

    _show_clear = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(THEME.surface))

        right = rect.width() - _PAD_X
        self._clear_rect = QRect()
        small = QFont(self.font())
        small.setPixelSize(THEME.font_small_px)

        if self._show_clear:
            painter.setFont(small)
            w = painter.fontMetrics().horizontalAdvance("Clear")
            self._clear_rect = QRect(right - w, 0, w, rect.height())
            painter.setPen(QColor(THEME.accent))
            painter.drawText(
                self._clear_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                "Clear",
            )
            if self._clear_hover:
                baseline = rect.height() // 2 + painter.fontMetrics().ascent() // 2 + 1
                painter.fillRect(self._clear_rect.x(), baseline, w, 1, QColor(THEME.accent))
            right -= w + _GAP

        if self._caption:
            painter.setFont(small)
            w = painter.fontMetrics().horizontalAdvance(self._caption)
            painter.setPen(QColor(THEME.text_muted))
            painter.drawText(
                QRect(right - w, 0, w, rect.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                self._caption,
            )
            right -= w + _GAP

        title_font = QFont(self.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(THEME.text))
        title_rect = QRect(_PAD_X, 0, max(right - _PAD_X, 0), rect.height())
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            painter.fontMetrics().elidedText(self._title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        over = self._clear_rect.contains(event.pos())
        if over != self._clear_hover:
            self._clear_hover = over
            self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._clear_hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._clear_rect.contains(event.pos()):
            self.clear_clicked.emit()
        super().mousePressEvent(event)


class PickerPopup(QWidget):
    """The popup itself. Selection is reported live, as each row is toggled."""

    selection_changed = pyqtSignal(list)  # list[str] of keys, in section order
    closed = pyqtSignal()

    def __init__(
        self,
        title: str,
        sections: list[Section],
        selected: list[str] | None = None,
        *,
        search_placeholder: str = "Filter…",
        parent: QWidget | None = None,
        single_select: bool = False,
        width: int = POPUP_WIDTH,
        broad: bool = False,
        section_icon: Callable[[str], QIcon | None] | None = None,
        embedded: bool = False,
    ) -> None:
        """*embedded* builds it as a child widget for a dialog's layout rather than a popup window."""
        super().__init__(parent, Qt.WindowType.Widget if embedded else Qt.WindowType.Popup)
        # Resolves a section title to an emblem, or to None -- which is the ordinary
        # answer, and the only one on an install that has extracted no art.
        self._section_icon = section_icon
        self._sections = sections
        self._selected: set[str] = set(selected or [])
        self._applying_style = False
        self._order = list(dict.fromkeys(r.key for _, rows in sections for r in rows))
        self._rows: list[_RowWidget] = []
        self._cursor_key: str | None = None
        # Single-select: no checkboxes, no count or Clear, and a pick closes the popup. A row
        # can appear in two sections (the zone picker's suggestions), so keys are deduplicated.
        self._single_select = single_select
        # See filter_sections: rows told apart only by their section and meta.
        self._broad = broad

        self.setFixedWidth(width)
        self.setObjectName("PickerPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        # Inset by the border width. At margin 0 the children paint over the
        # stylesheet border and the popup loses its edge against the window behind it.
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # ── Header: title, live selection count, and a way back to empty ──
        self._top = QWidget()
        self._top.setObjectName("PickerTop")
        self._top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        top_col = QVBoxLayout(self._top)
        top_col.setContentsMargins(0, 0, 0, 0)
        top_col.setSpacing(0)

        self._header = _HeaderBar(title)
        self._header.clear_clicked.connect(self._clear)
        top_col.addWidget(self._header)

        self._search = SearchLineEdit(search_placeholder)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._rebuild)
        self._search.installEventFilter(self)
        sw = QHBoxLayout()
        sw.setContentsMargins(8, 0, 8, 8)
        sw.addWidget(self._search)
        top_col.addLayout(sw)
        root.addWidget(self._top)

        head_line = QFrame()
        head_line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(head_line)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Every keystroke in the search changes how many rows there are, and so whether
        # the body scrolls; without the gutter the rows would widen and narrow as you type.
        reserve_scrollbar_gutter(self._scroll)
        self._body = QWidget()
        self._body.setObjectName("PickerBody")
        self._body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 6)
        self._body_layout.setSpacing(0)
        self._body_layout.addStretch()
        self._scroll.setWidget(self._body)
        root.addWidget(self._scroll, 1)

        self._apply_style()
        self._rebuild()

    # -- public API ----------------------------------------------------

    def selection(self) -> list[str]:
        """Selected keys, in the order the sections declare them."""
        return [k for k in self._order if k in self._selected]

    def focus_search(self) -> None:
        """Put focus in the filter box, where the keyboard drives the list -- for an embedded one."""
        self._search.setFocus()

    def popup_at(self, anchor: QWidget) -> None:
        """Show under *anchor*, edges flush, flipping above or across it if it will not fit."""
        self.adjustSize()
        ideal = min(self.sizeHint().height(), POPUP_MAX_HEIGHT)
        top_left = anchor.mapToGlobal(anchor.rect().topLeft())
        rect = QRect(top_left, anchor.size())
        if screen := anchor.screen():
            avail = screen.availableGeometry()
            self.setFixedHeight(popup_height(rect, ideal, avail))
            self.move(popup_position(rect, self.size(), avail))
        else:
            self.setFixedHeight(ideal)
            self.move(QPoint(rect.left(), rect.top() + rect.height()))
        self.show()
        self._search.setFocus()
        if self._single_select and self._selected:
            # Open on the current choice, scrolled into view, rather than at the top of a long list.
            self._set_cursor(next(iter(self._selected)))

    # -- keyboard ------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Drive the list from the search box, which is where focus lives.

        Up/Down move a cursor and Enter toggles it, rather than Space — Space has to
        keep typing a space while the filter box has focus.
        """
        if obj is self._search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_cursor(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_cursor(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._toggle_cursor_row()
                return True
        return super().eventFilter(obj, event)

    def _move_cursor(self, delta: int) -> None:
        if not self._rows:
            return
        keys = [r.key for r in self._rows]
        if self._cursor_key in keys:
            idx = min(max(keys.index(self._cursor_key) + delta, 0), len(keys) - 1)
        else:
            idx = 0 if delta > 0 else len(keys) - 1
        self._set_cursor(keys[idx])

    def _set_cursor(self, key: str | None) -> None:
        self._cursor_key = key
        for row in self._rows:
            row.set_cursor_row(row.key == key)
            if row.key == key:
                self._scroll.ensureWidgetVisible(row)

    def _toggle_cursor_row(self) -> None:
        for row in self._rows:
            if row.key == self._cursor_key:
                row.toggle()
                return

    # -- internals -----------------------------------------------------

    def _clear(self) -> None:
        self._selected.clear()
        for row in self._rows:
            row.set_selected(False)
        self._refresh_header()
        self.selection_changed.emit([])

    def _refresh_header(self) -> None:
        if self._single_select:
            # Nothing to count and nothing to clear: there is always exactly the one choice.
            self._header.set_state("", False)
            return
        self._header.set_state(selection_caption(len(self._selected)), bool(self._selected))

    def _rebuild(self) -> None:
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            if w := item.widget():
                w.hide()
                w.deleteLater()
        self._rows = []

        visible = filter_sections(self._sections, self._search.text(), broad=self._broad)
        if not visible:
            empty = QLabel("No matches.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(0, 14, 0, 14)
            set_style_sheet(empty, f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")
            self._body_layout.insertWidget(0, empty)
            self._refresh_header()
            return

        at = 0
        for title, rows in visible:
            icon = None
            if self._section_icon is not None:
                # A resolver that raises must not take the popup down with it: the icon is
                # decoration over a caption that already carries the meaning.
                try:
                    icon = self._section_icon(title)
                except Exception:
                    icon = None
            header = _SectionHeader(title, icon)
            self._body_layout.insertWidget(at, header)
            at += 1
            for row in rows:
                w = _RowWidget(row, row.key in self._selected, indicator=not self._single_select)
                w.toggled.connect(self._on_row_toggled)
                self._body_layout.insertWidget(at, w)
                self._rows.append(w)
                at += 1

        # The cursor follows the filtered list: after typing, Down should start at
        # the first surviving row rather than resume from something now hidden.
        if self._cursor_key not in [r.key for r in self._rows]:
            self._cursor_key = None
        self._set_cursor(self._cursor_key)
        self._refresh_header()

    def _on_row_toggled(self, key: str, checked: bool) -> None:
        self._selected = apply_toggle(self._selected, key, checked, single_select=self._single_select)
        if self._single_select:
            # The same key can be a row in two sections; both should show the choice.
            for row in self._rows:
                row.set_selected(row.key == key)
        self._refresh_header()
        self.selection_changed.emit(self.selection())
        if self._single_select:
            self.close()

    def _apply_style(self) -> None:
        if self._applying_style:
            return
        self._applying_style = True
        try:
            set_style_sheet(
                self,
                f"#PickerPopup {{ background: {THEME.bg}; border: 1px solid {THEME.border};"
                f"  border-radius: 6px; }}"
                # Painted explicitly rather than inherited from the app palette: the
                # row list sitting on a default-light viewport turns the dim/normal
                # contrast inside out, and a widget should not depend on a palette a
                # caller might not have set.
                f"#PickerTop {{ background: {THEME.surface}; }}"
                f"#PickerBody {{ background: {THEME.bg}; }}"
                f"QScrollArea {{ background: transparent; border: none; }}"
                # A QCheckBox left to the palette paints a Button/Base block behind
                # both its indicator and its empty text area, which shows up as a gray
                # slab beside every row under some themes and platform styles. Only the
                # background is wrong, so only the background is overridden — the
                # indicator keeps the platform's own checkmark. spacing: 0 removes the
                # gap the box reserves for text it does not have.
                f"QCheckBox {{ background: transparent; spacing: 0; }}"
                # border: none is not redundant. Left unsaid, a QLabel's frame is the
                # style's to draw, and under some styles and at fractional display
                # scaling it lands as a hairline along the label's bottom edge — which
                # is what put an underline beneath the title and the count.
                f"QLabel {{ color: {THEME.text}; background: transparent; border: none; }}"
                f'QFrame[frameShape="4"] {{ color: {THEME.border}; }}'
                f"QLineEdit {{ background: {THEME.bg}; color: {THEME.text};"
                f"  border: 1px solid {THEME.border}; border-radius: 4px; padding: 3px 6px; }}"
                f"QLineEdit:focus {{ border-color: {THEME.accent}; }}",
            )
        finally:
            self._applying_style = False

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.closed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()


class PickerButton(QToolButton):
    """Anchor button that owns a :class:`PickerPopup` and shows its selection.

    The popup is rebuilt on each click rather than kept: its rows carry counts that
    go stale, and a picker is opened far too rarely for building twenty widgets to
    be worth caching.

    Two signals, because "changing" and "settled" are different questions.
    ``selection_changed`` fires on every toggle, which is what a filter bar wants --
    the results should follow the checkbox. ``committed`` fires once, when the popup
    closes. A caller whose reaction is expensive, or which rebuilds the widget tree
    this button lives in, must use ``committed``: reacting per toggle would destroy
    the button while its own popup is still open on top of it.
    """

    selection_changed = pyqtSignal(list)
    committed = pyqtSignal(list)

    def __init__(
        self,
        title: str,
        *,
        placeholder: str = "—",
        search_placeholder: str = "Filter…",
        single_select: bool = False,
        popup_width: int = POPUP_WIDTH,
        broad: bool = False,
        section_icon: Callable[[str], QIcon | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._placeholder = placeholder
        self._search_placeholder = search_placeholder
        # Handed to every popup this opens; see PickerPopup.
        self._popup_options = {
            "single_select": single_select,
            "width": popup_width,
            "broad": broad,
            "section_icon": section_icon,
        }
        self._sections: list[Section] = []
        self._selected: list[str] = []
        self._applying_style = False
        self._popup: PickerPopup | None = None
        self._popup_closed_at: float | None = None
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.clicked.connect(self._open)
        self._apply_style()
        self._refresh_text()

    # -- public API ----------------------------------------------------

    def set_sections(self, sections: list[Section]) -> None:
        self._sections = sections
        self._refresh_text()

    def set_selection(self, keys: list[str]) -> None:
        """Set the selection without emitting ``selection_changed``."""
        self._selected = list(keys)
        if self._popup is not None:
            self._popup.close()
            # A programmatic close is not the click-dismiss case the guard exists
            # for, so it must not swallow the user's next click on the button.
            self._popup_closed_at = None
        self._refresh_text()

    def selection(self) -> list[str]:
        return list(self._selected)

    # -- internals -----------------------------------------------------

    def _labels(self) -> list[str]:
        by_key = {r.key: r.label for _, rows in self._sections for r in rows}
        return [by_key.get(k, k) for k in self._selected]

    def format_text(self, labels: list[str]) -> str:
        """The button's caption for *labels*. Overridable — see ``TagPickerButton``.

        The default lists what is selected, which is the most useful thing a lone
        picker can say. A button that is one of several facets wants its facet's
        *name* to stay put instead, so the bar reads as a row of filters.
        """
        return summarize(labels, self._placeholder)

    def _refresh_text(self) -> None:
        self.setText(self.format_text(self._labels()))

    def _open(self) -> None:
        # Clicking the button while the popup is up dismisses it and then delivers
        # the same press here, which would open a second one on top of the first.
        if not should_open(time.monotonic(), self._popup_closed_at):
            return
        popup = PickerPopup(
            self._title,
            self._sections,
            self._selected,
            search_placeholder=self._search_placeholder,
            parent=self,
            **self._popup_options,
        )
        popup.selection_changed.connect(self._on_selection_changed)
        popup.closed.connect(self._on_popup_closed)
        self._popup = popup
        popup.popup_at(self)

    def _on_popup_closed(self) -> None:
        self._popup_closed_at = time.monotonic()
        self.committed.emit(self.selection())

    def _on_selection_changed(self, keys: list) -> None:
        self._selected = list(keys)
        self._refresh_text()
        self.selection_changed.emit(self.selection())

    def _apply_style(self) -> None:
        # Guard is load-bearing: this styles itself, and setStyleSheet delivers a
        # StyleChange straight back to changeEvent.
        if self._applying_style:
            return
        self._applying_style = True
        try:
            set_style_sheet(
                self,
                f"QToolButton {{ background: {THEME.surface}; color: {THEME.text};"
                f"  border: 1px solid {THEME.border}; border-radius: 3px;"
                f"  padding: 3px {6 + CARET_W + CARET_PAD}px 3px 6px; text-align: left; }}"
                f"QToolButton:hover {{ border-color: {THEME.accent}; }}",
            )
        finally:
            self._applying_style = False

    def paintEvent(self, event) -> None:
        """Draw the label normally, then a caret in the padding reserved for it.

        Always points **down**, whichever way the popup opens. The caret says "this
        opens something", the way a ``QComboBox`` arrow does — it is not a report of
        which direction ``popup_height`` and ``popup_position`` settled on, which is
        a function of screen size and splitter position and changes under the user
        for no reason they can see. Two of these sit side by side in the bot library
        and open to different heights; opposite carets on adjacent identical controls
        would read as a glitch.

        Drawn rather than appended to the text: ``setText`` is ``summarize``'s output,
        which grows to "Gear, Jewels +2", and a glyph inside that string is a glyph
        that gets elided away exactly when the button is widest.
        """
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() - 6 - CARET_W / 2
        cy = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(THEME.text_muted))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(cx - CARET_W / 2, cy - CARET_H / 2),
                    QPointF(cx + CARET_W / 2, cy - CARET_H / 2),
                    QPointF(cx, cy + CARET_H / 2),
                ]
            )
        )
        painter.end()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()
