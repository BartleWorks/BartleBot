"""The world emblem table: that it covers the map, and that a state sheet is cropped.

The extractor itself needs a game install and a WAD, so what is checked here is the part
that can be wrong without one — the mapping — plus the crop, which is pure Pillow.

``tools.extract_card_art`` is imported directly, the way ``test_dialog_captions`` and
``test_zone_nav_edits`` import their tools.
"""

from __future__ import annotations

from PIL import Image

from bartlebot.game import zone_nav
from bartlebot.game.card_art import DUNGEON_ICON, world_icon_name
from bartlebot.game.worlds import DUNGEON_WORLDS, SPIRAL_DOOR_WORLDS
from tools.extract_card_art import (
    DUNGEON_TEXTURE,
    ICON_SOURCES,
    STATE_SHEET_ICONS,
    WORLD_ICON_SOURCES,
    WORLD_TEXTURES,
    top_left_quadrant,
)


def test_every_world_on_the_map_has_an_emblem():
    """The 25/25 guard. A world added to the nav map with no entry here loses its icon
    silently, which is exactly the kind of gap a header full of captions hides."""
    nav = zone_nav.load()

    missing = [key for key in nav.worlds if world_icon_name(key) not in WORLD_ICON_SOURCES]

    assert missing == []


def test_every_spiral_door_world_has_an_emblem():
    missing = [w for w in SPIRAL_DOOR_WORLDS if world_icon_name(w) not in WORLD_ICON_SOURCES]

    assert missing == []


def test_the_dungeon_worlds_all_point_at_the_one_badge():
    for world in DUNGEON_WORLDS:
        assert WORLD_ICON_SOURCES[world_icon_name(world)] == DUNGEON_TEXTURE
    assert WORLD_ICON_SOURCES[DUNGEON_ICON] == DUNGEON_TEXTURE


def test_no_dungeon_world_also_claims_a_world_button():
    """A key in both tables would resolve by whichever dict merged last."""
    assert not set(DUNGEON_WORLDS) & set(WORLD_TEXTURES)


def test_the_worlds_that_cannot_be_derived_are_spelled_out():
    """The two the game names differently from its own world key. A normalizing rule is
    right for twenty of these and silently wrong for these two."""
    assert WORLD_TEXTURES["Wysteria"] == "Pigswick_Academy"
    assert WORLD_TEXTURES["WizardCity"] == "Wizard_City"
    assert WORLD_TEXTURES["DragonSpire"] == "Dragonspire"


def test_aquila_has_its_own_emblem_though_it_is_off_the_door():
    assert WORLD_TEXTURES["Aquila"] == "Aquila"
    assert "Aquila" not in SPIRAL_DOOR_WORLDS


def test_the_world_icons_join_the_school_and_archmastery_ones():
    """One icons map, one directory: the GUI asks for all of them by logical name."""
    assert WORLD_ICON_SOURCES.items() <= ICON_SOURCES.items()
    assert "archmastery" in ICON_SOURCES


def test_every_world_emblem_is_cropped_and_the_school_icons_are_not():
    """The world buttons and the badge categories are 2x2 state sheets; the school
    medallions are single images. Cropping the wrong set is a quarter-size icon."""
    assert set(WORLD_ICON_SOURCES) <= STATE_SHEET_ICONS
    assert "archmastery" not in STATE_SHEET_ICONS
    assert world_icon_name("Krokotopia") in STATE_SHEET_ICONS


def test_the_crop_takes_the_top_left_quarter_at_native_size():
    """128px out of a 256px sheet, with no resample: the quadrant is already the size
    the GUI wants."""
    sheet = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
    sheet.paste((255, 0, 0, 255), (0, 0, 128, 128))  # the normal state
    sheet.paste((9, 9, 9, 255), (128, 128, 256, 256))  # the grayed one

    quadrant = top_left_quadrant(sheet)

    assert quadrant.size == (128, 128)
    assert quadrant.getpixel((0, 0)) == (255, 0, 0, 255)
    assert quadrant.getpixel((127, 127)) == (255, 0, 0, 255)


def test_the_crop_halves_whatever_it_is_given():
    assert top_left_quadrant(Image.new("RGBA", (64, 64))).size == (32, 32)
