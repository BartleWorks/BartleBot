"""Tests for the card art tile, its count badge, and the strip of them.

The badge geometry and the tooltip are pure and tested as such. The strip needs a
``QApplication`` for its pixmaps, so those tests take the session ``qapp`` fixture and
write real PNGs into a tmp art cache.
"""

from __future__ import annotations

import json

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QPixmap

from bartlebot.game import card_art
from bartlebot.gui.deck_content import DeckCardEntry, DeckGroup
from bartlebot.gui.widgets.card_art_tile import (
    COLLAPSED_TILE,
    CardArtStrip,
    GroupDivider,
    badge_rect,
    card_icon,
    tooltip_for,
)


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _extract(images: dict[str, str], qapp) -> None:
    """Stand in for a real extraction: an index, and a PNG per named file."""
    card_art.spells_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"images": images}), encoding="utf-8")
    for name in set(images.values()):
        pixmap = QPixmap(128, 128)
        pixmap.fill(QColor("red"))
        assert pixmap.save(str(card_art.spells_dir() / name), "PNG")


def _entry(template: str, count: int = 1, display: str | None = None) -> DeckCardEntry:
    return DeckCardEntry(template=template, display=display or template, count=count)


# -- the badge ---------------------------------------------------------------


def test_the_badge_sits_in_the_top_right_quadrant():
    rect = badge_rect(COLLAPSED_TILE)

    assert rect.center().x() > COLLAPSED_TILE / 2
    assert rect.center().y() < COLLAPSED_TILE / 2


def test_the_badge_never_leaves_the_tile():
    # A two-digit count grows the badge; unclamped it would hang off the right edge,
    # which on a flow-laid strip means overlapping the next tile rather than clipping.
    for digits in (1, 2, 3):
        rect = badge_rect(COLLAPSED_TILE, digits)
        assert rect.left() >= 0
        assert rect.top() >= 0
        assert rect.right() <= COLLAPSED_TILE
        assert rect.bottom() <= COLLAPSED_TILE


def test_a_longer_count_gets_a_wider_badge():
    assert badge_rect(COLLAPSED_TILE, 2).width() > badge_rect(COLLAPSED_TILE, 1).width()


def test_the_badge_stays_legible_on_a_small_tile():
    # It has a floor: at 24px, 42% of the tile is under 11px and the digit inside it
    # would be smaller than the smallest font this app uses anywhere.
    assert badge_rect(24).height() >= 13


# -- the tooltip -------------------------------------------------------------


def test_the_tooltip_names_the_template_when_it_differs():
    tip = tooltip_for(DeckCardEntry(template="Storm Lord - T05 - A", display="Storm Lord (Path A, Tier 5)", count=2))

    assert "Storm Lord (Path A, Tier 5) × 2" in tip
    assert "Storm Lord - T05 - A" in tip


def test_the_tooltip_does_not_repeat_an_identical_template_name():
    assert tooltip_for(_entry("Tempest")) == "Tempest"


def test_the_tooltip_omits_a_count_of_one():
    assert "×" not in tooltip_for(_entry("Tempest"))
    assert "×" in tooltip_for(_entry("Tempest", count=3))


# -- the strip ---------------------------------------------------------------


def test_a_card_with_extracted_art_gets_an_icon(art_home, qapp):
    _extract({"Tempest": "Spell_Storm_Tempest.png"}, qapp)

    assert card_icon("Tempest") is not None


@pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0])
def test_the_art_is_rendered_at_the_screens_own_resolution(art_home, qapp, ratio):
    """Whatever the display's scaling, the icon hands back an image with a physical
    pixel per device pixel -- never a 40px one for Qt to stretch over 50, which is what
    a pixmap scaled in advance to the logical size was."""
    _extract({"Tempest": "Spell_Storm_Tempest.png"}, qapp)

    pixmap = card_icon("Tempest").pixmap(QSize(COLLAPSED_TILE, COLLAPSED_TILE), ratio)

    assert pixmap.width() == round(COLLAPSED_TILE * ratio)
    assert pixmap.devicePixelRatio() == pytest.approx(ratio)


