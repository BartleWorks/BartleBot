"""What a card reorder *looks like* while it is happening.

The ordering contract is next door in ``test_card_column_reorder.py`` and was never the
problem: the drag worked, and the cards really did rearrange under the pointer. What it
did not do was say which card was being held, so the only evidence a drag was under way
was the *other* cards apparently moving on their own — which is how a working gesture
came to read as a surprise.

Four signals were added, and each answers a different question. This file holds one guard
per question, because they fail independently and three of the four are invisible to any
test that only checks the resulting order:

- *can I drag this at all* — the grip, and the cursor that changes only over it;
- *what am I holding* — the card lifted out of the layout, following the pointer, with a
  placeholder holding its hole open;
- *what just changed* — displaced cards sliding rather than teleporting;
- *where can I put it* — auto-scroll at the ends of a column taller than its viewport.

The cursor guards are the ones worth keeping honest. An override cursor is application
state on a stack, so an unbalanced push leaves a closed hand over the whole app with no
drag under it, and a drag can end three ways: by release, by a rebuild arriving beneath
it, or by the column being closed.
"""

from __future__ import annotations

import re

from bartleby.qt.theming.icon_utils import ICONS_DIR
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from bartlebot.gui.game_embed.card_columns import (
    AUTOSCROLL_MARGIN,
    CARD_SETTLE_MS,
    CARD_SHIFT_MS,
    CardColumn,
    _DropPlaceholder,
)
from bartlebot.gui.game_embed.launch_cards import AccountCard, ProfileCard

ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
    {"username": "three@example.com", "alias": "Three"},
    {"username": "four@example.com", "alias": "Four"},
    {"username": "five@example.com", "alias": "Five"},
    {"username": "six@example.com", "alias": "Six"},
]


def _column(qapp, count: int = 3, height: int = 600):
    """A shown column of *count* account cards, and the host holding it alive.

    Shown and pumped because every assertion here is about real geometry — an unshown
    card has no height, so nothing would move and nothing would need to.
    """
    accounts = ACCOUNTS[:count]
    column = CardColumn("Accounts", "hint", "Add account")
    column.set_cards([AccountCard(a, [], accounts) for a in accounts])
    host = QWidget()
    QVBoxLayout(host).addWidget(column)
    host.resize(300, height)
    host.show()
    for _ in range(3):
        qapp.processEvents()
    return host, column


