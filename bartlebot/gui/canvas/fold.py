"""Folding the canvas around collapsed regions (#67). Pure: no Qt.

A collapsed region is drawn as one node, and the grid columns and rows only its
members used are closed up, the way an editor folds lines of code. **Stored
positions are always the full expanded layout**; this module maps between them
and where things are drawn. Collapsing therefore writes one flag and moves
nothing, and expanding is an exact reverse.

A column is hidden when it lies inside a collapsed region's span -- past the
column its node is drawn in -- and nothing drawn occupies it. Rows follow the
same rule on their own. A gap outside every collapsed span is the author's and
is never touched, and a column another visible node shares stays, gap and all.

The map is derived from what the scene draws every time it is built and never
stored, like display ids: undo, a recompose or a paste rebuilds it with the scene.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    """A collapsed region's members' extent in grid cells, inclusive. Its node sits at ``(col0, row0)``."""

    col0: int
    col1: int
    row0: int
    row1: int


@dataclass(frozen=True)
class FoldMap:
    """Hidden grid columns and rows, and the two-way mapping they imply."""

    col_w: float
    row_h: float
    hidden_cols: tuple[int, ...] = ()
    hidden_rows: tuple[int, ...] = ()

    # -- cells ---------------------------------------------------------

    def col_of(self, x: float) -> int:
        return math.floor(x / self.col_w)

    def row_of(self, y: float) -> int:
        """The row a center ``y`` lies in; rows are centered on multiples of ``row_h``."""
        return math.floor(y / self.row_h + 0.5)

    def rows_covered(self, y: float, height: float) -> range:
        """Rows a body centered on ``y`` and ``height`` tall reaches into."""
        top = math.floor((y - height / 2) / self.row_h + 0.5)
        bottom = math.ceil((y + height / 2) / self.row_h - 0.5)
        return range(top, max(top, bottom) + 1)

    # -- building ------------------------------------------------------

    def span_of(self, positions: Iterable[tuple[float, float]]) -> Span | None:
        cells = [(self.col_of(x), self.row_of(y)) for x, y in positions]
        if not cells:
            return None
        cols = [c for c, _ in cells]
        rows = [r for _, r in cells]
        return Span(min(cols), max(cols), min(rows), max(rows))

    @classmethod
    def build(
        cls,
        spans: Iterable[Span],
        occupied: Iterable[tuple[float, float, float]],
        *,
        col_w: float,
        row_h: float,
    ) -> FoldMap:
        """The fold for these collapsed ``spans``, given what is drawn.

        ``occupied`` is ``(x, y, height)`` in stored coordinates for every item that
        is drawn: ordinary nodes, call nodes, and each collapsed node at its span's
        top-left cell. Nothing hidden is ever occupied, so no drawn item is lost.
        """
        base = cls(col_w, row_h)
        used_cols: set[int] = set()
        used_rows: set[int] = set()
        for x, y, height in occupied:
            used_cols.add(base.col_of(x))
            used_rows.update(base.rows_covered(y, height))
        cols: set[int] = set()
        rows: set[int] = set()
        for span in spans:
            cols.update(c for c in range(span.col0 + 1, span.col1 + 1) if c not in used_cols)
            rows.update(r for r in range(span.row0 + 1, span.row1 + 1) if r not in used_rows)
        return cls(col_w, row_h, tuple(sorted(cols)), tuple(sorted(rows)))

    # -- mapping -------------------------------------------------------

    @property
    def is_identity(self) -> bool:
        return not self.hidden_cols and not self.hidden_rows

    def to_display(self, x: float, y: float) -> tuple[float, float]:
        """Where a stored position is drawn. The offset within a cell is kept."""
        dx = bisect_left(self.hidden_cols, self.col_of(x)) * self.col_w
        dy = bisect_left(self.hidden_rows, self.row_of(y)) * self.row_h
        return x - dx, y - dy

    def to_stored(self, x: float, y: float) -> tuple[float, float]:
        """The stored position drawn at ``(x, y)``: always in a kept column and row."""
        return x + _skip(self.hidden_cols, self.col_of(x)) * self.col_w, y + (
            _skip(self.hidden_rows, self.row_of(y)) * self.row_h
        )


def _skip(hidden: tuple[int, ...], index: int) -> int:
    """How many hidden lines to step over so drawn line ``index`` lands on a kept one."""
    moved = index
    for h in hidden:
        if h <= moved:
            moved += 1
        else:
            break
    return moved - index
