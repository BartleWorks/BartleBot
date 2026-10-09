"""``DragReorder`` on a host that is not ``CardColumn``.

The gesture itself -- lift, slide, settle, cursor -- is tested through ``CardColumn`` in
``test_card_drag_feedback.py`` and ``test_card_column_reorder.py``, which is where it
lived first. What is tested here is what the engine had to learn to leave that widget:
rows before the cards that are not part of the order, a drag abandoned on cards that
outlive it, auto-scroll through a scroll area the host did not build, the keyboard
route to a move, and the two hooks a tree is drawn with (carry and accepts).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from bartlebot.gui.widgets.cards.card_frame import CardFrame, CardMode
from bartlebot.gui.widgets.layout.drag_reorder import AUTOSCROLL_INTERVAL_MS, DragReorder
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
    import bartlebot.gui.widgets.layout.drag_reorder as module  # noqa: PLC0415

    warmed: list[QWidget] = []
    monkeypatch.setattr(module, "warm_offscreen_render", warmed.append)
    host, _layout, drag, _cards = _host()
    drag.watch_for_show(host)

    QApplication.sendEvent(host, QEvent(QEvent.Type.Show))
    QTest.qWait(10)

    assert warmed == [host]


# ---------------------------------------------------------------------------
# The keyboard route (#155)
# ---------------------------------------------------------------------------


def _menu_texts(card) -> list[tuple[str, bool]]:
    menu = card._build_verbs_menu()
    return [(a.text().split("\t")[0], a.isEnabled()) for a in menu.actions() if not a.isSeparator()]


def test_a_keyboard_move_reports_like_a_drop(qapp):
    host, layout, drag, cards = _host()
    show(qapp, host)
    previews: list[list] = []
    reported: list[list] = []
    drag.previewed.connect(previews.append)
    drag.reordered.connect(reported.append)

    assert drag.move(cards[0], 1)

    assert reported == [[1, 0, 2]]
    assert previews == [[cards[1], cards[0], cards[2]]]
    assert _layout_texts(layout) == ["header", "card 1", "card 0", "card 2"]


def test_a_keyboard_move_off_either_end_does_nothing(qapp):
    _host_widget, _layout, drag, cards = _host()
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    assert not drag.move(cards[0], -1)
    assert not drag.move(cards[2], 1)
    assert seen == []


def test_a_locked_grip_refuses_a_keyboard_move_too(qapp):
    _host_widget, _layout, drag, cards = _host()
    cards[1].grip.set_locked(True, "locked")

    assert not drag.can_move(cards[1], 1)
    assert not drag.move(cards[1], 1)


def test_every_card_menu_leads_with_move_up_and_move_down(qapp):
    _host_widget, _layout, drag, cards = _host()
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    assert _menu_texts(cards[0]) == [("Move up", False), ("Move down", True)]
    assert _menu_texts(cards[1]) == [("Move up", True), ("Move down", True)]
    assert _menu_texts(cards[2]) == [("Move up", True), ("Move down", False)]

    menu = cards[2]._build_verbs_menu()
    next(a for a in menu.actions() if a.text().startswith("Move up")).trigger()
    assert seen == [[0, 2, 1]]


def test_alt_down_moves_the_card_that_has_focus(qapp):
    host, _layout, drag, cards = _host()
    show(qapp, host)
    QApplication.setActiveWindow(host)
    cards[0].setFocus()
    qapp.processEvents()
    assert cards[0].hasFocus()
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    QTest.keyClick(cards[0], Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)

    assert seen == [[1, 0, 2]]


def test_focus_follows_the_moved_card_through_a_rebuild(qapp):
    """A host that answers ``reordered`` by building new cards gets focus put back on the
    moved card's replacement, so Alt+Down can be pressed again straight away."""
    host, layout, drag, cards = _host()
    show(qapp, host)
    QApplication.setActiveWindow(host)
    texts = [c.text for c in cards]

    def rebuild(order: list[int]) -> None:
        nonlocal cards
        for card in cards:
            layout.removeWidget(card)
            card.deleteLater()
        texts[:] = [texts[i] for i in order]
        cards = [_Card(t) for t in texts]
        for index, card in enumerate(cards):
            layout.insertWidget(1 + index, card)
        drag.set_cards(cards)

    drag.reordered.connect(rebuild)
    cards[0].setFocus()
    qapp.processEvents()
    assert cards[0].hasFocus()

    drag.move(cards[0], 1)
    # A card added to a shown host is shown a turn later, and takes its focus then.
    qapp.processEvents()

    assert cards[1].text == "card 0"
    assert cards[1].hasFocus()


# ---------------------------------------------------------------------------
# A tree in one column: carry and accepts (#155)
# ---------------------------------------------------------------------------


def test_a_carried_block_travels_as_one(qapp):
    host, layout, drag, cards = _host(4, header=False)
    drag.set_rules(carry=lambda card: 1 if card is cards[1] else 0)
    show(qapp, host)
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    # Card 1 carries card 2; dragged past card 3, the two land together after it.
    drag_card(qapp, cards[1], cards[3].y() - cards[1].y() + cards[3].height())

    assert seen == [[0, 3, 1, 2]]
    assert _layout_texts(layout) == ["card 0", "card 3", "card 1", "card 2"]
    assert not cards[2].is_dragging


def test_a_carried_block_moves_by_keyboard_as_one(qapp):
    _host_widget, _layout, drag, cards = _host(4, header=False)
    drag.set_rules(carry=lambda card: 1 if card is cards[1] else 0)
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    drag.move(cards[1], -1)

    assert seen == [[1, 2, 0, 3]]


def test_a_refused_slot_keeps_the_hole_where_it_was(qapp):
    host, _layout, drag, cards = _host()
    drag.set_rules(accepts=lambda order, _block: order[0] is cards[0])
    show(qapp, host)
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    drag_card(qapp, cards[2], cards[0].y() - cards[2].y() - 200)

    assert seen == [], "the top slot is refused, so the card goes back where it was"


def test_a_keyboard_move_steps_past_a_refused_slot(qapp):
    _host_widget, _layout, drag, cards = _host(4, header=False)
    drag.set_rules(accepts=lambda order, _block: order.index(cards[0]) != 1)
    seen: list[list] = []
    drag.reordered.connect(seen.append)

    assert drag.move(cards[0], 1)

    assert seen == [[1, 2, 0, 3]]
