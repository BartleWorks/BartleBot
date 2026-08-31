"""A searchable, sectioned, multi-select popup — and the button that opens it.

Qt's native ``QMenu`` is drawn by the platform theme, so it ignores the app
stylesheet almost entirely: ``THEME`` never reaches it, and ``addSection()``
degrades to a hairline with small text. That is fine for a short list of commands
and wrong for a picker with sections, descriptions and a filter box, which is why
this is a plain widget rather than a menu.

It is a ``Qt.WindowType.Popup``, which buys three things a ``QDialog`` would not:

* click-outside-to-dismiss and Esc, handled by Qt;
* the app stylesheet, because it is an ordinary styled widget;
* no ``exec()``, so it cannot trip the qasync task re-entrancy rule in CLAUDE.md.

Being a popup *window* also puts it outside the sidebar's widget tree, so the
native-window transient-parent bug that ``menu_utils`` exists to work around
(#119) does not apply here.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.theme import THEME

POPUP_WIDTH = 330
POPUP_MAX_HEIGHT = 420


@dataclass(frozen=True)
class PickerRow:
    """One selectable row.

    ``key`` is the stable identity the popup reports; ``label`` is what is shown.
    They differ for callers whose display text is localized or decorated.

    ``meta`` is the right-aligned slot — a usage count, say. It is the caller's job
    to leave it empty where the number would be meaningless, which is why it is per
    row rather than a flag on the popup.
    """

    key: str
    label: str
    description: str = ""
    meta: str = ""
    accent: str | None = None  # colored dot; None draws no dot


Section = tuple[str, list[PickerRow]]


def filter_sections(sections: list[Section], query: str) -> list[Section]:
    """Narrow *sections* to rows matching *query*, dropping sections left empty.

    Matches label *and* description, so "no combat" finds Gathering — the
    description is the part a newcomer can search by, since they do not yet know
    the vocabulary's names.

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


class _RowWidget(QWidget):
    """One row: checkbox, optional dot, name over description, right-aligned meta."""

    toggled = pyqtSignal(str, bool)

    def __init__(self, row: PickerRow, checked: bool, parent=None) -> None:
        super().__init__(parent)
        self._row = row
        self.setObjectName("PickerRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._check = QCheckBox()
        self._check.setChecked(checked)
        self._check.toggled.connect(lambda on: self.toggled.emit(self._row.key, on))

        name = QLabel(row.label)
        f = name.font()
        f.setBold(True)
        name.setFont(f)
        name.setStyleSheet(f"color: {THEME.text}; background: transparent;")

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(5)
        if row.accent:
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            dot.setStyleSheet(f"background: {row.accent}; border-radius: 4px;")
            name_row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        name_row.addWidget(name, 1)
        text_col.addLayout(name_row)

        if row.description:
            desc = QLabel(row.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; background: transparent;")
            text_col.addWidget(desc)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 5, 10, 5)
        outer.setSpacing(7)
        outer.addWidget(self._check, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(text_col, 1)
        if row.meta:
            meta = QLabel(row.meta)
            meta.setStyleSheet(f"color: {THEME.text_subtle}; font-size: 10px; background: transparent;")
            outer.addWidget(meta, 0, Qt.AlignmentFlag.AlignTop)

        self._set_style(hover=False)

    def _set_style(self, *, hover: bool) -> None:
        bg = THEME.node_fill_hover if hover else "transparent"
        self.setStyleSheet(f"#PickerRow {{ background: {bg}; }}")

    def enterEvent(self, event) -> None:
        self._set_style(hover=True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_style(hover=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        # The whole row is the hit target — a 13px checkbox is not.
        self._check.toggle()
        super().mousePressEvent(event)


class PickerPopup(QWidget):
    """The popup itself. Selection is reported live, as each row is toggled."""

    selection_changed = pyqtSignal(list)  # list[str] of keys, in section order

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

        self.setFixedWidth(POPUP_WIDTH)
        self.setObjectName("PickerPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._title = QLabel(title)
        tf = self._title.font()
        tf.setBold(True)
        self._title.setFont(tf)
        self._title.setContentsMargins(10, 7, 10, 7)
        root.addWidget(self._title)

        head_line = QFrame()
        head_line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(head_line)

        self._search = QLineEdit()
        self._search.setPlaceholderText(search_placeholder)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._rebuild)
        search_wrap = QWidget()
        sw = QVBoxLayout(search_wrap)
        sw.setContentsMargins(8, 7, 8, 7)
        sw.addWidget(self._search)
        root.addWidget(search_wrap)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body = QWidget()
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
        """Show below *anchor*, nudged left if that would run off the screen."""
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

    # -- internals -----------------------------------------------------

    def _rebuild(self) -> None:
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            if w := item.widget():
                w.hide()
                w.deleteLater()

        visible = filter_sections(self._sections, self._search.text())
        if not visible:
            empty = QLabel("No matches.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(0, 14, 0, 14)
            empty.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
            self._body_layout.insertWidget(0, empty)
            return

        at = 0
        for title, rows in visible:
            header = QLabel(title)
            header.setObjectName("PickerSection")
            header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            header.setContentsMargins(10, 3, 10, 3)
            header.setStyleSheet(
                f"#PickerSection {{ background: {THEME.surface}; color: {THEME.text_subtle};"
                f"  font-size: 10px; font-weight: bold;"
                f"  border-top: 1px solid {THEME.border}; border-bottom: 1px solid {THEME.border}; }}"
            )
            self._body_layout.insertWidget(at, header)
            at += 1
            for row in rows:
                w = _RowWidget(row, row.key in self._selected)
                w.toggled.connect(self._on_row_toggled)
                self._body_layout.insertWidget(at, w)
                at += 1

    def _on_row_toggled(self, key: str, checked: bool) -> None:
        if checked:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self.selection_changed.emit(self.selection())

    def _apply_style(self) -> None:
        if self._applying_style:
            return
        self._applying_style = True
        try:
            self.setStyleSheet(
                f"#PickerPopup {{ background: {THEME.bg}; border: 1px solid {THEME.border};"
                f"  border-radius: 6px; }}"
                f"QLabel {{ color: {THEME.text}; background: transparent; }}"
                f'QFrame[frameShape="4"] {{ color: {THEME.border}; }}'
                f"QLineEdit {{ background: {THEME.surface}; color: {THEME.text};"
                f"  border: 1px solid {THEME.border}; border-radius: 4px; padding: 3px 6px; }}"
                f"QLineEdit:focus {{ border-color: {THEME.accent}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()


class PickerButton(QToolButton):
    """Anchor button that owns a :class:`PickerPopup` and shows its selection.

    The popup is rebuilt on each click rather than kept: its rows carry counts and
    descriptions that go stale, and a picker is opened far too rarely for building
    twenty widgets to be worth caching.
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
        popup = PickerPopup(
            self._title,
            self._sections,
            self._selected,
            search_placeholder=self._search_placeholder,
            parent=self,
        )
        popup.selection_changed.connect(self._on_selection_changed)
        self._popup = popup
        popup.popup_at(self)

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
