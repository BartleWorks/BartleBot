"""Folding the canvas around collapsed regions (#67). Pure: no Qt.

A collapsed region is drawn as one node, and the grid columns and rows only its
members used are closed up, the way an editor folds lines of code. **Stored
positions are always the full expanded layout**; this module maps between them
and where things are drawn. Collapsing therefore writes one flag and moves
nothing, and expanding is an exact reverse.

A column is hidden when it lies inside a collapsed region's span -- past the
column its node is drawn in -- and nothing drawn occupies it. Rows follow the
same rule on their own, with one more condition: hiding a row must not bring a
node into contact with one drawn above it in its column. A collapsed node grows
20px a port row, so from three it is taller than a row can hold, and the row under
it stays open when something sits below it -- only then, and only in that case,
since a node is never wider than a column and a tall one far away touches nothing
(#159). A gap outside every collapsed span is the author's and is never touched,
and a column another visible node shares stays, gap and all.

The map is derived from what the scene draws every time it is built and never
stored, like display ids: undo, a recompose or a paste rebuilds it with the scene.

**A move is settled here too** (#159), by :func:`settle_move`, never by converting
each moved item through the map on its own. The map is not a translation: the
hidden lines between two items make a drag of one cell on screen several in the
stored layout for one of them and not the other, which pulled two side-by-side
collapsed regions on top of each other. Instead a collapsed region moves the way a
folded block of code is cut and pasted: the hidden lines it leaves are deleted and
its own are inserted, whole, wherever it lands, so nothing ever lies inside its
expanded span and the screen shows exactly what was dropped.
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
        """The row a top ``y`` lies in: the nearest multiple of ``row_h``, where a snapped top sits."""
        return math.floor(y / self.row_h + 0.5)

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

        Rows are hidden top to bottom, each only if it leaves every pair of nodes
        in one column -- the only nodes a body can reach -- with the upper one's
        height between their tops, or as much as they had if the author put them closer.
        Which rows between two nodes are hidden does not matter, only how many.
        """
        base = cls(col_w, row_h)
        occupied = list(occupied)
        used_cols = {base.col_of(x) for x, _, _ in occupied}
        used_rows = {base.row_of(y) for _, y, _ in occupied}
        cols: set[int] = set()
        candidates: set[int] = set()
        for span in spans:
            cols.update(c for c in range(span.col0 + 1, span.col1 + 1) if c not in used_cols)
            candidates.update(r for r in range(span.row0 + 1, span.row1 + 1) if r not in used_rows)

        # (upper row, lower row, distance apart, distance they need) per pair in a column.
        # Positions are tops, so the pair needs the upper node's height between them.
        by_col: dict[int, list[tuple[float, float]]] = {}
        for x, y, height in occupied:
            by_col.setdefault(base.col_of(x), []).append((y, height))
        pairs: list[tuple[int, int, float, float]] = []
        for column in by_col.values():
            column.sort()
            for i, (y0, h0) in enumerate(column):
                for y1, _h1 in column[i + 1 :]:
                    gap = y1 - y0
                    pairs.append((base.row_of(y0), base.row_of(y1), gap, min(gap, h0)))
        hidden_between = [0] * len(pairs)
        rows: list[int] = []
        for r in sorted(candidates):
            spanning = [i for i, (top, bottom, _, _) in enumerate(pairs) if top < r < bottom]
            if all(pairs[i][2] - (hidden_between[i] + 1) * row_h >= pairs[i][3] for i in spanning):
                rows.append(r)
                for i in spanning:
                    hidden_between[i] += 1
        return cls(col_w, row_h, tuple(sorted(cols)), tuple(rows))

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


@dataclass(frozen=True)
class Placed:
    """One drawn item, for :func:`settle_move`. Positions are ``(left x, top y)``, as stored."""

    key: str
    stored: tuple[float, float] | None
    height: float
    # A collapsed region's members' extent; its node sits at the span's top-left.
    span: Span | None = None
    # Where it was dropped, in the fold's drawn coordinates; None if it did not move.
    drawn_to: tuple[float, float] | None = None


