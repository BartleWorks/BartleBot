"""Where a column's heading, rule and cards sit across its width.

A card's frame starts a grip's gutter in from the column's left edge, with the grip in the
gutter beside it. Under a rule spanning the whole column, every card looked narrower than
its column. So a titled column insets its heading, rule and banner by that gutter on both
sides, and gives its cards the same gutter on the right: the frames end where the rule
does, the grips sit outside the column as drawn, and the column stays centered.

The tests measure positions against each other rather than pinning pixels, so they keep
meaning if a margin changes.
"""

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QFrame

from bartlebot.gui.game_embed.card_columns import CardColumn
from bartlebot.gui.game_embed.launch_cards import ProfileCard

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


def test_an_untitled_column_keeps_its_full_width(qapp):
    """No rule to line up with, so no right gutter narrowing the cards: the Players tab's
    column is in a sidebar."""
    column = _shown_column(qapp, "")

    assert _span(column.cards[0].frame, column)[1] == column.scroll_area.viewport().width()
    column.close()
