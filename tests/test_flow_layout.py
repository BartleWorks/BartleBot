"""Tests for the wrapping layout behind the tag pills.

``flow_geometry`` holds the arithmetic and is pure, so the wrapping can be checked
at any width without a screen — the same split as ``popup_position`` in
tests/test_picker_popup.py. The ``QLayout`` around it is mostly Qt plumbing, but
not entirely: what it reports as *preferred* against *minimum*, and whether it
counts hidden items, both decide how the bot header's byline wraps, so those have
tests of their own at the bottom.
"""

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QWidget

from bartlebot.gui.widgets.flow_layout import block_offset, flow_container, flow_geometry


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


def test_a_short_item_is_centred_in_a_row_a_taller_one_set_the_height_of():
    """The byline puts 18px tag chips beside a 27px "Rewards" button.  Hung from
    the top of the row the chips read as misaligned rather than as a row of two
    different things."""
    positions, height = flow_geometry([QSize(40, 14), QSize(40, 22)], 300)

    assert [p.y() for p in positions] == [4, 0]
    assert height == 22


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


# ---------------------------------------------------------------------------
# The QLayout around it — two answers that are not the same answer
# ---------------------------------------------------------------------------
#
# These need a QApplication because they are about real widgets, but what they
# assert is not Qt plumbing: preferred and minimum are different questions, and
# for a long while this layout answered both with the minimum.


def _host(*widths: int):
    """A flow container holding one fixed-size widget per width."""
    host, layout = flow_container(spacing=4)
    for w in widths:
        child = QWidget()
        child.setFixedSize(w, 14)
        layout.addWidget(child)
    return host, layout


def test_the_preferred_width_is_one_row_of_everything(qapp):
    """Answering with the minimum is what made two tag pills stack vertically in a
    bar 1400px wide: the parent handed the widget the widest single pill, because
    that was all it had asked for."""
    _host_w, layout = _host(40, 60)

    assert layout.sizeHint().width() == 104  # 40 + 4 + 60


def test_the_minimum_width_is_still_the_widest_single_item(qapp):
    """A minimum of one whole row would make the parent demand that width, and the
    sidebar would then never narrow enough for the layout to do its job."""
    _host_w, layout = _host(40, 60)

    assert layout.minimumSize().width() == 60


def test_a_hidden_item_reserves_nothing(qapp):
    """The tag field parks its "Add…" buttons in this layout and hides them outside
    Edit mode. Counted in, they pushed the pills onto a second row in a bar with
    room to spare."""
    host, layout = _host(40, 60)
    host.layout().itemAt(1).widget().hide()

    assert layout.sizeHint().width() == 40


def test_hiding_an_item_moves_the_ones_after_it_up(qapp):
    host, layout = _host(60, 60, 60)
    host.resize(130, 100)
    layout.setGeometry(host.rect())
    assert layout.itemAt(2).widget().y() > 0  # third item was on row two

    layout.itemAt(0).widget().hide()
    layout.setGeometry(host.rect())

    assert layout.itemAt(2).widget().y() == 0  # now the two visible ones share a row


def test_insert_widget_places_it_before_the_ones_already_there(qapp):
    """The strategy card's pill row grows in the middle: pills come and go beside a
    "Fights…" button that must stay at the end of the row rather than walking along
    it as they arrive."""
    _host_w, layout = _host(60)
    button = layout.itemAt(0).widget()
    pill = QWidget()
    pill.setFixedSize(40, 14)

    layout.insertWidget(0, pill)

    assert layout.count() == 2
    assert [layout.itemAt(i).widget() for i in range(2)] == [pill, button]


def test_an_inserted_widget_is_reparented_like_an_added_one(qapp):
    """``addWidget`` first, then move the item: the reparenting and ownership are
    things ``QLayout`` alone knows how to do."""
    host, layout = _host(60)
    pill = QWidget()
    pill.setFixedSize(40, 14)

    layout.insertWidget(0, pill)

    assert pill.parent() is host


# ---------------------------------------------------------------------------
# Where the block sits when the rect is taller than the rows need
# ---------------------------------------------------------------------------


def test_rows_stay_at_the_top_by_default():
    """A keyword list or a card grid handed a tall panel belongs at the top of it."""
    assert block_offset(40, 100, centre=False) == 0


def test_rows_centre_in_the_slack_when_asked():
    assert block_offset(40, 100, centre=True) == 30


def test_a_block_that_exactly_fills_its_rect_is_not_moved():
    """Which is the normal case: ``flow_container`` sizes the widget to its own wrapped
    height, so the offset only ever comes up where a parent deliberately gave it more."""
    assert block_offset(40, 40, centre=True) == 0


def test_a_block_taller_than_its_rect_is_never_pulled_up_off_the_top():
    """Clipping the last row is visible; hiding the first one is not."""
    assert block_offset(100, 40, centre=True) == 0


def test_a_centred_flow_puts_its_single_row_on_the_rect_s_centre(qapp):
    """The bot header's byline: pills a few pixels shorter than the rect they are given.

    Three spare pixels all landing below them left the pill above the author text beside
    it, which reads as a misalignment rather than as a row of two different things.
    """
    host, layout = flow_container(centre_rows=True)
    for _ in range(2):
        child = QWidget()
        child.setFixedSize(40, 20)
        layout.addWidget(child)
    host.resize(200, 30)
    layout.setGeometry(host.rect())

    tops = {layout.itemAt(i).widget().y() for i in range(layout.count())}
    assert tops == {5}


def test_the_same_flow_left_uncentred_hangs_from_the_top(qapp):
    host, layout = flow_container()
    for _ in range(2):
        child = QWidget()
        child.setFixedSize(40, 20)
        layout.addWidget(child)
    host.resize(200, 30)
    layout.setGeometry(host.rect())

    tops = {layout.itemAt(i).widget().y() for i in range(layout.count())}
    assert tops == {0}