def settle_move(fold: FoldMap, items: Iterable[Placed]) -> dict[str, tuple[float, float]]:
    """The stored position of every drawn item a move changes, keyed as given.

    ``fold`` is the one the move was drawn through, and ``items`` everything drawn,
    moved or not. A collapsed region is cut and pasted, a whole line at a time:

    1. The hidden columns and rows a moved region leaves behind are deleted, unless a
       region staying put also spans them. They were hidden, so nothing on screen moves.
    2. Every moved item is stored where it was dropped, read through the fold of what
       stayed put -- the screen the drop was aimed at.
    3. Each moved region gets its expanded span back as whole columns after its own
       column, and whole rows after its own row, less what a region staying on that
       line already holds open. They are empty and inside its span, so they are hidden.

    The caller moves a collapsed node's members by the delta of its position.
    """
    items = list(items)
    moved = [p for p in items if p.drawn_to is not None]
    if not moved:
        return {}
    still = [p for p in items if p.drawn_to is None and p.stored is not None]
    col_w, row_h = fold.col_w, fold.row_h

    moved_spans = [p.span for p in moved if p.span is not None]
    still_spans = [p.span for p in still if p.span is not None]
    del_cols = [
        c
        for c in fold.hidden_cols
        if any(s.col0 < c <= s.col1 for s in moved_spans) and not any(s.col0 <= c <= s.col1 for s in still_spans)
    ]
    del_rows = [
        r
        for r in fold.hidden_rows
        if any(s.row0 < r <= s.row1 for s in moved_spans) and not any(s.row0 <= r <= s.row1 for s in still_spans)
    ]

    pos: dict[str, tuple[float, float]] = {}
    spans: dict[str, Span] = {}
    for p in still:
        assert p.stored is not None
        x, y = p.stored
        pos[p.key] = (
            x - bisect_left(del_cols, fold.col_of(x)) * col_w,
            y - bisect_left(del_rows, fold.row_of(y)) * row_h,
        )
        if p.span is not None:
            dc, dr = bisect_left(del_cols, p.span.col0), bisect_left(del_rows, p.span.row0)
            spans[p.key] = Span(p.span.col0 - dc, p.span.col1 - dc, p.span.row0 - dr, p.span.row1 - dr)

    left = FoldMap.build(spans.values(), [(*pos[p.key], p.height) for p in still], col_w=col_w, row_h=row_h)
    for p in moved:
        assert p.drawn_to is not None
        x, y = left.to_stored(*p.drawn_to)
        pos[p.key] = (x, y)
        if p.span is not None:
            c, r = fold.col_of(x), fold.row_of(y)
            spans[p.key] = Span(c, c + p.span.col1 - p.span.col0, r, r + p.span.row1 - p.span.row0)

    moving = {p.key for p in moved}
    _open_lines(pos, spans, moving, fold, vertical=False)
    _open_lines(pos, spans, moving, fold, vertical=True)

    changed = {}
    for p in items:
        new = pos.get(p.key)
        if new is not None and (p.stored is None or new != p.stored):
            changed[p.key] = new
    return changed


def _open_lines(
    pos: dict[str, tuple[float, float]], spans: dict[str, Span], moving: set[str], fold: FoldMap, *, vertical: bool
) -> None:
    """Insert the whole columns (or rows) each moved region's span needs past its own line, in place."""
    need: dict[int, int] = {}
    held: dict[int, int] = {}
    for key, s in spans.items():
        line, extent = (s.row0, s.row1 - s.row0) if vertical else (s.col0, s.col1 - s.col0)
        need[line] = max(need.get(line, 0), extent)
        if key not in moving:
            held[line] = max(held.get(line, 0), extent)
    # Right to left (bottom to top), so a line not yet handled is never shifted off itself.
    for line in sorted(need, reverse=True):
        n = need[line] - held.get(line, 0)
        if n <= 0:
            continue
        for key, (x, y) in pos.items():
            if vertical and fold.row_of(y) > line:
                pos[key] = (x, y + n * fold.row_h)
            elif not vertical and fold.col_of(x) > line:
                pos[key] = (x + n * fold.col_w, y)
        for key, s in spans.items():
            if vertical and s.row0 > line:
                spans[key] = Span(s.col0, s.col1, s.row0 + n, s.row1 + n)
            elif not vertical and s.col0 > line:
                spans[key] = Span(s.col0 + n, s.col1 + n, s.row0, s.row1)


def _skip(hidden: tuple[int, ...], index: int) -> int:
    """How many hidden lines to step over so drawn line ``index`` lands on a kept one."""
    moved = index
    for h in hidden:
        if h <= moved:
            moved += 1
        else:
            break
    return moved - index
