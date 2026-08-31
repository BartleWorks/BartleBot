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
    """
    positions: list[QPoint] = []
    x = y = 0
    row_height = 0
    for size in sizes:
        if x and x + size.width() > width:
            x = 0
            y += row_height + v_spacing
            row_height = 0
        positions.append(QPoint(x, y))
        x += size.width() + h_spacing
        row_height = max(row_height, size.height())
    return positions, y + row_height


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new row when it runs out of width."""

    def __init__(self, parent: QWidget | None = None, *, margin: int = 0, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # -- QLayout plumbing ----------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802  (Qt override)
        self._items.append(item)

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
        positions, _ = self._geometry(rect.width() - left - right)
        for item, pos in zip(self._items, positions):
            size = item.sizeHint()
            item.setGeometry(QRect(rect.x() + left + pos.x(), rect.y() + top + pos.y(), size.width(), size.height()))

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt override)
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802  (Qt override)
        """The widest single item, not the sum — anything else refuses to wrap.

        A minimum equal to one row of every item makes the parent demand that width,
        and the sidebar then never narrows enough for the layout to do its job.
        """
        left, top, right, bottom = self.getContentsMargins()
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size + QSize(left + right, top + bottom)

    def _geometry(self, width: int) -> tuple[list[QPoint], int]:
        return flow_geometry(
            [item.sizeHint() for item in self._items],
            max(width, 0),
            h_spacing=self._spacing,
            v_spacing=self._spacing,
        )


def flow_container(spacing: int = 4) -> tuple[QWidget, FlowLayout]:
    """A widget whose height follows its :class:`FlowLayout`'s wrapping.

    ``heightForWidth`` alone is not enough inside a ``QVBoxLayout``: the parent asks
    for a size hint before it knows the width, so a wrapped row is drawn outside the
    widget's own rect and clipped. The height policy below is what makes the parent
    ask again once the width is settled.
    """
    w = QWidget()
    layout = FlowLayout(w, spacing=spacing)
    policy = w.sizePolicy()
    policy.setHeightForWidth(True)
    policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
    w.setSizePolicy(policy)
    return w, layout
