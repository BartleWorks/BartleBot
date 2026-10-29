"""Tests for the shared deck card list: what it lists, in what order, and with what art.

The rows themselves are the sidebar deck card's, extracted so the combat library's Deck
Presets page shows the same thing. What is worth testing here is the part neither
surface can be allowed to do its own way: the grouping and ordering come from
``deck_groups``, a card whose art is missing loses the tile and keeps the row, and an
empty deck says whatever the surface gave it to say.
"""

from __future__ import annotations

import json

import pytest
from bartleby.shared.strategy_models import DeckData
from PyQt6.QtGui import QColor, QPixmap

from bartlebot.game import card_art
from bartlebot.gui.widgets.cards.card_art_tile import CardArtTile
from bartlebot.gui.widgets.cards.deck_card_list import DeckCardList


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _extract(images: dict[str, str]) -> None:
    """Stand in for a real extraction: an index, and a PNG per named file."""
    card_art.spells_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"images": images}), encoding="utf-8")
    for name in set(images.values()):
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("red"))
        assert pixmap.save(str(card_art.spells_dir() / name), "PNG")


def _tiles(listing: DeckCardList) -> list[CardArtTile]:
    tiles: list[CardArtTile] = []
    for row in listing.rows:
        layout = row.layout()
        if layout is None:
            continue
        tiles.extend(
            widget for i in range(layout.count()) if isinstance(widget := layout.itemAt(i).widget(), CardArtTile)
        )
    return tiles


# -- what it lists -----------------------------------------------------------


def test_every_group_is_headed_and_every_card_is_a_row(qapp):
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Test Card A": 2, "Test Card B": 1}, item={"Test Item": 1}))

    assert listing.row_texts() == [
        "Normal Cards",
        "×2 Test Card A",
        "×1 Test Card B",
        "Item Cards",
        "×1 Test Item",
    ]


def test_cards_are_ordered_by_school_the_way_the_game_orders_them(qapp):
    # Real card names, because the school comes from the shipped snapshot. Neither
    # surface sorts this itself -- the order is ``deck_groups``'.
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Judgement": 1, "Tempest": 1, "Tower Shield": 1}))

    assert listing.row_texts()[1:] == ["×1 Tower Shield", "×1 Tempest", "×1 Judgement"]


def test_treasure_cards_are_never_listed(qapp):
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Test Card": 1}, tc={"Test Treasure Card": 3}))

    assert not any("Treasure" in text for text in listing.row_texts())


def test_no_deck_leaves_the_list_empty(qapp):
    listing = DeckCardList()

    listing.set_deck(None)

    assert listing.rows == []


def test_rebuilding_replaces_what_was_there(qapp):
    listing = DeckCardList()
    listing.set_deck(DeckData(normal={"Test Card A": 1, "Test Card B": 1}))

    listing.set_deck(DeckData(normal={"Test Card B": 1}))

    assert listing.row_texts() == ["Normal Cards", "×1 Test Card B"]


# -- the empty text ----------------------------------------------------------


def test_an_empty_deck_says_so_where_a_surface_gave_it_words(qapp):
    listing = DeckCardList(empty_text="(empty deck)")

    listing.set_deck(DeckData())

    assert listing.row_texts() == ["(empty deck)"]


def test_an_empty_deck_says_nothing_where_a_surface_did_not(qapp):
    """The sidebar's card refuses to unfold with no deck, so its list is never reached
    empty and a sentence there would be unreachable chrome."""
    listing = DeckCardList()

    listing.set_deck(DeckData())

    assert listing.rows == []


# -- the art -----------------------------------------------------------------


def test_a_card_with_extracted_art_gets_a_tile(art_home, qapp):
    _extract({"Test Card": "a.png"})
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Test Card": 2}))

    assert [tile.entry.template for tile in _tiles(listing)] == ["Test Card"]


def test_a_card_with_no_art_keeps_its_row_and_loses_only_the_tile(art_home, qapp):
    """Missing art is the ordinary case -- most installs never run the extractor -- so
    the row has to carry the deck on its own."""
    _extract({"Test Card A": "a.png"})
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Test Card A": 1, "Test Card B": 1}))

    assert [tile.entry.template for tile in _tiles(listing)] == ["Test Card A"]
    assert listing.row_texts() == ["Normal Cards", "×1 Test Card A", "×1 Test Card B"]


def test_nothing_extracted_still_lists_the_whole_deck(art_home, qapp):
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Test Card": 4}))

    assert _tiles(listing) == []
    assert listing.row_texts() == ["Normal Cards", "×4 Test Card"]


def test_a_row_names_the_template_only_where_it_differs_from_the_display_name(qapp):
    """Two cards can be drawn with the same art -- a tiered variant uses its base
    spell's texture -- and the template name is what tells them apart."""
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Storm Lord - T05 - A": 1}))

    row = listing.row_texts()[1]
    assert "Storm Lord - T05 - A" in row
    assert row.count("Storm Lord") == 2, "the display name and the template both appear"


def test_a_row_does_not_say_an_unresolved_name_twice(qapp):
    listing = DeckCardList()

    listing.set_deck(DeckData(normal={"Not A Real Card Template": 1}))

    assert listing.row_texts()[1] == "×1 Not A Real Card Template"


def test_new_art_gives_the_rows_their_tiles(qapp):
    """Art extracted while the list is up (#239): each row gains the tile it lacked."""
    from bartleby.qt.art_signal import announce_art_changed  # noqa: PLC0415

    from tests.extracted_art import write_extracted_art  # noqa: PLC0415

    listing = DeckCardList()
    listing.set_deck(DeckData(normal={"Test Card": 2}))
    assert _tiles(listing) == []

    write_extracted_art(images=["Test Card"])
    announce_art_changed()

    assert [tile.entry.template for tile in _tiles(listing)] == ["Test Card"]


def test_new_art_leaves_a_list_never_filled_empty(qapp):
    from bartleby.qt.art_signal import announce_art_changed  # noqa: PLC0415

    listing = DeckCardList(empty_text="No cards")
    announce_art_changed()

    assert listing.rows == []
