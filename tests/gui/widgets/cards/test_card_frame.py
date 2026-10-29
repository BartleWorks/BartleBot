"""CardFrame's highlight: a card just added is lit for a moment, then goes back to rest (#272)."""

from __future__ import annotations

from bartleby.qt.theming.theme import THEME
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel

from bartlebot.gui.widgets.cards import card_frame
from bartlebot.gui.widgets.cards.card_frame import CardFrame, CardMode


class _Card(CardFrame):
    def __init__(self) -> None:
        super().__init__(mode=CardMode.MANAGE)
        self._build_frame("cards").addWidget(QLabel("card"))


def _border(card: CardFrame) -> str:
    return card.styleSheet().split("border: 1px solid ")[1].split(";")[0]


def test_a_highlighted_card_wears_the_accent_border(qapp):
    card = _Card()
    card.refresh_theme_styles()
    assert _border(card) == THEME.border

    card.highlight()

    assert card.is_highlighted
    assert _border(card) == THEME.accent


def test_the_highlight_goes_by_itself(qapp, monkeypatch):
    monkeypatch.setattr(card_frame, "CARD_HIGHLIGHT_DURATION", 0.001)
    card = _Card()
    card.highlight()

    QTest.qWait(50)

    assert not card.is_highlighted
    assert _border(card) == THEME.border
