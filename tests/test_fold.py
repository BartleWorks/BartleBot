"""The fold that closes up a collapsed region's columns and rows (#67). Pure."""

from __future__ import annotations

from bartlebot.gui.canvas.fold import FoldMap, Placed, Span, settle_move

W, H = 440.0, 90.0


def _fold(spans, occupied) -> FoldMap:
    return FoldMap.build(spans, occupied, col_w=W, row_h=H)


def _row(cols, y: float = 0.0):
    return [(c * W, y, 70.0) for c in cols]


def test_nothing_collapsed_is_the_identity():
    fold = _fold([], _row(range(10)))
    assert fold.is_identity
    assert fold.to_display(1320.0, 90.0) == (1320.0, 90.0)
    assert fold.to_stored(1320.0, 90.0) == (1320.0, 90.0)


def test_a_linear_region_hides_its_span_less_one_and_everything_past_it_slides_in():
    """Atlantea's shape: one row, a region over columns 2..9, drawn at column 2."""
    visible = [0, 1, 2, 10, 11, 12]  # column 2 holds the collapsed node itself
    fold = _fold([Span(2, 9, 0, 0)], _row(visible))
    assert fold.hidden_cols == tuple(range(3, 10))
    assert fold.hidden_rows == ()
    assert [fold.to_display(c * W, 0.0)[0] / W for c in visible] == [0, 1, 2, 3, 4, 5]


def test_two_collapsed_regions_compose():
    fold = _fold([Span(1, 3, 0, 0), Span(5, 8, 0, 0)], _row([0, 1, 4, 5, 9]))
    assert fold.hidden_cols == (2, 3, 6, 7, 8)
    assert [fold.to_display(c * W, 0.0)[0] / W for c in [0, 1, 4, 5, 9]] == [0, 1, 2, 3, 4]


def test_a_column_a_visible_node_shares_stays():
    fold = _fold([Span(2, 6, 0, 0)], _row([0, 1, 2, 7]) + [(4 * W, 3 * H, 70.0)])
    assert fold.hidden_cols == (3, 5, 6)


def test_a_two_row_region_hides_its_second_row_too():
    fold = _fold([Span(2, 4, 0, 1)], _row([0, 1, 2, 5]))
    assert fold.hidden_rows == (1,)
    assert fold.to_display(5 * W, 2 * H) == (3 * W, H)


def test_a_row_a_tall_node_reaches_into_stays():
    tall = [(0.0, 0.0, 70.0), (W, 0.0, 170.0)]  # 170 tall, centered on row 0: reaches rows -1..1
    fold = _fold([Span(2, 2, 0, 2)], tall)
    assert fold.hidden_rows == (2,)


def test_an_authors_gap_outside_any_collapsed_span_is_kept():
    fold = _fold([Span(6, 8, 0, 0)], _row([0, 3, 6, 9]))  # columns 1, 2, 4, 5 are empty on purpose
    assert fold.hidden_cols == (7, 8)
    assert fold.to_display(3 * W, 0.0) == (3 * W, 0.0)


def test_every_drawn_position_maps_back_to_itself():
    visible = [
        (c * W + dx, r * H + dy, 70.0) for c, r, dx, dy in [(0, 0, 0, 0), (1, 0, 13, -7), (7, 2, 0, 20), (9, 3, 5, 0)]
    ]
    fold = _fold([Span(1, 6, 0, 1), Span(7, 8, 2, 2)], visible)
    for x, y, _ in visible:
        assert fold.to_stored(*fold.to_display(x, y)) == (x, y)


def test_a_drop_never_lands_in_a_hidden_column_or_row():
    fold = _fold([Span(1, 5, 0, 3)], _row([0, 1, 6]))
    for c in range(-3, 12):
        for r in range(-2, 6):
            x, y = fold.to_stored(c * W, r * H)
            assert fold.col_of(x) not in fold.hidden_cols
            assert fold.row_of(y) not in fold.hidden_rows


def test_an_offset_within_a_cell_is_kept():
    fold = _fold([Span(1, 3, 0, 0)], _row([0, 1, 4]))
    assert fold.to_display(4 * W + 17.0, 23.0) == (2 * W + 17.0, 23.0)


