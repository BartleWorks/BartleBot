"""Bot tag medallions (#29): the extractor's table and its two composites.

The extractor needs a game install to run, so what is checked is what can be wrong without
one: that the table covers the vocabulary, and that the composite builders put their parts
where they belong, on synthetic images. The pill, the chip and the picker rows that draw them
are core's (its ``tests/qt/widgets/pills/test_tag_pill.py``).
"""

from __future__ import annotations

import pytest
from bartleby.runtime.game.card_art import tag_icon_name
from PIL import Image

from bartlebot.content.bot_tags import BOT_TAGS
from bartlebot.game.art_extraction import (
    BAND_COLOR,
    ICON_SOURCES,
    STATE_SHEET_ICONS,
    TAG_COMPOSITES,
    TAG_ICON_SOURCES,
    TAG_TEXTURES,
    challenge_icon,
    gauntlet_disc,
    gauntlet_icon,
    ring_inner_radius,
)

# -- keys ------------------------------------------------------------------------------


def test_no_two_tags_share_a_key():
    assert len({tag_icon_name(t) for t in BOT_TAGS}) == len(BOT_TAGS)


# -- the extractor's table ---------------------------------------------------------------


def test_every_tag_has_exactly_one_source():
    """A tag added to the vocabulary with no medallion keeps its text pill silently;
    this is where that gap shows."""
    assert set(TAG_TEXTURES) | set(TAG_COMPOSITES) == set(BOT_TAGS)
    assert not set(TAG_TEXTURES) & set(TAG_COMPOSITES)


def test_tag_sources_are_full_paths_and_join_the_icons_map():
    """Full paths, because the index holds both kinds of key and a basename is
    first-archive-wins."""
    assert all(t.startswith("GUI/") and t.endswith(".dds") for t in TAG_TEXTURES.values())
    assert TAG_ICON_SOURCES.items() <= ICON_SOURCES.items()


def test_no_tag_medallion_is_cropped():
    """The medallions are single images; only the Gauntlet badge inside a composite is a
    state sheet, and its builder crops that itself."""
    assert not set(TAG_ICON_SOURCES) & STATE_SHEET_ICONS


def test_dungeon_and_challenge_mode_share_the_sigil():
    textures, _build = TAG_COMPOSITES["Challenge Mode"]
    assert textures[0] == TAG_TEXTURES["Dungeon"]


# -- the composites ----------------------------------------------------------------------


def _ring(size: int = 128, inner: int = 51) -> Image.Image:
    """A gold ring with a transparent center of radius *inner*."""
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c = (size - 1) / 2
    px = frame.load()
    for y in range(size):
        for x in range(size):
            if inner <= ((x - c) ** 2 + (y - c) ** 2) ** 0.5 <= size / 2:
                px[x, y] = (230, 180, 40, 255)
    return frame


def test_the_ring_radius_is_read_from_the_frame():
    assert ring_inner_radius(_ring(inner=51)) == pytest.approx(51, abs=1)


def test_a_frame_with_no_hole_is_refused():
    with pytest.raises(ValueError):
        ring_inner_radius(Image.new("RGBA", (64, 64), (200, 200, 0, 255)))


def test_the_skull_lands_above_and_left_of_center():
    sigil = Image.new("RGBA", (128, 128), (0, 128, 0, 255))
    skull = Image.new("RGBA", (32, 32), (255, 0, 0, 255))

    icon = challenge_icon(sigil, skull)

    red = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    red.paste(icon, mask=icon.split()[0].point(lambda v: 255 if v == 255 else 0))
    left, top, right, bottom = red.getbbox()
    assert right - left == round(128 * 0.378)
    assert (left + right) / 2 < 64 - 4  # 0.05 of the width to the left
    assert (top + bottom) / 2 < 64 - 14  # 0.13 of it up
    assert sigil.getpixel((64, 40)) == (0, 128, 0, 255), "the source texture is not drawn on"


def test_the_disc_is_green_with_a_dark_band_and_nothing_outside():
    disc = gauntlet_disc(128, 51)

    r, g, b, a = disc.getpixel((64, 64))
    assert a == 255 and g > r and g > b
    edge = disc.getpixel((round(63.5 + 51.5), 64))  # on the band
    assert edge[:3] == pytest.approx(BAND_COLOR, abs=3)
    assert disc.getpixel((0, 0))[3] == 0


def test_the_gauntlet_is_framed_by_the_ring():
    frame = _ring()
    sheet = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    sheet.paste((0, 0, 255, 255), (20, 20, 108, 108))  # the normal state's artwork
    sheet.paste((255, 0, 255, 255), (148, 148, 236, 236))  # a grayed state, never drawn

    icon = gauntlet_icon(frame, sheet)

    assert icon.size == frame.size
    assert icon.getpixel((64, 64))[:3] == (0, 0, 255)
    assert icon.getpixel((2, 64)) == frame.getpixel((2, 64)), "the ring is drawn last"
    assert all(icon.getpixel((x, 64))[:3] != (255, 0, 255) for x in range(128))
