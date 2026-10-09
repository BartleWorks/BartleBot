"""Tests for the archmastery badge: which two icons it draws, and when it declines to.

The interesting rule is the second icon. A deck can carry a school it will never
apply, so the medallion names the school only when the apply will actually set it and
the universal mark stands in otherwise -- naming a school there would be a claim about
the wizard the deck does not make.

Icons are written into a tmp art cache in distinguishable colors, which is how these
tell "drew the school" from "drew the universal mark" apart.
"""

from __future__ import annotations

import json

import pytest
from PyQt6.QtGui import QColor, QIcon, QPixmap

from bartlebot.game import card_art
from bartlebot.gui.sidebar.combat_tab import _PlayerDecksSection
from bartlebot.gui.widgets.cards.archmastery_badge import ArchmasteryBadge, extracted_icon, school_icon_for
from bartlebot.shared.strategy_models import DeckData

#: A color per icon key, so a pixmap can be identified by reading a pixel out of it.
COLORS = {
    card_art.ARCHMASTERY_ICON: QColor("red"),
    card_art.UNIVERSAL_ICON: QColor("blue"),
    "school_fire": QColor("green"),
    "school_ice": QColor("yellow"),
}


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _extract_icons(keys=tuple(COLORS), *, images: dict[str, str] | None = None) -> None:
    """Stand in for a real extraction: an index with an ``icons`` map, and a PNG each."""
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    icons = {key: f"{key}.png" for key in keys}
    card_art.index_path().write_text(json.dumps({"images": images or {}, "icons": icons}), encoding="utf-8")
    for key in keys:
        pixmap = QPixmap(32, 32)
        pixmap.fill(COLORS[key])
        assert pixmap.save(str(card_art.icons_dir() / f"{key}.png"), "PNG")


def _color_of(icon: QIcon) -> QColor:
    image = icon.pixmap(16).toImage()
    return image.pixelColor(image.width() // 2, image.height() // 2)


# -- which second icon ------------------------------------------------------


def test_the_school_medallion_is_drawn_when_the_school_will_be_set(art_home, qapp):
    _extract_icons()

    icon = school_icon_for("Fire", will_set=True)

    assert icon is not None
    assert _color_of(icon) == COLORS["school_fire"]


def test_the_universal_mark_stands_in_when_the_school_will_not_be_set(art_home, qapp):
    """A Fire medallion on a deck whose apply leaves archmastery alone is a claim about
    the wizard that the deck does not make."""
    _extract_icons()

    icon = school_icon_for("Fire", will_set=False)

    assert icon is not None
    assert _color_of(icon) == COLORS[card_art.UNIVERSAL_ICON]


def test_a_school_is_found_whatever_case_the_deck_stored_it_in(art_home, qapp):
    # The deck's value comes from wizwalker's school table and the extractor's from
    # SCHOOL_ORDER; keying on exact casing would make the two agree only by luck.
    _extract_icons()

    assert school_icon_for("ICE", will_set=True) is not None
    assert school_icon_for("ice", will_set=True) is not None


def test_no_school_means_no_second_icon(art_home, qapp):
    _extract_icons()

    assert school_icon_for(None, will_set=True) is None
    assert school_icon_for("", will_set=True) is None


# -- the badge --------------------------------------------------------------


def test_the_badge_draws_both_icons(art_home, qapp):
    _extract_icons()
    badge = ArchmasteryBadge()

    badge.set_archmastery("Fire", will_set=True, tooltip="Archmastery: Fire · always set")

    assert badge.has_icons()
    assert _color_of(badge._mark.icon()) == COLORS[card_art.ARCHMASTERY_ICON]
    assert _color_of(badge._school.icon()) == COLORS["school_fire"]
    assert "always set" in badge.toolTip()


def test_the_badge_has_nothing_to_draw_without_extracted_icons(art_home, qapp):
    badge = ArchmasteryBadge()

    badge.set_archmastery("Fire", will_set=True)

    assert not badge.has_icons()


def test_the_badge_declines_a_school_its_icon_set_is_missing(art_home, qapp):
    """Both icons or neither: a lone spiral says the deck has an opinion without
    saying what it is."""
    _extract_icons(keys=(card_art.ARCHMASTERY_ICON, card_art.UNIVERSAL_ICON))

    badge = ArchmasteryBadge()
    badge.set_archmastery("Fire", will_set=True)

    assert not badge.has_icons()
    assert badge._mark.icon() is None


def test_a_deck_with_no_school_draws_nothing(art_home, qapp):
    _extract_icons()
    badge = ArchmasteryBadge()

    badge.set_archmastery(None, will_set=False)

    assert not badge.has_icons()


def test_rebuilding_the_badge_clears_what_was_there(art_home, qapp):
    _extract_icons()
    badge = ArchmasteryBadge()
    badge.set_archmastery("Fire", will_set=True)

    badge.set_archmastery(None, will_set=False)

    assert badge._school.icon() is None


# -- on the deck card -------------------------------------------------------


def test_the_deck_card_shows_icons_instead_of_the_text_line(art_home, qapp):
    _extract_icons()
    section = _PlayerDecksSection()

    section.refresh({1: DeckData(normal={"Card": 1}, archmastery_school="Fire", apply_archmastery=True)})

    card = section.card(1)
    assert not card._archmastery_icons.isHidden()
    assert card._archmastery.isHidden(), "the sentence and the icons must never both be up"


def test_the_deck_card_keeps_the_text_line_when_there_are_no_icons(art_home, qapp):
    """Most installs never run the extractor, so this is the ordinary case."""
    section = _PlayerDecksSection()

    section.refresh({1: DeckData(normal={"Card": 1}, archmastery_school="Fire", apply_archmastery=True)})

    card = section.card(1)
    assert card._archmastery_icons.isHidden()
    assert not card._archmastery.isHidden()
    assert "Fire" in card._archmastery.full_text()


def test_a_deck_that_will_not_set_its_school_shows_the_universal_mark(art_home, qapp):
    _extract_icons()
    section = _PlayerDecksSection()

    section.refresh({1: DeckData(normal={"Card": 1}, archmastery_school="Fire", apply_archmastery=False)})

    badge = section.card(1)._archmastery_icons
    assert badge.has_icons()
    assert _color_of(badge._school.icon()) == COLORS[card_art.UNIVERSAL_ICON]


def test_neither_form_appears_without_a_school(art_home, qapp):
    _extract_icons()
    section = _PlayerDecksSection()

    section.refresh({1: DeckData(normal={"Card": 1})})

    card = section.card(1)
    assert card._archmastery_icons.isHidden()
    assert card._archmastery.isHidden()


def test_the_icon_lookup_is_none_for_a_key_that_was_never_extracted(art_home, qapp):
    _extract_icons(keys=(card_art.ARCHMASTERY_ICON,))

    assert extracted_icon(card_art.ARCHMASTERY_ICON) is not None
    assert extracted_icon("school_shadow") is None