def test_negative_columns_fold_like_any_other():
    fold = _fold([Span(-3, -1, 0, 0)], _row([-4, -3, 0, 1]))
    assert fold.hidden_cols == (-2, -1)
    assert fold.to_display(0.0, 0.0) == (-2 * W, 0.0)
    assert fold.to_stored(-2 * W, 0.0) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Settling a move (#159): a collapsed region is cut and pasted, whole lines at a time
# ---------------------------------------------------------------------------


def _node(key, col, row=0, **kw) -> Placed:
    return Placed(key, (col * W, row * H), 70.0, **kw)


def _region(key, col0, col1, row0=0, row1=0, **kw) -> Placed:
    return Placed(key, (col0 * W, row0 * H), 70.0, Span(col0, col1, row0, row1), **kw)


def _fold_of(items) -> FoldMap:
    return _fold([p.span for p in items if p.span], [(*p.stored, p.height) for p in items])


def _drop(fold: FoldMap, p: Placed, d_cols: int, d_rows: int = 0) -> Placed:
    """``p`` dragged on screen by whole cells, as the canvas reports it."""
    x, y = fold.to_display(*p.stored)
    return Placed(p.key, p.stored, p.height, p.span, (x + d_cols * W, y + d_rows * H))


def _settle(items, moves: dict[str, tuple[int, int]]):
    """Settle ``moves`` (key -> cells on screen); the settled items and the fold they are now drawn through."""
    fold = _fold_of(items)
    dropped = [_drop(fold, p, *moves[p.key]) if p.key in moves else p for p in items]
    new = settle_move(fold, dropped)
    after = []
    for p in items:
        x, y = new.get(p.key, p.stored)
        span = p.span
        if span is not None:
            dc, dr = fold.col_of(x) - span.col0, fold.row_of(y) - span.row0
            span = Span(span.col0 + dc, span.col1 + dc, span.row0 + dr, span.row1 + dr)
        after.append(Placed(p.key, (x, y), p.height, span))
    return fold, after, _fold_of(after)


def _cells(fold: FoldMap, items) -> dict[str, tuple[int, int]]:
    return {
        p.key: (fold.col_of(fold.to_display(*p.stored)[0]), fold.row_of(fold.to_display(*p.stored)[1])) for p in items
    }


def _assert_expandable(items) -> None:
    """Nothing lies inside a collapsed region's expanded span but its own node."""
    for r in items:
        if r.span is None:
            continue
        for p in items:
            if p is r:
                continue
            c, row = int(p.stored[0] // W), round(p.stored[1] / H)
            assert not (r.span.col0 <= c <= r.span.col1 and r.span.row0 <= row <= r.span.row1), (p.key, r.key)
            if p.span is not None:
                s = p.span
                overlap = (
                    s.col0 <= r.span.col1 and r.span.col0 <= s.col1 and s.row0 <= r.span.row1 and r.span.row0 <= s.row1
                )
                assert not overlap, (p.key, r.key)


def test_nothing_moved_settles_nothing():
    items = [_region("a", 0, 3), _node("x", 4)]
    assert settle_move(_fold_of(items), items) == {}


def test_two_side_by_side_regions_moved_together_stay_side_by_side():
    """The issue's own case: dragged two cells left, they used to expand on top of each other."""
    items = [_region("a", 0, 3), _region("b", 4, 7), _node("z", 9)]
    before, after, fold = _settle(items, {"a": (-2, 0), "b": (-2, 0)})
    by = {p.key: p for p in after}
    assert by["b"].span.col0 - by["a"].span.col0 == 4  # exactly as they were, a column apart
    _assert_expandable(after)
    shown = _cells(fold, after)
    was = _cells(before, items)
    assert shown == {"a": (was["a"][0] - 2, 0), "b": (was["b"][0] - 2, 0), "z": was["z"]}


def test_a_region_dropped_into_a_one_cell_gap_opens_its_span_there():
    """The node past the gap and everything past it move over, and the screen shows exactly the drop."""
    items = [_region("a", 0, 3), _node("gap", 6), _node("x", 8), _node("far", 9, 4)]
    before, after, fold = _settle(items, {"a": (2, 0)})  # drawn at 0, dropped at 2: the empty cell before gap
    by = {p.key: p for p in after}
    assert by["gap"].stored[0] / W == by["a"].span.col1 + 1
    _assert_expandable(after)
    was = _cells(before, items)
    assert _cells(fold, after) == {**was, "a": (2, 0)}


def test_a_region_dropped_right_before_a_node_lands_its_members_clear_of_it():
    items = [_region("a", 0, 3), _node("x", 10), _node("y", 11)]
    before, after, fold = _settle(items, {"a": (6, 0)})  # x is drawn at 7: a lands just left of it
    by = {p.key: p for p in after}
    assert by["x"].stored[0] / W == by["a"].span.col1 + 1
    _assert_expandable(after)
    was = _cells(before, items)
    assert _cells(fold, after) == {**was, "a": (6, 0)}


def test_a_region_three_rows_deep_dropped_above_a_node_pushes_whole_rows():
    items = [_region("a", 10, 12, 6, 8), _node("x", 2, 1), _node("left", -3, 2)]
    before, after, fold = _settle(items, {"a": (-8, -6)})  # drawn at (10, 6), dropped at (2, 0): just above x
    by = {p.key: p for p in after}
    assert by["x"].stored[1] / H == by["a"].span.row1 + 1
    assert by["left"].stored[1] / H == 4  # whole rows: a node in another column moves down too
    _assert_expandable(after)
    was = _cells(before, items)
    assert _cells(fold, after) == {**was, "a": (2, 0)}


def test_inserting_a_column_in_front_of_a_region_carries_its_span():
    """What ``_open_column_at`` does: everything from the region on moves one cell right."""
    items = [_node("p", -1), _region("a", 0, 3), _node("x", 4)]
    before, after, fold = _settle(items, {"a": (1, 0), "x": (1, 0)})
    by = {p.key: p for p in after}
    assert by["a"].span == Span(1, 4, 0, 0)
    assert by["x"].stored == (5 * W, 0.0)
    _assert_expandable(after)


def test_moving_a_region_away_closes_the_space_it_held():
    """Its hidden columns are deleted, so what was past it does not jump on screen."""
    items = [_region("a", 0, 3), _node("x", 4), _node("y", 5)]
    before, after, fold = _settle(items, {"a": (5, 0)})  # past y
    was = _cells(before, items)
    assert _cells(fold, after) == {**was, "a": (was["a"][0] + 5, 0)}
    by = {p.key: p for p in after}
    assert by["x"].stored == (W, 0.0)
    _assert_expandable(after)


def test_moving_a_region_and_back_is_a_round_trip():
    items = [_node("p", -1), _region("a", 0, 3, 0, 1), _node("x", 4), _node("y", 6, 3)]
    _, there, _ = _settle(items, {"a": (2, 2)})
    _, back, _ = _settle(there, {"a": (-2, -2)})
    assert [p.stored for p in back] == [p.stored for p in items]


def test_a_region_staying_on_the_same_column_holds_room_already():
    """Dropped below a wider region in its column, nothing more is opened: no gap appears on screen."""
    items = [_region("big", 0, 4), _node("x", 5), _region("a", 10, 12, 3, 3)]
    before, after, fold = _settle(items, {"a": (-6, 0)})  # drawn at (6, 3), dropped at (0, 3): under big
    by = {p.key: p for p in after}
    assert by["x"].stored == (5 * W, 0.0)
    assert by["a"].span == Span(0, 2, 3, 3)
    _assert_expandable(after)
    assert _cells(fold, after) == {"big": (0, 0), "x": (1, 0), "a": (0, 3)}


def test_plain_nodes_either_side_of_a_region_staying_put_land_where_dropped():
    items = [_node("x", 0), _region("c", 1, 4), _node("y", 5)]
    before, after, fold = _settle(items, {"x": (2, 0), "y": (2, 0)})
    was = _cells(before, items)
    assert _cells(fold, after) == {"x": (2, 0), "c": was["c"], "y": (4, 0)}
    _assert_expandable(after)
