"""Where a column's heading, rule and cards sit across its width.

A card's frame starts a grip's gutter in from the column's left edge, with the grip in the
gutter beside it. Under a rule spanning the whole column, every card looked narrower than
its column. So a titled column insets its heading, rule and banner by that gutter on both
sides, and gives its cards the same gutter on the right: the frames end where the rule
does, the grips sit outside the column as drawn, and the column stays centered.

The tests measure positions against each other rather than pinning pixels, so they keep
meaning if a margin changes.
"""

from bartleby.qt.game_embed.card_columns import CardColumn
from bartleby.qt.offscreen_warmup import WARMED_PROPERTY
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget

from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard

PROFILE = {"name": "Farm Team", "accounts": ["someone@example.com"]}


def _shown_column(qapp, title: str) -> CardColumn:
    column = CardColumn(title, "hint", "Add launch profile")
    column.set_cards([ProfileCard(dict(PROFILE), [])])
    column.resize(400, 400)
    column.show()
    qapp.processEvents()
    return column


def _span(widget, column: CardColumn) -> tuple[int, int]:
    """*widget*'s left and right edges, in *column*'s coordinates."""
    left = widget.mapTo(column, QPoint(0, 0)).x()
    return left, left + widget.width()


def test_a_cards_frame_spans_exactly_the_rule(qapp):
    column = _shown_column(qapp, "Launch Profiles")
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]

    assert _span(column.cards[0].frame, column) == _span(rule, column)
    column.close()


def test_the_grip_sits_outside_the_rule_and_the_rule_is_centered(qapp):
    column = _shown_column(qapp, "Launch Profiles")
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]
    rule_left, rule_right = _span(rule, column)

    assert _span(column.cards[0].grip, column)[1] <= rule_left
    assert rule_left == column.width() - rule_right
    column.close()


def test_the_add_card_lines_up_with_the_cards_above_it(qapp):
    column = _shown_column(qapp, "Launch Profiles")

    assert _span(column.add_card, column)[1] == _span(column.cards[0].frame, column)[1]
    column.close()


def test_a_scroll_bar_takes_its_width_from_the_gutter_not_the_cards(qapp):
    """It narrows the viewport, and taking the whole gutter out of what was left put the
    cards a bar's width short of the rule, off center under it."""
    column = CardColumn("Launch Profiles", "hint", "Add launch profile")
    column.set_cards([ProfileCard({"name": f"Profile {i}", "accounts": ["someone@example.com"]}, []) for i in range(8)])
    column.resize(400, 250)
    column.show()
    qapp.processEvents()
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]
    bar = column.scroll_area.verticalScrollBar()

    assert bar.isVisible(), "the column has to overflow for this to test anything"
    assert _span(column.cards[0].frame, column) == _span(rule, column)
    assert _span(bar, column)[0] >= _span(rule, column)[1], "the bar sits in the right gutter"
    column.close()


def test_a_scroll_bar_that_has_gone_gives_its_width_back(qapp):
    """Qt hides the bar's container, not the bar, so a column that had overflowed once
    went on giving up the bar's width after the bar was gone -- in the Game tab, the
    column without a bar ran past its rule beside the one with a bar."""
    column = CardColumn("Launch Profiles", "hint", "Add launch profile")
    column.set_cards([ProfileCard({"name": f"Profile {i}", "accounts": ["someone@example.com"]}, []) for i in range(8)])
    column.resize(400, 250)
    column.show()
    qapp.processEvents()
    bar = column.scroll_area.verticalScrollBar()
    assert bar.isVisible(), "the column has to overflow first for this to test anything"

    column.resize(400, 2000)
    qapp.processEvents()
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]

    assert not bar.isVisibleTo(column.scroll_area)
    assert _span(column.cards[0].frame, column) == _span(rule, column)
    column.close()


def test_an_untitled_column_keeps_its_full_width(qapp):
    """No rule to line up with, so no right gutter narrowing the cards: the Players tab's
    column is in a sidebar."""
    column = _shown_column(qapp, "")

    assert _span(column.cards[0].frame, column)[1] == column.scroll_area.viewport().width()
    column.close()


# ---------------------------------------------------------------------------
# The first offscreen render is paid on a show (bartleby.qt.offscreen_warmup)
# ---------------------------------------------------------------------------

_WARMUP_ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
]


def test_showing_a_card_column_warms_its_window(qapp):
    column = CardColumn("Accounts", "hint", "Add account")
    column.set_cards([AccountCard(a, [], _WARMUP_ACCOUNTS) for a in _WARMUP_ACCOUNTS])
    host = QWidget()
    QVBoxLayout(host).addWidget(column)

    host.show()
    for _ in range(3):
        qapp.processEvents()

    assert host.property(WARMED_PROPERTY) is True, (
        "a drag would otherwise pay Qt's first offscreen render between two mouse moves"
    )
