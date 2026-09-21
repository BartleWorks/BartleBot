"""A layout that wraps its children onto as many rows as they need.

Qt ships no wrapping layout — ``QHBoxLayout`` overflows, and a ``QGridLayout``
needs the column count decided up front, which is exactly what is unknown when
the items are pills whose widths come from their text.

The bot's tag pills are the case this exists for. Eighteen of them can be on one
bot, in a sidebar whose minimum width is 300px, so the row count is a function of
the split and of which tags were picked, and neither is known when the layout is
built.

``flow_geometry`` holds the arithmetic and is pure, so the wrapping can be tested
at any width without a screen — the same reasoning as ``popup_position`` in
:mod:`bartlebot.gui.widgets.picker_popup`.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


def flow_geometry(
    sizes: list[QSize],
    width: int,
    *,
    h_spacing: int = 4,
    v_spacing: int = 4,
) -> tuple[list[QPoint], int]:
    """Lay *sizes* out left to right, wrapping at *width*.

    Returns each item's top-left corner relative to the layout's own origin, and the
    total height the rows came to.

    An item wider than *width* still gets a row to itself rather than being dropped
    or shrunk — a pill for a long tag name is better clipped by the sidebar than
    absent from it, and the alternative is an item that never places at all.

    Items shorter than their row are **centred** in it rather than hung from its top.
    The bot header's tag row is the case: an 18px tag chip beside a 27px "Rewards"
    button sat four pixels high, which reads as a misalignment rather than as a row of
    two different things.  That is also why the rows are collected before any position
    is assigned — a row's height is only known once the row is complete.
    """
    rows: list[list[int]] = []
    current: list[int] = []
    x = 0
    for index, size in enumerate(sizes):
        if x and x + size.width() > width:
            rows.append(current)
            current = []
            x = 0
        current.append(index)
        x += size.width() + h_spacing
    if current:
        rows.append(current)

    positions: list[QPoint] = []
    y = 0
    for row in rows:
        row_height = max(sizes[i].height() for i in row)
        x = 0
        for i in row:
            positions.append(QPoint(x, y + (row_height - sizes[i].height()) // 2))
            x += sizes[i].width() + h_spacing
        y += row_height + v_spacing
    return positions, max(y - v_spacing, 0)


def block_offset(content_height: int, available_height: int, *, centre: bool) -> int:
    """How far down to push a block of rows in the space it was given.

    Zero unless *centre*, and zero anyway whenever the flow got exactly the height its
    rows came to -- which is the normal case, because :func:`flow_container` sizes the
    widget to its own wrapped height.

    Top is the right default and most callers want it: a keyword list or a card grid
    handed a panel taller than it needs should stay at the top of it, not float in the
    middle.  Centring is for a flow whose rect is taller than its rows for a reason of
    its own, and which therefore has to find the rect's centre rather than its top.  The
    bot header's tag row is that case -- its tag field reserves the height of its
    *editable* form in both modes so the bar does not change height when the mode does,
    and the grid row is taller still while the button clusters span it, so in Run mode
    the pills sit in a rect several pixels deeper than they need.  All of that slack
    landed below them, which reads as pills pinned to the row's ceiling rather than as a
    row.  That is the same reasoning as the per-row centring in :func:`flow_geometry`,
    one level up.

    Never negative: a block taller than its rect is clipped at the bottom, which is
    visible, rather than pulled up off the top, which hides the first row entirely.
    """
    if not centre:
        return 0
    return max((available_height - content_height) // 2, 0)


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new row when it runs out of width."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margin: int = 0,
        spacing: int = 4,
        centre_rows: bool = False,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        #: Centre the rows in a rect taller than they need, rather than hanging them from
        #: its top.  Off by default -- see :func:`block_offset` for which case wants it.
        self._centre_rows = centre_rows
        self.setContentsMargins(margin, margin, margin, margin)

    # -- QLayout plumbing ----------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802  (Qt override)
        self._items.append(item)

    def insertWidget(self, index: int, widget: QWidget) -> None:  # noqa: N802  (matches QBoxLayout)
        """Place *widget* at *index* rather than at the end.

        For a row that grows in the middle and keeps a control pinned to the end --
        pills that come and go beside a button that must not walk along the row as they
        do. ``addWidget`` first, so ``QLayout`` does the reparenting and ownership it
        alone knows how to do, and then move the item it appended.
        """
        self.addWidget(widget)
        self._items.insert(index, self._items.pop())

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802  (Qt override)
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802  (Qt override)
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802  (Qt override)
        return Qt.Orientation(0)

    # -- geometry ------------------------------------------------------

    def hasHeightForWidth(self) -> bool:  # noqa: N802  (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802  (Qt override)
        left, top, right, bottom = self.getContentsMargins()
        _, height = self._geometry(width - left - right)
        return height + top + bottom

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802  (Qt override)
        super().setGeometry(rect)
        left, top, right, bottom = self.getContentsMargins()
        positions, height = self._geometry(rect.width() - left - right)
        offset = block_offset(height + top + bottom, rect.height(), centre=self._centre_rows)
        for item, pos in zip(self._live(), positions):
            size = item.sizeHint()
            item.setGeometry(
                QRect(rect.x() + left + pos.x(), rect.y() + top + offset + pos.y(), size.width(), size.height())
            )

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt override)
        """One row of everything -- what this would *like*, not what it needs.

        Preferred and minimum are different questions, and answering both with the
        minimum is what made a row of two tag pills stack vertically in a bar 1400px
        wide: the parent handed the widget the widest single pill, because that is
        all it had asked for. Wrapping is still what happens when the width is not
        there; ``minimumSize`` below is what allows it.
        """
        left, top, right, bottom = self.getContentsMargins()
        items = self._live()
        width = sum(item.sizeHint().width() for item in items)
        width += self._spacing * max(len(items) - 1, 0)
        height = max((item.sizeHint().height() for item in items), default=0)
        return QSize(width + left + right, height + top + bottom)

    def minimumSize(self) -> QSize:  # noqa: N802  (Qt override)
        """The widest single item, not the sum — anything else refuses to wrap.

        A minimum equal to one row of every item makes the parent demand that width,
        and the sidebar then never narrows enough for the layout to do its job.
        """
        left, top, right, bottom = self.getContentsMargins()
        size = QSize(0, 0)
        for item in self._live():
            size = size.expandedTo(item.minimumSize())
        return size + QSize(left + right, top + bottom)

    def _live(self) -> list[QLayoutItem]:
        """The items that are actually on screen.

        A hidden widget must not reserve a gap, and here it would reserve a whole
        one: the tag field parks its "Add…" buttons in this layout and hides them
        outside Edit mode, and counted in they pushed the pills onto a second row in
        a bar with room to spare. ``QBoxLayout`` skips hidden items too.
        """
        return [item for item in self._items if not item.isEmpty()]

    def _geometry(self, width: int) -> tuple[list[QPoint], int]:
        return flow_geometry(
            [item.sizeHint() for item in self._live()],
            max(width, 0),
            h_spacing=self._spacing,
            v_spacing=self._spacing,
        )


def flow_container(spacing: int = 4, *, centre_rows: bool = False) -> tuple[QWidget, FlowLayout]:
    """A widget whose height follows its :class:`FlowLayout`'s wrapping.

    ``heightForWidth`` alone is not enough inside a ``QVBoxLayout``: the parent asks
    for a size hint before it knows the width, so a wrapped row is drawn outside the
    widget's own rect and clipped. The height policy below is what makes the parent
    ask again once the width is settled.
    """
    w = QWidget()
    layout = FlowLayout(w, spacing=spacing, centre_rows=centre_rows)
    policy = w.sizePolicy()
    policy.setHeightForWidth(True)
    policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
    w.setSizePolicy(policy)
    return w, layout
