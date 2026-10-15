"""Duel slot gems, and the right-to-left combat order they came with (#29).

A duel lines a team up right to left -- slot 1 is the rightmost wizard, and the game's own
"Set Sigil Slot Preference" dialog lists 4, 3, 2, 1 in that order, each marked with a gold
diamond. This app drew the same four positions left to right under ``1st``…``4th``, which
is the opposite of what the user sees in the fight they are configuring.

So these tests are about **agreement**: that the extractor's table and the run-time symbol
names say the same thing, and that every surface showing a combat order mirrors it the
same way. A half-mirrored app is worse than the unmirrored one it replaced, because the
two surfaces then disagree about which end enters first.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from bartlebot.combat.strategy_summary import format_combat_order
from bartlebot.game import card_art
from bartlebot.game.card_art import DUEL_SLOT_SYMBOLS, DUEL_SLOTS, duel_slot_icon_name
from tools.extract_card_art import (
    DUEL_SLOT_ICON_SOURCES,
    DUEL_SLOT_TEXTURES,
    ICON_SOURCES,
    STATE_SHEET_ICONS,
)

# -- keys, and the two tables that have to agree -----------------------------------------


def test_a_slot_key_is_its_number():
    assert duel_slot_icon_name(1) == "duel_slot_1"
    assert duel_slot_icon_name(4) == "duel_slot_4"


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


def test_the_slots_are_sun_eye_star_moon_in_that_order():
    """The order is the game's, read off its own dialog, and is not alphabetical or
    derivable from anything. Spelled out so a reorder has to be deliberate."""
    assert [DUEL_SLOT_SYMBOLS[s] for s in range(1, DUEL_SLOTS + 1)] == ["Sun", "Eye", "Star", "Moon"]


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


# -- the read side -----------------------------------------------------------------------


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _extract(*slots: int) -> None:
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    icons = {}
    for slot in slots:
        key = duel_slot_icon_name(slot)
        Image.new("RGBA", (64, 64), (200, 170, 40, 255)).save(card_art.icons_dir() / f"{key}.png")
        icons[key] = f"{key}.png"
    card_art.index_path().write_text(json.dumps({"icons": icons}), encoding="utf-8")


def test_a_slot_with_an_extracted_gem_resolves(art_home):
    _extract(1)

    assert card_art.duel_slot_icon_path(1) == card_art.icons_dir() / "duel_slot_1.png"
    assert card_art.duel_slot_icon_path(2) is None


def test_no_art_is_the_ordinary_case(art_home):
    assert card_art.duel_slot_icon_path(1) is None


def test_the_tooltip_names_the_symbol_without_any_art(art_home):
    """Most installs never run the extractor; the tooltip is the only thing left that
    connects this app's "slot 1" to the diamond the user picked in-game."""
    from bartlebot.gui.widgets.cards.duel_slot_gem import duel_slot_tooltip  # noqa: PLC0415

    assert "Sun" in duel_slot_tooltip(1)
    assert "Moon" in duel_slot_tooltip(4)


# -- the text form -----------------------------------------------------------------------


def test_the_order_facet_reads_right_to_left():
    assert format_combat_order([1, 2, 3]) == "P3 ← P2 ← P1"


def test_an_empty_order_is_still_a_word():
    assert format_combat_order([]) == "Simultaneous"


def test_the_facet_reverses_the_stored_list_and_not_the_caller_s():
    """The reversal happens in the formatter and nowhere else, so a caller can keep
    handing over the first-to-last list it stores."""
    order = [3, 1, 2]

    assert format_combat_order(order) == "P2 ← P1 ← P3"
    assert order == [3, 1, 2]


# -- the editor --------------------------------------------------------------------------


def _chip_players(widget) -> list[int]:
    """The chips left to right, as they are laid out on screen."""
    layout = widget._chip_layout
    return [layout.itemAt(i).widget()._player_num for i in range(layout.count())]


