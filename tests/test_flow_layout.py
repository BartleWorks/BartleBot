"""Tests for the wrapping layout behind the tag pills.

Only ``flow_geometry`` is covered: it holds the arithmetic and is pure, so the
wrapping can be checked at any width without a screen — the same split as
``popup_position`` in tests/test_picker_popup.py. The ``QLayout`` around it is
Qt plumbing.
"""

from PyQt6.QtCore import QSize

from bartlebot.gui.widgets.flow_layout import flow_geometry


def _sizes(*widths: int, height: int = 14) -> list[QSize]:
    return [QSize(w, height) for w in widths]


def test_items_that_fit_stay_on_one_row():
    positions, height = flow_geometry(_sizes(40, 40, 40), 300, h_spacing=4, v_spacing=4)

    assert [p.y() for p in positions] == [0, 0, 0]
    assert [p.x() for p in positions] == [0, 44, 88]
    assert height == 14


def test_an_item_that_does_not_fit_starts_a_new_row():
    positions, height = flow_geometry(_sizes(60, 60, 60), 130, h_spacing=4, v_spacing=4)

    assert [p.y() for p in positions] == [0, 0, 18]
    assert positions[2].x() == 0
    assert height == 32


def test_the_row_height_is_the_tallest_item_in_it():
    positions, height = flow_geometry([QSize(40, 14), QSize(40, 22), QSize(300, 14)], 100)

    assert positions[2].y() == 26  # 22 + 4 spacing, not 14 + 4
    assert height == 40


def test_an_item_wider_than_the_layout_still_gets_a_row():
    """A pill for a long tag name is better clipped by the sidebar than absent from
    it — and an item that never places at all is the alternative."""
    positions, height = flow_geometry(_sizes(40, 500), 100)

    assert positions[1].x() == 0
    assert positions[1].y() > 0
    assert height > 14


def test_the_first_item_never_wraps_however_narrow_the_layout():
    positions, _ = flow_geometry(_sizes(500), 10)

    assert positions[0].x() == 0
    assert positions[0].y() == 0


def test_no_items_is_no_height():
    positions, height = flow_geometry([], 300)

    assert positions == []
    assert height == 0


def test_narrowing_the_width_adds_rows_rather_than_dropping_items():
    """The sidebar is resizable and a bot can carry all eighteen tags."""
    sizes = _sizes(*([50] * 18))

    wide, wide_h = flow_geometry(sizes, 600)
    narrow, narrow_h = flow_geometry(sizes, 200)

    assert len(wide) == len(narrow) == 18
    assert narrow_h > wide_h
