"""Drive a real grip drag on any card list hosted by ``DragReorder``.

The events go to the card's grip, because that is where the engine's event filter is
installed -- calling a handler directly would step over the thing under test. Positions
are measured against the card, which is what the engine reads to decide the slot.

``test_card_column_reorder.py`` has its own copy of this, written before the engine left
``CardColumn``; this one is for the hosts that came after.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.gui.widgets.drag_reorder import CARD_SETTLE_MS


def _send(card: QWidget, kind: QEvent.Type, dy: int, buttons: Qt.MouseButton) -> None:
    local = QPoint(card.width() // 2, 5 + dy)
    event = QMouseEvent(
        kind,
        card.grip.mapFrom(card, local).toPointF(),
        card.mapToGlobal(local).toPointF(),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(card.grip, event)


def drag_card(qapp, card: QWidget, dy: int) -> None:
    """Press on *card*'s grip, move it *dy* pixels, release, and wait for it to settle.

    The wait matters: the engine reports an order only once the dropped card has flown
    into its slot, so an assertion made at release would read the list mid-gesture.
    """
    _send(card, QEvent.Type.MouseButtonPress, 0, Qt.MouseButton.LeftButton)
    _send(card, QEvent.Type.MouseMove, dy, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    _send(card, QEvent.Type.MouseButtonRelease, dy, Qt.MouseButton.NoButton)
    QTest.qWait(CARD_SETTLE_MS * 2)


def press_and_move(qapp, card: QWidget, dy: int) -> None:
    """Start a drag of *card* and hold it *dy* pixels away, without letting go."""
    _send(card, QEvent.Type.MouseButtonPress, 0, Qt.MouseButton.LeftButton)
    _send(card, QEvent.Type.MouseMove, dy, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def release(qapp, card: QWidget, dy: int) -> None:
    """Let go of a card held by :func:`press_and_move`, and wait for it to settle."""
    _send(card, QEvent.Type.MouseButtonRelease, dy, Qt.MouseButton.NoButton)
    QTest.qWait(CARD_SETTLE_MS * 2)


def menu_move(card: QWidget, label: str) -> bool:
    """Pick *label* ("Move up" or "Move down") from *card*'s right-click menu.

    The keyboard route to a reorder (#155). Returns whether the entry was enabled, and
    triggers it only if it was -- a greyed entry is one the user cannot pick.
    """
    menu = card._build_verbs_menu()
    action = next(a for a in menu.actions() if a.text().split("\t")[0] == label)
    if action.isEnabled():
        action.trigger()
    return action.isEnabled()


def row_height(cards: list[QWidget], spacing: int) -> int:
    """One card plus the gap under it -- how far a drag travels to pass one."""
    return cards[0].height() + spacing


def show(qapp, widget: QWidget, width: int = 320, height: int = 900) -> QWidget:
    """Show *widget* at a real size and let its layouts run: a drag reads real geometry."""
    widget.resize(width, height)
    widget.show()
    for _ in range(3):
        qapp.processEvents()
    return widget
