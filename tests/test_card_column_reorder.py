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
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from bartlebot.gui.game_embed.card_columns import CardColumn
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
    local = QPoint(5, 5) + offset
    return QMouseEvent(
        kind,
        local.toPointF(),
        card.mapToGlobal(local).toPointF(),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def _drag(qapp, column: CardColumn, card, dy: int) -> None:
    """Press on *card*, move it *dy* pixels down its column, release.

    Sent through ``QApplication.sendEvent`` rather than called on the card, because the
    reorder is a column-level event filter and calling the handler directly would step
    over the thing under test.
    """
    QApplication.sendEvent(card, _event(QEvent.Type.MouseButtonPress, card, QPoint(0, 0), Qt.MouseButton.LeftButton))
    QApplication.sendEvent(card, _event(QEvent.Type.MouseMove, card, QPoint(0, dy), Qt.MouseButton.LeftButton))
    qapp.processEvents()
    QApplication.sendEvent(card, _event(QEvent.Type.MouseButtonRelease, card, QPoint(0, dy), Qt.MouseButton.NoButton))


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


def test_a_click_that_never_moves_still_launches(qapp):
    """The other half of the same rule: waiting for the release must not cost the click."""
    host, column = _column(qapp)
    launched: list[tuple] = []
    card = column.cards[0]
    card.launch_account_requested.connect(lambda u, d: launched.append((u, d)))

    _drag(qapp, column, card, 0)

    assert launched == [("one@example.com", "One")]
    assert host


def test_the_add_card_is_not_draggable(qapp):
    """It is the column's terminator, not a member of the ordered list."""
    host, column = _column(qapp)
    rows = column.scroll_area.widget().layout()
    add_card = rows.itemAt(rows.count() - 2).widget()
    seen: list[list] = []
    column.reordered.connect(seen.append)

    _drag(qapp, column, add_card, -_row_height(column) * 2)

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
