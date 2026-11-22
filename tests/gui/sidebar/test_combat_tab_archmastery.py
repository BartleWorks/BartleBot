"""A deck card's archmastery on the Combat tab: the game's two icons, or the sentence they replace.

The badge is core's (``bartleby/qt/widgets/cards/archmastery_badge.py``), tested there; these
are the card's choice between the two forms, with icons in a tmp art cache in
distinguishable colors.
"""

from __future__ import annotations

import json

import pytest
from bartleby.runtime.game import card_art
from bartleby.shared.strategy_models import DeckData
from PyQt6.QtGui import QColor, QIcon, QPixmap

from bartlebot.gui.sidebar.combat_tab import _PlayerDecksSection

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
