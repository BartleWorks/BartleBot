"""Tests for bartlebot.combat.deck_content — the Qt-free deck-content line builder."""

from __future__ import annotations

from bartleby.shared.strategy_models import DeckData

from bartlebot.combat.deck_content import archmastery_line, deck_content_lines, deck_groups


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


# -- the structured form, which card art is looked up by ---------------------


def test_groups_carry_the_template_name_beside_the_display_name():
    # The template name is the art lookup key and cannot be recovered from the display
    # name, since one display name can cover several templates.
    deck = DeckData(normal={"Test Card A": 2}, item={"Test Item": 1})

    groups = deck_groups(deck)

    assert [group.label for group in groups] == ["Normal", "Item"]
    assert [group.heading for group in groups] == ["Normal Cards", "Item Cards"]
    entry = groups[0].entries[0]
    assert (entry.template, entry.count) == ("Test Card A", 2)


def test_cards_with_no_known_school_sort_alphabetically():
    deck = DeckData(normal={"Test Card B": 1, "Test Card A": 1})

    assert [entry.template for entry in deck_groups(deck)[0].entries] == ["Test Card A", "Test Card B"]


def test_cards_are_grouped_by_school_in_the_games_order():
    # Storm before Balance before Shadow, and none of that is alphabetical -- it is
    # the order the game's own deck window uses. Real card names, because the school
    # comes from the shipped snapshot.
    deck = DeckData(normal={"Judgement": 1, "Tempest": 1, "Tower Shield": 1, "Epic": 1})

    order = [entry.template for entry in deck_groups(deck)[0].entries]

    assert order == ["Tower Shield", "Epic", "Tempest", "Judgement"]


def test_an_unknown_card_sorts_after_every_known_school():
    deck = DeckData(normal={"Not A Real Card Template": 1, "Tempest": 1})

    order = [entry.template for entry in deck_groups(deck)[0].entries]

    assert order == ["Tempest", "Not A Real Card Template"]


def test_an_empty_group_is_dropped_rather_than_returned_empty():
    deck = DeckData(normal={"Test Card": 1})

    assert [group.label for group in deck_groups(deck)] == ["Normal"]


def test_groups_never_include_treasure_cards():
    # They can be neither read nor restored, so tiling them would promise a round trip
    # that cannot happen -- the same reason the text list omits them.
    deck = DeckData(normal={"Test Card": 1}, tc={"Test Treasure Card": 3})

    templates = [entry.template for group in deck_groups(deck) for entry in group.entries]
    assert templates == ["Test Card"]


def test_an_unresolved_card_does_not_show_its_name_twice():
    deck = DeckData(normal={"Not A Real Card Template": 1})

    entry = deck_groups(deck)[0].entries[0]
    assert entry.display == entry.template
    assert not entry.shows_template


def test_no_archmastery_line_without_a_school():
    assert archmastery_line(DeckData(normal={"Test Card": 1})) is None


def test_the_archmastery_line_names_the_school_and_the_source():
    deck = DeckData(normal={"Test Card": 1}, archmastery_school="Storm", apply_archmastery=True)

    assert archmastery_line(deck) == "Archmastery: Storm — set on apply (this deck)"