def _event(kind, card, dy: int, buttons):
    """One mouse event *dy* pixels down *card*, positioned against the card itself.

    Against the card even when it is delivered to the grip: the card is what travels, and
    where the pointer sits within it is what the column measures.

    Inside the card's *frame*, which starts past the drag handle's gutter: the gutter is
    not a hit target, so a body click aimed at the card's own origin would land on nothing.
    """
    local = getattr(card, "frame", card).geometry().topLeft() + QPoint(5, 5 + dy)
    return QMouseEvent(
        kind,
        local.toPointF(),
        card.mapToGlobal(local).toPointF(),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def _press(card) -> None:
    QApplication.sendEvent(card.grip, _event(QEvent.Type.MouseButtonPress, card, 0, Qt.MouseButton.LeftButton))


def _move(card, dy: int) -> None:
    QApplication.sendEvent(card.grip, _event(QEvent.Type.MouseMove, card, dy, Qt.MouseButton.LeftButton))


def _release(card, dy: int) -> None:
    QApplication.sendEvent(card.grip, _event(QEvent.Type.MouseButtonRelease, card, dy, Qt.MouseButton.NoButton))


def _land(column: CardColumn) -> None:
    """Wait out the settle: the flight home a released card makes before it is a row again.

    The drop is deliberately not instantaneous — the card is still lifted, the placeholder
    still holds its slot and nothing has been reported until it lands — so every assertion
    about the *state after* a drop has to wait for one.
    """
    QTest.qWait(CARD_SETTLE_MS * 2)
    assert column  # held: the settle drives widgets that die with it


def _rows(column: CardColumn):
    return column.scroll_area.widget().layout()


def _row_height(column: CardColumn) -> int:
    return column.cards[0].height() + _rows(column).spacing()


def _placeholder(column: CardColumn):
    layout = _rows(column)
    for i in range(layout.count()):
        widget = layout.itemAt(i).widget()
        if isinstance(widget, _DropPlaceholder):
            return widget
    return None


# ---------------------------------------------------------------------------
# Can I drag this at all
# ---------------------------------------------------------------------------


def test_every_card_has_a_grip_and_the_add_card_has_none(qapp):
    """The grip is what says a card can be dragged; the Add card cannot be."""
    host, column = _column(qapp)
    layout = _rows(column)
    add_card = layout.itemAt(layout.count() - 2).widget()

    assert all(card.grip is not None for card in column.cards)
    assert getattr(add_card, "grip", None) is None, "the Add card is the terminator, not a member"
    assert host


def test_the_open_hand_is_on_the_grip_and_nowhere_else_on_the_card(qapp):
    """The whole reason the grip exists.

    The cursor is how a surface says "this drags", and a card's primary verb is *launch* —
    so the announcement cannot be made over the card's face without advertising the wrong
    gesture. Putting it on twelve pixels is what lets both cursors be true at once.
    """
    host, column = _column(qapp)
    card = column.cards[0]

    assert card.grip.cursor().shape() == Qt.CursorShape.OpenHandCursor
    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert host


def test_the_grip_is_a_full_height_target_outside_the_card_frame(qapp):
    """Both halves of what moving it out of the header bought.

    In the header it was a twelve-pixel square — the smallest target on the surface, at
    the one place the pointer has to land precisely. Beside the frame it is free to be as
    tall as the card, because it is no longer competing with the content for the row.
    """
    host, column = _column(qapp)
    card = column.cards[0]

    assert card.grip.geometry().right() < card.frame.x(), "the handle is outside the frame, not in it"
    assert card.grip.height() == card.frame.height(), "and as tall as what it moves"
    assert host


def test_the_dots_run_the_height_of_what_they_grip(qapp):
    """The handle is a column, not a mark sitting in the middle of a tall target.

    A grip whose glyph is the size it was in the header, floating halfway down 54 pixels
    of hit area, says the target is the glyph — which is the thing the enlargement was
    for. Asserted against two cards of different heights because the column is stacked to
    fit rather than drawn once: a profile card is taller than an account card, and its
    handle has to be too.
    """
    accounts = ACCOUNTS[:3]
    card = AccountCard(accounts[0], [], accounts)
    tall = ProfileCard({"name": "Farm Team", "accounts": [a["username"] for a in accounts]}, accounts)
    host, column = _column(qapp)
    column.set_cards([card, tall])
    for _ in range(3):
        qapp.processEvents()

    assert tall.height() > card.height(), "the fixture needs two heights, or this tests nothing"
    for one in (card, tall):
        drawn = one.grip.column_height
        assert drawn > one.height() // 2, "the dots run the length of the target, not a mark in it"
        assert drawn < one.height(), "and stop short of the card's own edges"
    assert tall.grip.column_height > card.grip.column_height
    assert host


def test_the_grip_tile_stacks_without_a_seam():
    """``grip.svg`` is one repeat of a column, and the arithmetic that makes it one.

    The dots are stacked rather than stretched — a circle scaled to an 18x54 box is an
    ellipse — so the padding above the first row and below the last are a *seam*, and they
    have to add up to the gap between two rows inside the tile. Seated at the usual 6/12/18
    of a 24-unit box they add up to twice it, and the handle draws as bands of six dots
    with a visible break between them, which reads as three handles rather than one.
    """
    svg = (ICONS_DIR / "grip.svg").read_text()
    box = float(re.search(r'viewBox="0 0 \S+ (\S+)"', svg).group(1))
    rows = sorted({float(cy) for cy in re.findall(r'cy="(\S+?)"', svg)})

    gaps = {round(b - a, 3) for a, b in zip(rows, rows[1:])}
    assert len(gaps) == 1, f"the rows inside a tile are evenly spaced, got {sorted(gaps)}"
    assert round(rows[0] + (box - rows[-1]), 3) == gaps.pop(), "the seam is one more gap, not two"


def test_the_card_frame_does_not_move_when_the_grip_is_revealed(qapp):
    """The reason the gutter is reserved whether the handle is drawn in it or not.

    On the launch screen the grip appears on hover, and in the header that meant every
    card had eighteen pixels punched out in front of its name for the whole time it was
    *not* hovered, and its name jumping right the moment it was. The handle is out of the
    content row now, and the space it holds is the same either way.
    """
    host, column = _column(qapp)
    card = column.cards[0]
    layout = _rows(column)

    seen = []
    for visible in (True, False):
        card.grip.setVisible(visible)
        layout.activate()
        seen.append(card.frame.x())

    assert seen[0] == seen[1], "the frame starts in the same place hovered or not"
    assert host


def test_a_click_in_the_gutter_beside_the_grip_does_not_launch(qapp):
    """The gutter is the handle's, not the card's. Nothing is drawn in the rest of it.

    The widget the column lays out is wider than the frame the user sees, so its bare
    rectangle is no longer the hit target: a few pixels of blank column that silently
    start a game client are the kind of target nobody aims at and everybody hits.
    """
    host, column = _column(qapp)
    launched: list[tuple] = []
    card = column.cards[0]
    card.launch_account_requested.connect(lambda u, d: launched.append((u, d)))
    # Between the handle and the frame, which is inside the widget and outside the card.
    gap = QPoint((card.grip.geometry().right() + card.frame.x()) // 2, card.height() // 2)

    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(
            card,
            QMouseEvent(
                kind,
                gap.toPointF(),
                card.mapToGlobal(gap).toPointF(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

    assert launched == []
    assert host


def test_a_click_on_the_grip_does_not_launch(qapp):
    """It is a handle, not a button. Press and release without travel must do nothing."""
    host, column = _column(qapp)
    launched: list[tuple] = []
    card = column.cards[0]
    card.launch_account_requested.connect(lambda u, d: launched.append((u, d)))

    _press(card)
    _release(card, 0)

    assert launched == []
    assert host


# ---------------------------------------------------------------------------
# What am I holding
# ---------------------------------------------------------------------------


def test_a_lifted_card_leaves_a_hole_the_same_size_behind(qapp):
    """The placeholder is doing the layout's work, not decorating it.

    Without it the column closes up by one card-height on the first pixel of every drag,
    every card below jumps twice, and the slots the drag is measured against are all a row
    out of place.
    """
    host, column = _column(qapp)
    card = column.cards[0]
    before = _rows(column).sizeHint().height()

    _press(card)
    _move(card, QApplication.startDragDistance() + 2)

    hole = _placeholder(column)
    assert hole is not None, "a lifted card leaves a placeholder in its slot"
    assert hole.isVisible(), "a hidden widget is skipped by the layout and holds nothing open"
    assert hole.height() == card.height()
    assert _rows(column).sizeHint().height() == before

    _release(card, QApplication.startDragDistance() + 2)
    assert host


def test_a_lifted_card_follows_the_pointer_between_slots(qapp):
    """The signal that was missing outright.

    A card the layout still owns can only be in a slot, so it snaps a row at a time: drag
    it forty pixels and *nothing whatever happens*, which is exactly the stretch of the
    gesture where the user is deciding whether it is working.
    """
    host, column = _column(qapp)
    card = column.cards[0]
    start = card.y()
    # Deliberately less than a row: far enough to be a drag, not far enough to reorder.
    nudge = _row_height(column) // 3 + QApplication.startDragDistance()

    _press(card)
    _move(card, nudge)

    assert card.y() == start + nudge, "the card tracks the pointer, not the nearest slot"
    assert column.cards[0] is card, "and it has not reordered anything yet"

    _release(card, nudge)
    assert host


def test_a_lifted_card_says_it_is_being_held_and_stops_saying_so(qapp):
    """The grabbed state is what picks the card out of the column it is travelling over."""
    host, column = _column(qapp)
    card = column.cards[0]

    assert not card.is_dragging
    _press(card)
    _move(card, QApplication.startDragDistance() + 2)
    assert card.is_dragging
    assert card.grip.cursor().shape() == Qt.CursorShape.ClosedHandCursor

    _release(card, QApplication.startDragDistance() + 2)
    _land(column)
    assert not card.is_dragging
    assert card.grip.cursor().shape() == Qt.CursorShape.OpenHandCursor
    assert host


def test_a_dropped_card_is_handed_back_to_the_layout(qapp):
    """The lift is for the length of the gesture only.

    A card left positioned by hand would sit where it was dropped until the next relayout
    and then jump — and the placeholder would still be holding a hole open beneath it.
    """
    host, column = _column(qapp)
    card = column.cards[0]
    dy = _row_height(column)

    _press(card)
    _move(card, dy)
    _release(card, dy)
    _land(column)

    assert _placeholder(column) is None, "the hole goes when the card comes back"
    assert _rows(column).indexOf(card) == 1, "and the card is in the layout at its new slot"
    assert host


# ---------------------------------------------------------------------------
# What just changed
# ---------------------------------------------------------------------------


def test_a_displaced_card_slides_into_its_new_slot(qapp):
    """Motion is what makes a swap something you watched rather than something you notice.

    Asserted in two halves, because "it animated" and "it arrived" are separately
    breakable: right after the reorder the displaced card is still at its *old* position
    — which is what proves it did not teleport — and after the slide has had time to run
    it is at the slot the layout gave it.
    """
    host, column = _column(qapp)
    card, displaced = column.cards[0], column.cards[1]
    slots = [c.y() for c in column.cards]
    dy = _row_height(column)

    _press(card)
    _move(card, dy)

    assert displaced.y() == slots[1], "a displaced card starts from where it was, not where it lands"

    QTest.qWait(CARD_SHIFT_MS * 3)
    assert displaced.y() == slots[0], "and slides the whole way there"

    _release(card, dy)
    assert host


# ---------------------------------------------------------------------------
# Where can I put it
# ---------------------------------------------------------------------------


def test_a_drag_held_at_the_foot_of_a_full_column_scrolls_it(qapp):
    """Without this, the reorderable part of a column is whatever was on screen at press.

    Moving an account from the bottom of a long list to the top stops being a gesture the
    user can perform at all — they have to drop it part-way, scroll, and pick it up again.
    """
    host, column = _column(qapp, count=6, height=260)
    bar = column.scroll_area.verticalScrollBar()
    assert bar.maximum() > 0, "the fixture must actually overflow, or this tests nothing"
    bar.setValue(0)

    card = column.cards[0]
    viewport = column.scroll_area.viewport()
    # Held just inside the bottom margin: the pointer stops here and the content moves.
    foot = viewport.mapToGlobal(QPoint(5, viewport.height() - AUTOSCROLL_MARGIN // 2)).y()

    _press(card)
    _move(card, foot - card.mapToGlobal(QPoint(0, 5)).y())
    QTest.qWait(CARD_SHIFT_MS * 3)

    assert bar.value() > 0, "the column scrolls while the drag is held at its foot"

    _release(card, 0)
    assert host


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------


def test_a_dropped_card_flies_home_before_anything_else_happens(qapp):
    """The drop used to jitter, and this is the ordering that stops it.

    Handing the card back to the layout at the moment of release put two things in charge
    of its position at once — the settle animation and a layout that had just been told
    this widget was one of its rows again — so every deferred layout pass snapped it into
    its slot and the slide picked it up from there. Reporting the order in the same breath
    made it worse: the rebuild that answers ``reordered`` tore the cards down mid-flight,
    so the card jumped whatever distance was left and landed in a different set of widgets.

    So the release changes nothing structural. The card is still lifted, the placeholder
    is still holding its slot, and the order is still unreported until the flight is over.
    """
    host, column = _column(qapp)
    seen: list[list] = []
    column.reordered.connect(seen.append)
    card = column.cards[0]
    # Deliberately past the slot rather than exactly onto it: a card let go on top of its
    # own destination has nothing to fly, and lands in the same breath.
    dy = _row_height(column) + _row_height(column) // 2

    _press(card)
    _move(card, dy)
    _release(card, dy)

    assert _placeholder(column) is not None, "the slot is held open until the card is in it"
    assert card.is_dragging, "and the card is still the one in the air"
    assert seen == [], "nothing is reported while the card is still moving"

    _land(column)

    assert _placeholder(column) is None
    assert not card.is_dragging
    assert seen == [[1, 0, 2]]
    assert host


def test_a_rebuild_leaves_the_column_scrolled_where_the_user_left_it(qapp):
    """A drop that auto-scrolled to reach its slot ends in a rebuild — which must not jump.

    Emptying the layout collapses the scroll range, and a bar whose range collapses loses
    its value on the way. Restoring it is the difference between a drag ending where the
    user dropped the card and ending at the top of a column they had scrolled away from.
    """
    host, column = _column(qapp, count=6, height=260)
    bar = column.scroll_area.verticalScrollBar()
    assert bar.maximum() > 0, "the fixture must actually overflow, or this tests nothing"
    bar.setValue(bar.maximum())
    scrolled_to = bar.value()

    column.set_cards([AccountCard(a, [], ACCOUNTS[:6]) for a in ACCOUNTS[:6]])
    for _ in range(3):
        qapp.processEvents()

    assert bar.value() == scrolled_to
    assert host


# ---------------------------------------------------------------------------
# Giving the cursor back — all three ways a drag can end
# ---------------------------------------------------------------------------


def test_the_cursor_closes_for_the_drag_and_is_given_back_on_release(qapp):
    host, column = _column(qapp)
    card = column.cards[0]
    dy = _row_height(column)

    assert QApplication.overrideCursor() is None
    _press(card)
    _move(card, dy)
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.ClosedHandCursor

    _release(card, dy)
    assert QApplication.overrideCursor() is None
    assert host


def test_a_rebuild_arriving_mid_drag_gives_the_cursor_back(qapp):
    """The reachable one: ``reordered`` rebuilds both columns while the drag is still up.

    An override cursor outlives the widgets it was pushed for, so a teardown that skipped
    it would leave a closed hand over the whole application with nothing being dragged.
    """
    host, column = _column(qapp)
    card = column.cards[0]

    _press(card)
    _move(card, _row_height(column))
    assert QApplication.overrideCursor() is not None

    column.set_cards([AccountCard(a, [], ACCOUNTS[:3]) for a in ACCOUNTS[:3]])

    assert QApplication.overrideCursor() is None
    assert host


def test_closing_a_column_mid_drag_gives_the_cursor_back(qapp):
    """The third way out, and the only one with no card left to ask about it afterwards."""
    host, column = _column(qapp)
    card = column.cards[0]

    _press(card)
    _move(card, _row_height(column))
    assert QApplication.overrideCursor() is not None

    column.close()

    assert QApplication.overrideCursor() is None
    assert host
