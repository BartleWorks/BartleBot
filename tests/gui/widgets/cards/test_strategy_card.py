"""Tests for StrategyCard's own layout -- what makes it wider or taller than its column wants."""

from __future__ import annotations

from bartlebot.combat.strategy_summary import StrategySummary
from bartlebot.gui.widgets.cards.card_art_tile import ArtIconView
from bartlebot.gui.widgets.cards.strategy_card import StrategyCard
from bartlebot.gui.widgets.pickers.tag_picker import STRATEGY_VOCABULARY

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


def _deck(schools: tuple[str, ...]) -> StrategySummary:
    return StrategySummary(
        name="D", description="", facets=["/".join(schools), "36 cards"], school_names=schools, tags=_ROLES
    )


def _with_icons(monkeypatch, *, have: set[str]) -> None:
    from PyQt6.QtGui import QIcon, QPixmap

    pixmap = QPixmap(8, 8)
    pixmap.fill()
    monkeypatch.setattr(
        "bartlebot.gui.widgets.cards.strategy_card.school_icon",
        lambda school: QIcon(pixmap) if school in have else None,
    )


def test_a_deck_card_draws_each_school_beside_its_icon(qapp, monkeypatch):
    _with_icons(monkeypatch, have={"Fire", "Ice"})
    card = StrategyCard(_deck(("Fire", "Ice")))

    views = card.findChildren(ArtIconView)

    assert len(views) == 2
    assert card.sub_texts == ["Fire", "Ice", "· 36 cards"]


def test_a_school_with_no_icon_is_its_name_alone(qapp, monkeypatch):
    _with_icons(monkeypatch, have={"Fire"})
    card = StrategyCard(_deck(("Fire", "Ice")))

    assert len(card.findChildren(ArtIconView)) == 1
    assert "Ice" in card.sub_texts


def test_a_deck_card_without_art_keeps_the_plain_line(qapp, monkeypatch):
    _with_icons(monkeypatch, have=set())
    card = StrategyCard(_deck(("Fire", "Ice")))

    assert card.sub_texts == ["Fire/Ice · 36 cards"]
    assert not card.findChildren(ArtIconView)


def test_role_pills_are_not_the_color_of_any_school(qapp):
    from bartleby.qt.theming.theme import THEME

    assert STRATEGY_VOCABULARY.accent("Hitter") == THEME.chip_neutral_bg
