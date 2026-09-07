"""Dragging a card up or down its column reorders the list behind it (#192).

Order is a real setting, not a display preference. It is what the Accounts column, the
Launch Profiles column and the Launch menu all list, and inside a launch profile the
account order decides which window becomes Player 1 — so a drag has to end in
``settings.json`` and not merely in the layout.

Reordering used to be the one thing the Game tab could do that the launch screen could
not, on a ``_ReorderableList`` the cards cannot live in. It is ``CardColumn``'s now, so
both surfaces have it and neither can lose it on its own.

Two halves, and the second is the one with teeth: the gesture has to *not* also be a
click. A card body launches a game client, and until #192 it fired on press — on the
first pixel of every drag.

The gesture now starts on the card's grip and nowhere else, so that the cursor can say
where dragging works without claiming a whole card whose primary verb is launch. That
moved the teeth rather than pulling them: a press-and-sweep across a card body must still
end in nothing at all — not a reorder, and not a launch on whichever card the pointer came
to rest over. What the drag *looks like* while it happens is tested next door, in
``test_card_drag_feedback.py``.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from bartlebot.gui.game_embed.card_columns import CARD_SETTLE_MS, CardColumn
from bartlebot.gui.game_embed.launch_cards import AccountCard
from bartlebot.gui.settings.accounts_tab import reorder_accounts, reorder_launch_profiles
from bartlebot.settings_manager import SettingsManager

ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
    {"username": "three@example.com", "alias": "Three"},
]


def _column(qapp, count: int = 3, height: int = 600):
    """A shown column of *count* account cards, and the host holding it alive.

    Shown and pumped because the drag is computed from real geometry: an unshown card has
    no height, so every insertion point would resolve to the same slot.
    """
    column = CardColumn("Accounts", "hint", "Add account")
    column.set_cards([AccountCard(a, [], ACCOUNTS[:count]) for a in ACCOUNTS[:count]])
    host = QWidget()
    QVBoxLayout(host).addWidget(column)
    host.resize(300, height)
    host.show()
    for _ in range(3):
        qapp.processEvents()
    return host, column


def _event(kind, card, offset: QPoint, buttons):
    # Anchored inside the card's *frame*, which starts past the drag handle's gutter. The
    # gutter is not a hit target — a blank strip that launched a game client would be a
    # bad one — so a body click aimed at the card's own origin would land on nothing.
    local = getattr(card, "frame", card).geometry().topLeft() + QPoint(5, 5) + offset
    return QMouseEvent(
        kind,
        local.toPointF(),
        card.mapToGlobal(local).toPointF(),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def _send(target, card, kind, dy: int, buttons) -> None:
    """One mouse event at *dy* down *card*, delivered to *target*.

    The position is always measured against the **card** even when the event goes to the
    grip: that is what the column reads, and it is what decides which slot the card now
    covers. Delivering to the grip is the part under test — the drag is a column-level
    event filter installed on the handle, so calling a handler directly would step over
    the thing this file is about.
    """
    QApplication.sendEvent(target, _event(kind, card, QPoint(0, dy), buttons))


def _drag(qapp, column: CardColumn, card, dy: int, *, target=None) -> None:
    """Press on *card*'s grip, move it *dy* pixels down its column, release, and land it.

    *target* overrides where the events land, which is how the body-drag guards below ask
    for a gesture that is deliberately aimed at the wrong place.

    The wait is not padding. A released card flies the last few pixels into its slot
    before the column reports anything, so that the rebuild the report provokes cannot
    land on top of a card that is still moving — see ``_end_drag``. Every assertion about
    what a drag *wrote* is therefore an assertion about a gesture that has finished, and
    a test that read the signal at release would be asserting the bug back.
    """
    target = card.grip if target is None else target
    _send(target, card, QEvent.Type.MouseButtonPress, 0, Qt.MouseButton.LeftButton)
    _send(target, card, QEvent.Type.MouseMove, dy, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    _send(target, card, QEvent.Type.MouseButtonRelease, dy, Qt.MouseButton.NoButton)
    QTest.qWait(CARD_SETTLE_MS * 2)


def _row_height(column: CardColumn) -> int:
    """One card plus the gap under it — how far a drag has to travel to pass one."""
    first = column.cards[0]
    return first.height() + column.scroll_area.widget().layout().spacing()


# ---------------------------------------------------------------------------
# The gesture
# ---------------------------------------------------------------------------


def test_dragging_a_card_down_past_one_reports_the_new_order(qapp):
    host, column = _column(qapp)
    seen: list[list] = []
    column.reordered.connect(seen.append)

    _drag(qapp, column, column.cards[0], _row_height(column))

    assert seen == [[1, 0, 2]]
    assert host  # held: the column dies with it


def test_dragging_a_card_to_the_foot_of_the_column_reports_it_last(qapp):
    host, column = _column(qapp)
    seen: list[list] = []
    column.reordered.connect(seen.append)

    _drag(qapp, column, column.cards[0], _row_height(column) * 5)

    assert seen == [[1, 2, 0]], "a drag past the end lands in the last slot, not past it"
    assert host


def test_a_drag_that_ends_where_it_started_reports_nothing(qapp):
    """A wobble on a card must not rewrite settings and rebuild two columns under the pointer."""
    host, column = _column(qapp)
    seen: list[list] = []
    column.reordered.connect(seen.append)

    _drag(qapp, column, column.cards[0], QApplication.startDragDistance() + 2)

    assert seen == []
    assert host


def test_a_drag_does_not_also_launch_what_it_moved(qapp):
    """The one with teeth. A card that activates on press launches on the first pixel."""
    host, column = _column(qapp)
    launched: list[tuple] = []
    for card in column.cards:
        card.launch_account_requested.connect(lambda u, d: launched.append((u, d)))

    _drag(qapp, column, column.cards[0], _row_height(column))

    assert launched == []
    assert host


def test_a_sweep_across_a_card_body_neither_reorders_nor_launches(qapp):
    """The teeth, moved. The body is not a drag source, so it has to be inert either way.

    This is what a user who has not found the grip yet will try first, and both ways of
    getting it wrong are live: reordering from the body would make the grip a decoration
    that lies about where dragging works, and launching would fire a game client because
    the pointer happened to come to rest over a card after travelling a row.
    """
    host, column = _column(qapp)
    launched: list[tuple] = []
    seen: list[list] = []
    column.reordered.connect(seen.append)
    for card in column.cards:
        card.launch_account_requested.connect(lambda u, d: launched.append((u, d)))

    card = column.cards[0]
    _drag(qapp, column, card, _row_height(column), target=card)

    assert seen == [], "the body must not reorder — the grip is the only drag source"
    assert launched == [], "and a sweep is still not a click"
    assert column.cards[0] is card
    assert host


def test_a_click_that_never_moves_still_launches(qapp):
    """The other half of the same rule: waiting for the release must not cost the click.

    Aimed at the body, which is where a launch comes from. The grip is not a button and a
    click on it launches nothing — see ``test_card_drag_feedback.py``.
    """
    host, column = _column(qapp)
    launched: list[tuple] = []
    card = column.cards[0]
    card.launch_account_requested.connect(lambda u, d: launched.append((u, d)))

    _drag(qapp, column, card, 0, target=card)

    assert launched == [("one@example.com", "One")]
    assert host


def test_the_add_card_is_not_draggable(qapp):
    """It is the column's terminator, not a member of the ordered list."""
    host, column = _column(qapp)
    rows = column.scroll_area.widget().layout()
    add_card = rows.itemAt(rows.count() - 2).widget()
    seen: list[list] = []
    column.reordered.connect(seen.append)

    _drag(qapp, column, add_card, -_row_height(column) * 2, target=add_card)

    assert add_card.grip is None if hasattr(add_card, "grip") else True
    assert add_card not in column.cards
    assert seen == []
    assert host


