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
native-window transient-parent bug that ``menu_utils`` exists to work around
(#119) does not apply here.

``PickerRow.description`` is supported but optional, and the tag picker does not
use it: a second line of muted text under every name doubled the row height, made
the heights ragged, and read as an underline once the list was scrolling. Rows are
one line unless a caller has something to say that the name does not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME

POPUP_WIDTH = 400
POPUP_MAX_HEIGHT = 560


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
    """

    key: str
    label: str
    description: str = ""
    meta: str = ""
    accent: str | None = None  # colored dot; None draws no dot
    dim: bool = False


Section = tuple[str, list[PickerRow]]


def filter_sections(sections: list[Section], query: str) -> list[Section]:
    """Narrow *sections* to rows matching *query*, dropping sections left empty.

    Matches the description too, for callers that set one — free when they do not,
    since an empty description matches no non-empty query.

    Pure and module level, so the matching can be tested without a QApplication.
    """
    q = query.strip().casefold()
    if not q:
        return list(sections)
    out: list[Section] = []
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


ROW_HEIGHT = 26
_BOX = 13  # checkbox indicator, px
_PAD_X = 10
_GAP = 7
_DOT = 9


class _RowWidget(QWidget):
    """One row, painted in a single ``paintEvent`` rather than assembled from
    child widgets.

    Everything here was child widgets once — a QCheckBox, two QLabels, a dot — and
    every one of them was a way for the platform style, the app palette or fractional
    display scaling to put something on screen this module did not ask for: a grey
    slab behind the checkbox, an indicator the same colour as the background, hairlines
    under each child's bottom edge. Painting it directly is both smaller and
    deterministic, and since it reads ``THEME`` at paint time it follows a theme change
    with no ``changeEvent`` plumbing (see CLAUDE.md, GUI theming).

    Three visual states stack: selected, hovered, and the keyboard cursor.
    """

    toggled = pyqtSignal(str, bool)

    def __init__(self, row: PickerRow, checked: bool, parent=None) -> None:
        super().__init__(parent)
        self._row = row
        self._selected = checked
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
        box = QRect(x, (rect.height() - _BOX) // 2, _BOX, _BOX)
        self._paint_indicator(painter, box)
        x = box.right() + 1 + _GAP

        if self._row.accent:
            dot = QRect(x, (rect.height() - _DOT) // 2, _DOT, _DOT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._row.accent))
            painter.drawEllipse(dot)
            x = dot.right() + 1 + _GAP

        meta_w = 0
        if self._row.meta:
            meta_font = QFont(self.font())
            meta_font.setPointSizeF(max(self.font().pointSizeF() - 1.0, 6.0))
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
        which is how it ended up the same colour as the background.
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
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self._sections = sections
        self._selected: set[str] = set(selected or [])
        self._applying_style = False
        self._order = [r.key for _, rows in sections for r in rows]
        self._rows: list[_RowWidget] = []
        self._cursor_key: str | None = None

        self.setFixedWidth(POPUP_WIDTH)
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

        header = QHBoxLayout()
        header.setContentsMargins(10, 7, 8, 5)
        header.setSpacing(6)
        self._title = QLabel(title)
        tf = self._title.font()
        tf.setBold(True)
        self._title.setFont(tf)
        header.addWidget(self._title, 1)

        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; background: transparent;")
        header.addWidget(self._count_lbl, 0)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFlat(True)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ color: {THEME.accent}; border: none; padding: 0 2px; font-size: 10px; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}"
        )
        self._clear_btn.clicked.connect(self._clear)
        header.addWidget(self._clear_btn, 0)
        top_col.addLayout(header)

        self._search = QLineEdit()
        self._search.setPlaceholderText(search_placeholder)
        self._search.setClearButtonEnabled(True)
        self._search.addAction(
            svg_icon("search", 14, tint=THEME.text_muted),
            QLineEdit.ActionPosition.LeadingPosition,
        )
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

    def popup_at(self, anchor: QWidget) -> None:
        """Show below *anchor*, nudged to stay on screen."""
        self.adjustSize()
        self.setFixedHeight(min(self.sizeHint().height(), POPUP_MAX_HEIGHT))
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        if screen := anchor.screen():
            avail = screen.availableGeometry()
            pos.setX(max(avail.left(), min(pos.x(), avail.right() - self.width())))
            if pos.y() + self.height() > avail.bottom():
                pos.setY(max(avail.top(), anchor.mapToGlobal(anchor.rect().topLeft()).y() - self.height()))
        self.move(pos)
        self.show()
        self._search.setFocus()

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
        self._count_lbl.setText(selection_caption(len(self._selected)))
        self._clear_btn.setVisible(bool(self._selected))

    def _rebuild(self) -> None:
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            if w := item.widget():
                w.hide()
                w.deleteLater()
        self._rows = []

        visible = filter_sections(self._sections, self._search.text())
        if not visible:
            empty = QLabel("No matches.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(0, 14, 0, 14)
            empty.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
            self._body_layout.insertWidget(0, empty)
            self._refresh_header()
            return

        at = 0
        for title, rows in visible:
            header = QLabel(title)
            header.setObjectName("PickerSection")
            header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            header.setContentsMargins(10, 3, 10, 3)
            header.setStyleSheet(
                f"#PickerSection {{ background: {THEME.surface}; color: {THEME.text_subtle};"
                f"  font-size: 10px; font-weight: bold; }}"
            )
            self._body_layout.insertWidget(at, header)
            at += 1
            for row in rows:
                w = _RowWidget(row, row.key in self._selected)
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
        if checked:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self._refresh_header()
        self.selection_changed.emit(self.selection())

    def _apply_style(self) -> None:
        if self._applying_style:
            return
        self._applying_style = True
        try:
            self.setStyleSheet(
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
                # both its indicator and its empty text area, which shows up as a grey
                # slab beside every row under some themes and platform styles. Only the
                # background is wrong, so only the background is overridden — the
                # indicator keeps the platform's own checkmark. spacing: 0 removes the
                # gap the box reserves for text it does not have.
                f"QCheckBox {{ background: transparent; spacing: 0; }}"
                f"QLabel {{ color: {THEME.text}; background: transparent; }}"
                f'QFrame[frameShape="4"] {{ color: {THEME.border}; }}'
                f"QLineEdit {{ background: {THEME.bg}; color: {THEME.text};"
                f"  border: 1px solid {THEME.border}; border-radius: 4px; padding: 3px 6px; }}"
                f"QLineEdit:focus {{ border-color: {THEME.accent}; }}"
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
    """

    selection_changed = pyqtSignal(list)

    def __init__(
        self,
        title: str,
        *,
        placeholder: str = "—",
        search_placeholder: str = "Filter…",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._placeholder = placeholder
        self._search_placeholder = search_placeholder
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

    def _refresh_text(self) -> None:
        self.setText(summarize(self._labels(), self._placeholder))

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
        )
        popup.selection_changed.connect(self._on_selection_changed)
        popup.closed.connect(self._on_popup_closed)
        self._popup = popup
        popup.popup_at(self)

    def _on_popup_closed(self) -> None:
        self._popup_closed_at = time.monotonic()

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
            self.setStyleSheet(
                f"QToolButton {{ background: {THEME.surface}; color: {THEME.text};"
                f"  border: 1px solid {THEME.border}; border-radius: 3px;"
                f"  padding: 3px 6px; text-align: left; }}"
                f"QToolButton:hover {{ border-color: {THEME.accent}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()