def _visible_header_slots(widget) -> list[int]:
    """The header cells left to right, by the slot each stands for.

    ``isHidden`` rather than ``isVisible``: nothing here is ever shown on screen, and
    ``isVisible`` is false for every child of an unshown window. What is being asserted is
    the explicit ``setVisible`` state, which is what ``isHidden`` reports.
    """
    layout = widget._header_row.layout()
    cells = [layout.itemAt(i).widget() for i in range(layout.count())]
    return [widget._header_cells.index(cell) + 1 for cell in cells if not cell.isHidden()]


def test_the_chips_are_drawn_right_to_left(qapp):
    from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget  # noqa: PLC0415

    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])

    assert _chip_players(widget) == [4, 3, 2, 1]


def test_the_value_is_still_stored_first_to_last(qapp):
    """Only the drawing is mirrored. A widget that also reversed its value would silently
    invert every bot file it saved."""
    from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget  # noqa: PLC0415

    widget = CombatOrderWidget()
    widget.set_value([3, 1, 4, 2])

    assert widget.get_value() == [3, 1, 4, 2]
    assert _chip_players(widget) == [2, 4, 1, 3]


def test_each_header_cell_sits_over_its_own_chip(qapp):
    """The gems and the chips are built by two different loops, so this is the assertion
    that keeps them from drifting apart."""
    from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget  # noqa: PLC0415

    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])

    # Header cell i names position i; the chip beneath it holds that position's player.
    positions = _visible_header_slots(widget)
    assert positions == [4, 3, 2, 1]
    assert [widget.get_value()[p - 1] for p in positions] == _chip_players(widget)


def test_a_two_player_duel_keeps_sun_and_eye_not_star_and_moon(qapp):
    """Hiding "the last few" cells would hide the two a two-player duel actually has, the
    row being laid out reversed."""
    from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget  # noqa: PLC0415

    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])

    widget.set_max_players(2)

    assert _visible_header_slots(widget) == [2, 1]
    assert _chip_players(widget) == [2, 1]


def test_a_widget_built_small_hides_its_spare_cells(qapp):
    """The cells are built to four and hidden down, so a widget constructed at two has to
    hide them at construction rather than wait for a ``set_max_players`` that never comes."""
    from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget  # noqa: PLC0415

    widget = CombatOrderWidget(max_players=2)

    assert _visible_header_slots(widget) == [2, 1]


def test_a_swap_still_reorders_by_player(qapp):
    """Drag and drop names players, not positions, so the mirroring must not reach it."""
    from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget  # noqa: PLC0415

    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])

    widget._swap_players(1, 4)

    assert widget.get_value() == [4, 2, 3, 1]
    assert _chip_players(widget) == [1, 3, 2, 4]


# -- the summary row ---------------------------------------------------------------------


def test_the_summary_row_mirrors_and_gems_each_position(qapp, art_home):
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadgeRow  # noqa: PLC0415

    row = PlayerBadgeRow("←", duel_order=True)
    row.set_players([1, 2, 3])

    assert [b.player for b in row.badges] == [3, 2, 1]
    # Position 3 is leftmost; the gem names the position, not the player in it.
    assert [g.slot for g in row._gems[:3]] == [3, 2, 1]


def test_an_assignment_row_is_not_mirrored_and_wears_no_gems(qapp):
    """An assignment names a *set* of players and has no order to mirror."""
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadgeRow  # noqa: PLC0415

    row = PlayerBadgeRow()
    row.set_players([1, 2, 3])

    assert [b.player for b in row.badges] == [1, 2, 3]
    assert row._gems == []


def test_a_gem_is_hidden_where_there_is_no_art(qapp, art_home):
    """An empty 20px column would push the badges out of line with everything else."""
    from bartlebot.gui.widgets.pills.player_pills import PlayerBadgeRow  # noqa: PLC0415

    row = PlayerBadgeRow("←", duel_order=True)
    row.set_players([1, 2])

    assert all(g.isHidden() for g in row._gems)
