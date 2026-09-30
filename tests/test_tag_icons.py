"""Bot tag medallions (#29): the extractor's table, its two composites, and the GUI forms.

The extractor needs a game install to run, so what is checked on that side is what can be
wrong without one: that the table covers the vocabulary, and that the composite builders
put their parts where they belong, on synthetic images. The GUI side is checked both ways
round: with a medallion extracted, and without one, which is the ordinary install.
"""

from __future__ import annotations

import json

import pytest
from PIL import Image, ImageDraw

from bartlebot import card_art
from bartlebot.bot_tags import BOT_TAGS
from bartlebot.card_art import tag_icon_name
from tools.extract_card_art import (
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


def test_a_tag_key_is_a_tidy_filename():
    assert tag_icon_name("Team-Up") == "tag_team_up"
    assert tag_icon_name("Treasure Cards") == "tag_treasure_cards"
    assert tag_icon_name("Gear") == "tag_gear"


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


# -- the GUI -----------------------------------------------------------------------------


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def _extract(*tags: str) -> None:
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    icons = {}
    for tag in tags:
        key = tag_icon_name(tag)
        Image.new("RGBA", (128, 128), (10, 200, 10, 255)).save(card_art.icons_dir() / f"{key}.png")
        icons[key] = f"{key}.png"
    card_art.index_path().write_text(json.dumps({"icons": icons}), encoding="utf-8")


def test_a_tag_with_an_extracted_medallion_resolves(art_home):
    _extract("Gear")

    assert card_art.tag_icon_path("Gear") == card_art.icons_dir() / "tag_gear.png"
    assert card_art.tag_icon_path("Boss") is None


def test_a_tag_icon_is_looked_up_once_until_the_cache_is_forgotten(qapp, art_home, monkeypatch):
    from bartlebot.gui.widgets.tag_picker import tag_icon

    _extract("Gear")
    calls = []
    real = card_art.tag_icon_path
    monkeypatch.setattr(card_art, "tag_icon_path", lambda tag: calls.append(tag) or real(tag))

    assert tag_icon("Gear") is not None
    assert tag_icon("Gear") is not None
    assert calls == ["Gear"]

    card_art.forget_cache()
    tag_icon("Gear")
    assert calls == ["Gear", "Gear"]


def test_forgetting_the_cache_lets_new_art_appear(qapp, art_home):
    from bartlebot.gui.widgets.tag_picker import tag_icon

    assert tag_icon("Gear") is None

    _extract("Gear")
    card_art.forget_cache()

    assert tag_icon("Gear") is not None


def test_the_pill_is_led_by_the_medallion_and_keeps_its_name(qapp, art_home):
    from bartlebot.gui.widgets.tag_picker import TAG_ICON_PX
    from bartlebot.gui.widgets.tag_pill import TagPill

    _extract("Gear")
    pill = TagPill("Gear")

    assert pill.has_icon
    assert pill.text() == "Gear"
    assert pill.height() == TAG_ICON_PX
    assert pill.width() > TAG_ICON_PX + 10


def test_the_medallion_is_the_pills_round_end(qapp, art_home):
    """The ring and the pill's left end are one circle: the corner outside the medallion
    stays clear, the medallion covers the left, and the body runs on to a round right end."""
    from PyQt6.QtGui import QColor

    from bartlebot.gui.widgets.tag_picker import TAG_ICON_PX, group_accent
    from bartlebot.gui.widgets.tag_pill import TagPill

    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    disc = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, 127, 127), fill=(10, 200, 10, 255))
    disc.save(card_art.icons_dir() / "tag_gear.png")
    card_art.index_path().write_text(json.dumps({"icons": {"tag_gear": "tag_gear.png"}}), encoding="utf-8")
    pill = TagPill("Gear")
    image = pill.grab().toImage()
    ratio = image.devicePixelRatio()

    def at(x, y):
        return image.pixelColor(round(x * ratio), round(y * ratio))

    h = TAG_ICON_PX
    assert at(0, 0).alpha() == 0, "no fill peeks past the ring's corner"
    assert at(h / 2, h / 2).green() > 150, "the medallion sits in the left end"
    fill = QColor(group_accent("Rewards"))
    body = at(pill.width() - h / 2, 1)
    assert (body.red(), body.green(), body.blue()) == (fill.red(), fill.green(), fill.blue())
    assert at(pill.width() - 1, 0).alpha() == 0, "the right end is round"


def test_the_pill_keeps_its_name_without_art(qapp, art_home):
    from bartlebot.gui.widgets.tag_picker import group_accent
    from bartlebot.gui.widgets.tag_pill import TagPill

    _extract("Gear")
    pill = TagPill("Boss")  # has a source, but this install extracted only Gear

    assert not pill.has_icon
    assert pill.text() == "Boss"
    assert group_accent("Activities") in pill.styleSheet()


def test_the_chip_takes_the_pills_medallion_shape(qapp, art_home):
    from bartlebot.gui.widgets.tag_picker import TAG_ICON_PX
    from bartlebot.gui.widgets.tag_pill import TagChip

    _extract("Boss")
    with_art, without = TagChip("Boss"), TagChip("Mobs")

    assert with_art.has_icon
    assert with_art.height() == TAG_ICON_PX
    assert "Boss" in with_art.text()
    assert not without.has_icon


def test_picker_rows_carry_the_medallion_where_there_is_one(qapp, art_home):
    from bartlebot.gui.widgets.tag_picker import tag_sections

    _extract("Dungeon")
    rows = {row.key: row for _title, section in tag_sections() for row in section}

    assert rows["Dungeon"].icon is not None
    assert rows["Boss"].icon is None
    assert rows["Boss"].accent, "a row with no medallion keeps its group dot"
