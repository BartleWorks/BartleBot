"""Duel slot gems: the extractor's table, against the symbols core names each slot by (#29).

The gems, the right-to-left combat order and the read side are core's, tested there
(``tests/qt/widgets/cards/test_duel_slot_gem.py``).
"""

from __future__ import annotations

from bartleby.runtime.game import card_art
from bartleby.runtime.game.card_art import DUEL_SLOT_SYMBOLS, DUEL_SLOTS, duel_slot_icon_name

from bartlebot.game.art_extraction import (
    DUEL_SLOT_ICON_SOURCES,
    DUEL_SLOT_TEXTURES,
    ICON_SOURCES,
    STATE_SHEET_ICONS,
)

# -- keys, and the two tables that have to agree -----------------------------------------


def test_every_slot_has_a_texture_and_a_symbol():
    slots = set(range(1, DUEL_SLOTS + 1))
    assert set(DUEL_SLOT_TEXTURES) == slots
    assert set(DUEL_SLOT_SYMBOLS) == slots


def test_each_texture_is_the_gem_its_symbol_names():
    """The one guard on a pairing written down in two places.

    ``DUEL_SLOT_SYMBOLS`` is read at run time for tooltips on machines that never ran the
    extractor, so it cannot be derived from the texture table -- and a slot whose tooltip
    says "Star" over the moon gem is wrong in the one way a user cannot check.
    """
    for slot, texture in DUEL_SLOT_TEXTURES.items():
        assert texture.endswith(f"Art_Target_{DUEL_SLOT_SYMBOLS[slot]}.dds")


def test_slot_sources_are_full_paths_and_join_the_icons_map():
    assert all(t.startswith("GUI/") and t.endswith(".dds") for t in DUEL_SLOT_TEXTURES.values())
    assert DUEL_SLOT_ICON_SOURCES.items() <= ICON_SOURCES.items()


def test_no_gem_is_cropped():
    """They are single 64px images, not the 2x2 button-state sheets the worlds are."""
    assert not set(DUEL_SLOT_ICON_SOURCES) & STATE_SHEET_ICONS


def test_a_gem_is_not_a_school_medallion():
    """Sun, Star and Moon are also three real schools whose ``school_`` medallions look
    nothing like these. The keys must not collide, or one surface silently draws the
    other's art -- and it would look plausible."""
    assert not set(DUEL_SLOT_ICON_SOURCES) & {card_art.school_icon_name(s) for s in ("Sun", "Star", "Moon")}
    for school in ("Sun", "Star", "Moon"):
        assert ICON_SOURCES[card_art.school_icon_name(school)] != ICON_SOURCES[duel_slot_icon_name(1)]
