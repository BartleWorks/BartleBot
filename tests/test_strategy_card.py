"""Tests for StrategyCard's own layout -- what makes it wider or taller than its column wants."""

from __future__ import annotations

from bartlebot.gui.widgets.strategy_card import StrategyCard
from bartlebot.strategy_summary import StrategySummary

_ROLES = ("Hitter", "Support", "Healer")


def _tagged() -> StrategyCard:
    return StrategyCard(StrategySummary(name="A", description="", tags=_ROLES))


def test_the_tag_row_does_not_ask_for_every_pill_end_to_end(qapp):
    """A box row never wraps, so its minimum was the whole row -- wider than a narrow column (#118)."""
    card = _tagged()
    end_to_end = sum(pill.sizeHint().width() for pill in card._tag_pills)

    assert card.minimumSizeHint().width() < end_to_end


def test_the_tag_row_wraps_when_the_card_is_narrow(qapp):
    card = _tagged()
    widest = max(pill.sizeHint().width() for pill in card._tag_pills)

    assert card.heightForWidth(widest + 40) > card.heightForWidth(1000)


def test_the_tags_keep_their_order(qapp):
    assert _tagged().tag_texts == list(_ROLES)
