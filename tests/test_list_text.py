"""Comma-separated text <-> list[str], for the param forms that edit string lists.

The property under test is the round trip. A naive split broke on the one shipped
item name that contains a comma ("No Pain, No Gain Onyx"), turning one keep-list
entry into two — one of which then matched dozens of unrelated items.
"""

from __future__ import annotations

import pytest

from bartlebot.gui.widgets.inputs.list_text import format_list_text, parse_list_text


@pytest.mark.parametrize(
    "values",
    [
        [],
        ["Amber"],
        ["Amber", "Bone Dust"],
        ["No Pain, No Gain Onyx"],
        ["Amber", "No Pain, No Gain Onyx", "Balance Crushing Pin (60)"],
        ["Dragon's Claw"],
        ['He said "hi"'],
        ["a,b,c"],
    ],
)
def test_round_trip(values):
    assert parse_list_text(format_list_text(values)) == values


def test_quotes_only_the_entries_that_need_it():
    assert format_list_text(["Amber", "No Pain, No Gain Onyx"]) == 'Amber, "No Pain, No Gain Onyx"'


def test_plain_entries_stay_readable():
    assert format_list_text(["Amber", "Bone Dust"]) == "Amber, Bone Dust"


# ---------------------------------------------------------------------------
# Parsing what a user might type
# ---------------------------------------------------------------------------


def test_empty_text_is_an_empty_list():
    assert parse_list_text("") == []
    assert parse_list_text("   ") == []


def test_surrounding_brackets_are_tolerated():
    assert parse_list_text("[Amber, Bone Dust]") == ["Amber", "Bone Dust"]


def test_hand_typed_single_quotes_are_stripped():
    assert parse_list_text("'Amber', 'Bone Dust'") == ["Amber", "Bone Dust"]


def test_blank_entries_are_dropped():
    # An empty keyword is a substring of every name and would keep the backpack.
    assert parse_list_text("Amber,,Bone Dust, ") == ["Amber", "Bone Dust"]


def test_a_half_typed_quote_still_parses():
    # Reached on every keystroke while the user is typing, so it must not raise.
    assert parse_list_text('Amber, "No Pain') == ["Amber", "No Pain"]
