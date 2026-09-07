"""Tests for bartlebot.gui.deck_content — the Qt-free deck-content line builder."""

from __future__ import annotations

from bartlebot.bot.strategy_models import DeckData
from bartlebot.gui.deck_content import deck_content_lines


def test_lists_normal_and_item_sections_sorted_by_name():
    # Synthetic template names, deliberately not in data/spell_names.json — a
    # real template can be display-renamed (see spell_names.py's own "Soul
    # Burner" -> "Ammut" example), which would make an assertion on exact
    # display text depend on that snapshot's current content.
    deck = DeckData(normal={"Test Card B": 4, "Test Card A": 2}, item={"Test Item": 1})
    lines = deck_content_lines(deck)

    texts = [line.text for line in lines]
    assert texts == [
        "Normal Cards",
        "Test Card A × 2",
        "Test Card B × 4",
        "Item Cards",
        "Test Item × 1",
    ]


def test_section_headers_are_bold_and_cards_are_not():
    deck = DeckData(normal={"Test Card": 1})
    lines = deck_content_lines(deck)

    assert lines[0].text == "Normal Cards"
    assert lines[0].bold
    assert lines[1].text == "Test Card × 1"
    assert not lines[1].bold


def test_omits_a_section_with_no_cards():
    deck = DeckData(normal={"Test Card": 1})
    lines = deck_content_lines(deck)

    assert not any(line.text == "Item Cards" for line in lines)


def test_archmastery_line_is_first_and_names_the_source():
    deck = DeckData(normal={"Test Card": 1}, archmastery_school="Storm", apply_archmastery=True)
    lines = deck_content_lines(deck)

    assert lines[0].text == "Archmastery: Storm — set on apply (this deck)"
    assert not lines[0].bold


def test_no_archmastery_line_when_no_school_was_saved():
    deck = DeckData(normal={"Test Card": 1})
    lines = deck_content_lines(deck)

    assert not any(line.text.startswith("Archmastery:") for line in lines)


def test_treasure_cards_are_never_listed():
    deck = DeckData(normal={"Test Card": 1}, tc={"Test Treasure Card": 3})
    lines = deck_content_lines(deck)

    assert not any("Test Treasure Card" in line.text for line in lines)


def test_unknown_template_name_falls_back_to_itself():
    deck = DeckData(normal={"Not A Real Card Template": 1})
    lines = deck_content_lines(deck)

    assert lines[1].text == "Not A Real Card Template × 1"
