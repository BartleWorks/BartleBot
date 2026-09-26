"""``DragReorder`` on a host that is not ``CardColumn``.

The gesture itself -- lift, slide, settle, cursor -- is tested through ``CardColumn`` in
``test_card_drag_feedback.py`` and ``test_card_column_reorder.py``, which is where it
lived first. What is tested here is what the engine had to learn to leave that widget:
rows before the cards that are not part of the order, a drag abandoned on cards that
outlive it, and auto-scroll through a scroll area the host did not build.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from bartlebot.gui.widgets.card_frame import CardFrame, CardMode
from bartlebot.gui.widgets.drag_reorder import AUTOSCROLL_INTERVAL_MS, DragReorder
from tests.drag_helpers import drag_card, press_and_move, release, show


class _Card(CardFrame):
    def __init__(self, text: str) -> None:
        super().__init__(mode=CardMode.MANAGE)
        self.text = text
        self._build_frame("cards").addWidget(QLabel(text))


def _host(count: int = 3, *, header: bool = True):
    """A widget holding an optional header row and then *count* cards, and its engine."""
    host = QWidget()
    layout = QVBoxLayout(host)
    if header:
        layout.addWidget(QLabel("header — not a card"))
    cards = [_Card(f"card {i}") for i in range(count)]
    for card in cards:
        layout.addWidget(card)
    layout.addStretch()
    drag = DragReorder(layout, host)
    drag.set_cards(cards)
    return host, layout, drag, cards


def _layout_texts(layout) -> list[str]:
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    return [w.text if isinstance(w, _Card) else "header" for w in widgets if w is not None]


def test_a_row_before_the_cards_is_never_part_of_the_order(qapp):
    host, layout, drag, cards = _host()
    show(qapp, host)
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    drag_card(qapp, cards[2], cards[0].y() - cards[2].y() - 200)

    assert seen == [[2, 0, 1]]
    assert _layout_texts(layout) == ["header", "card 2", "card 0", "card 1"]


def test_an_abandoned_drag_puts_every_card_back(qapp):
    """A deck card is never rebuilt, so a cancelled drag must leave the list as it was."""
    host, layout, drag, cards = _host()
    show(qapp, host)
    previews: list[list] = []
    reported: list[list] = []
    drag.previewed.connect(previews.append)
    drag.reordered.connect(reported.append)

    press_and_move(qapp, cards[0], cards[2].y() - cards[0].y() + 1)
    assert drag.cards == [cards[1], cards[2], cards[0]]
    drag.cancel()
    qapp.processEvents()

    assert drag.cards == cards
    assert previews[-1] == cards
    assert reported == []
    assert _layout_texts(layout) == ["header", "card 0", "card 1", "card 2"]
    assert not cards[0].is_dragging
    assert QApplication.overrideCursor() is None


def test_closing_the_host_abandons_a_drag(qapp):
    host, _layout, drag, cards = _host()
    drag.watch_for_show(host)
    show(qapp, host)

    press_and_move(qapp, cards[0], cards[1].y() - cards[0].y() + 1)
    host.close()

    assert not drag.is_busy
    assert QApplication.overrideCursor() is None


def test_a_drag_near_the_bottom_scrolls_the_enclosing_scroll_area(qapp):
    host, _layout, _drag, cards = _host(8, header=False)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(host)
    show(qapp, area, height=160)
    bar = area.verticalScrollBar()
    assert bar.maximum() > 0

    card = cards[0]
    bottom = area.viewport().mapTo(host, area.viewport().rect().bottomLeft()).y()
    press_and_move(qapp, card, bottom - card.y() - 5)
    QTest.qWait(AUTOSCROLL_INTERVAL_MS * 6)
    scrolled = bar.value()
    release(qapp, card, bottom - card.y() - 5)

    assert scrolled > 0


def test_a_locked_grip_starts_no_drag(qapp):
    host, _layout, drag, cards = _host()
    show(qapp, host)
    cards[0].grip.set_locked(True, "locked")
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    drag_card(qapp, cards[0], cards[2].y() - cards[0].y() + 1)

    assert seen == []
    assert drag.cards == cards


def test_showing_the_host_warms_its_window(qapp, monkeypatch):
    import bartlebot.gui.widgets.drag_reorder as module  # noqa: PLC0415

    warmed: list[QWidget] = []
    monkeypatch.setattr(module, "warm_offscreen_render", warmed.append)
    host, _layout, drag, _cards = _host()
    drag.watch_for_show(host)

    QApplication.sendEvent(host, QEvent(QEvent.Type.Show))
    QTest.qWait(10)

    assert warmed == [host]
