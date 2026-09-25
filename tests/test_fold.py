"""The fold that closes up a collapsed region's columns and rows (#67). Pure."""

from __future__ import annotations

from bartlebot.gui.canvas.fold import FoldMap, Span

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
    tall = [(0.0, 0.0, 70.0), (W, 0.0, 170.0)]  # 170 tall, centred on row 0: reaches rows -1..1
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