def test_the_view_paints_at_the_devices_resolution(art_home, qapp):
    """Painted into a 150% device, a 20pt view fills 30 physical pixels with the art."""
    from PyQt6.QtGui import QImage, QPainter

    from bartlebot.gui.widgets.card_art_tile import ArtIconView

    _extract({"Tempest": "Spell_Storm_Tempest.png"}, qapp)
    view = ArtIconView(20)
    view.set_icon(card_icon("Tempest"))
    image = QImage(30, 30, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(1.5)
    image.fill(0)
    painter = QPainter(image)
    view.render(painter)
    painter.end()

    assert image.pixelColor(29, 29) == QColor("red")
    assert image.pixelColor(0, 0) == QColor("red")


def test_a_card_with_no_extracted_art_has_no_icon(art_home, qapp):
    assert card_icon("Tempest") is None


def test_the_strip_tiles_every_card_that_has_art(art_home, qapp):
    _extract({"Tempest": "a.png", "Epic": "b.png"}, qapp)
    strip = CardArtStrip()

    strip.set_groups([DeckGroup("Normal", "Normal Cards", (_entry("Tempest", 3), _entry("Epic")))])

    assert strip.has_art()
    assert [tile.entry.template for tile in strip.tiles] == ["Tempest", "Epic"]


def test_a_card_without_art_is_skipped_rather_than_placeheld(art_home, qapp):
    _extract({"Tempest": "a.png"}, qapp)
    strip = CardArtStrip()

    strip.set_groups([DeckGroup("Normal", "Normal Cards", (_entry("Tempest"), _entry("No Art Card")))])

    assert [tile.entry.template for tile in strip.tiles] == ["Tempest"]


def test_the_strip_is_empty_when_nothing_has_been_extracted(art_home, qapp):
    strip = CardArtStrip()

    strip.set_groups([DeckGroup("Normal", "Normal Cards", (_entry("Tempest"),))])

    assert not strip.has_art()


def test_a_divider_separates_groups_but_never_leads_the_strip(art_home, qapp):
    _extract({"Tempest": "a.png", "Item Card": "b.png"}, qapp)
    strip = CardArtStrip()

    strip.set_groups(
        [
            DeckGroup("Normal", "Normal Cards", (_entry("Tempest"),)),
            DeckGroup("Item", "Item Cards", (_entry("Item Card"),)),
        ]
    )

    layout = strip.layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    dividers = [w for w in widgets if isinstance(w, GroupDivider)]
    assert len(dividers) == 1
    assert not isinstance(widgets[0], GroupDivider)


def test_a_group_whose_cards_all_lack_art_contributes_no_divider(art_home, qapp):
    _extract({"Tempest": "a.png"}, qapp)
    strip = CardArtStrip()

    strip.set_groups(
        [
            DeckGroup("Normal", "Normal Cards", (_entry("Tempest"),)),
            DeckGroup("Item", "Item Cards", (_entry("No Art Card"),)),
        ]
    )

    layout = strip.layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert not any(isinstance(w, GroupDivider) for w in widgets)


def test_the_strip_reports_a_taller_height_when_it_has_to_wrap(art_home, qapp):
    """The bug this guards: a strip that is one row tall however many tiles it holds.

    A ``QBoxLayout`` asks the *size policy* whether an item's height depends on its
    width and takes "no" for an answer, so the FlowLayout inside wrapped correctly
    while the card around it reserved a single row -- a 14-tile deck drew seven tiles
    and clipped the rest, in a card exactly as tall as a five-tile one.
    """
    _extract({f"Card {i}": "a.png" for i in range(14)}, qapp)
    strip = CardArtStrip()
    strip.set_groups([DeckGroup("Normal", "Normal Cards", tuple(_entry(f"Card {i}") for i in range(14)))])

    assert strip.sizePolicy().hasHeightForWidth()
    at_sidebar_width = strip.heightForWidth(390)
    assert at_sidebar_width > COLLAPSED_TILE, "14 tiles do not fit one row at a sidebar width"
    assert strip.heightForWidth(2000) <= COLLAPSED_TILE, "given room, they should need only one row"


def test_rebuilding_the_strip_replaces_what_was_there(art_home, qapp):
    _extract({"Tempest": "a.png", "Epic": "b.png"}, qapp)
    strip = CardArtStrip()
    strip.set_groups([DeckGroup("Normal", "Normal Cards", (_entry("Tempest"), _entry("Epic")))])

    strip.set_groups([DeckGroup("Normal", "Normal Cards", (_entry("Epic"),))])

    assert [tile.entry.template for tile in strip.tiles] == ["Epic"]