# ---------------------------------------------------------------------------
# What it writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("order", "expected"),
    [([1, 0, 2], ["Two", "One", "Three"]), ([2, 1, 0], ["Three", "Two", "One"])],
)
def test_reorder_accounts_rearranges_the_stored_list(order, expected):
    SettingsManager.instance().accounts = [dict(a) for a in ACCOUNTS]

    assert reorder_accounts(order) is True
    assert [a["alias"] for a in SettingsManager.instance().accounts] == expected


def test_reorder_launch_profiles_rearranges_the_stored_list():
    sm = SettingsManager.instance()
    sm.launch_profiles = [{"name": n, "accounts": []} for n in ("A", "B", "C")]

    assert reorder_launch_profiles([2, 0, 1]) is True
    assert [p["name"] for p in sm.launch_profiles] == ["C", "A", "B"]


@pytest.mark.parametrize("order", [[0, 1], [0, 1, 2, 3], [0, 0, 1], [0, 1, 3]])
def test_a_permutation_that_does_not_describe_the_stored_list_is_refused(order):
    """The list can have been rewritten between the column being built and the drop.

    The other surface, or a delete the same gesture raced. Refusing costs the user one
    drag; applying a stale permutation reorders a list they never dragged, and can drop an
    account off the end of it.
    """
    sm = SettingsManager.instance()
    sm.accounts = [dict(a) for a in ACCOUNTS]

    assert reorder_accounts(order) is False
    assert [a["alias"] for a in sm.accounts] == ["One", "Two", "Three"]
