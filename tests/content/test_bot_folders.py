"""The bot library's folders: the table, the tree on disk, and the icons they need.

The folders are the game's badge categories, so three things can drift apart: the
folders under ``bots/``, the hand-written table in ``bot_folders``, and the snapshot of
the game's own list. Every test here pins two of them together.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QStyle

from bartlebot.content.bot_folders import (
    ALL_BADGE,
    FOLDER_BADGES,
    UNBADGED_FOLDERS,
    folder_badge,
    folder_caption,
    folder_number,
    folder_slug,
)
from bartlebot.game import badge_categories
from bartlebot.game.art_extraction import BADGE_ICON_SOURCES, ICON_SOURCES, SINGLE_IMAGE_BADGES, STATE_SHEET_ICONS
from bartlebot.game.card_art import RECENT_ICON, badge_icon_name
from bartlebot.gui.canvas.bot_canvas import (
    CATEGORY_LIST_WIDTH,
    CATEGORY_PAD_X,
)
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.pickers.picker_popup import SECTION_ICON_PX
from bartlebot.paths import project_root

BOTS = project_root() / "bots"


def _folders() -> list[Path]:
    return sorted(p for p in BOTS.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# The tree on disk against the table
# ---------------------------------------------------------------------------


def test_every_folder_is_a_known_category():
    """A folder missing from the table is listed with a made-up caption and no icon,
    which reads as correct -- so it has to fail here instead."""
    unknown = [p.name for p in _folders() if folder_slug(p.name) not in FOLDER_BADGES.keys() | UNBADGED_FOLDERS]

    assert unknown == []


def test_every_category_in_the_table_has_a_folder():
    """The empty ones included: the folder is how a user knows where a new bot goes."""
    on_disk = {folder_slug(p.name) for p in _folders()}

    assert set(FOLDER_BADGES) - on_disk == set()


def test_every_folder_is_kept_by_git():
    """Git stores no empty directory, which is how Novus's folder was lost before."""
    missing = [p.name for p in _folders() if not (p / ".gitkeep").is_file()]

    assert missing == []


def test_no_bot_sits_loose_at_the_top_of_the_library():
    """A file beside the folders ships as Official content and shows in every user's list
    (#253: the three test bots did). Test bots belong in ``tests/`` fixtures."""
    loose = [p.name for p in BOTS.iterdir() if p.is_file() and p.name != ".gitkeep"]

    assert loose == []


def test_no_shipped_bot_is_named_test():
    named = [str(p.relative_to(BOTS)) for p in BOTS.rglob("*.json") if p.stem.startswith("test_")]

    assert named == []


def test_the_category_folders_are_numbered_and_the_rest_are_not():
    for folder in _folders():
        if folder_slug(folder.name) in UNBADGED_FOLDERS:
            assert folder_number(folder.name) is None, folder.name
        else:
            assert folder_number(folder.name) is not None, folder.name


def test_the_numbers_follow_the_badge_page():
    """The number is the order Explorer and the library both show, and it is meant to be
    the game's. Sorting by name and by the game's list must agree."""
    order = {key: i for i, key in enumerate(badge_categories.keys())}
    numbered = sorted((folder_number(p.name), p.name) for p in _folders() if folder_number(p.name) is not None)

    positions = [order[folder_badge(name)] for _, name in numbered]

    assert positions == sorted(positions)
    assert len({n for n, _ in numbered}) == len(numbered)
    assert [name for _, name in numbered] == sorted(name for _, name in numbered)


# ---------------------------------------------------------------------------
# The table against the game's list
# ---------------------------------------------------------------------------


def test_every_folder_names_a_real_category():
    known = set(badge_categories.keys())

    assert [key for key in FOLDER_BADGES.values() if key not in known] == []
    assert ALL_BADGE in known


def test_every_folder_captions_itself_in_the_games_words():
    assert folder_caption("31_four_dungeons") == "Four Dungeons"
    assert folder_caption("30_five_boxes") == "Five B.O.X.E.S."
    assert folder_caption("33_monstrology") == "Monstrology"
    assert folder_caption("04_mooshu") == "MooShu"
    assert folder_caption("27_housing_gauntlets") == "Housing Gauntlet"


def test_a_folder_the_table_does_not_know_keeps_a_readable_caption():
    assert folder_badge("40_my_farms") is None
    assert folder_caption("40_my_farms") == "My Farms"
    assert folder_caption("templates") == "Templates"


def test_the_number_is_stripped_before_the_lookup():
    """So renumbering a folder never touches the table."""
    assert folder_badge("99_crafting") == folder_badge("crafting") == "Crafting"


# ---------------------------------------------------------------------------
# The icons those categories need
# ---------------------------------------------------------------------------


def test_the_extractor_produces_every_icon_the_library_asks_for():
    wanted = {badge_icon_name(key) for key in (*FOLDER_BADGES.values(), ALL_BADGE)}

    assert wanted - set(BADGE_ICON_SOURCES) == set()


def test_the_recent_row_has_an_uncropped_icon():
    """The self-timer watch is one 128px picture, not a state sheet."""
    assert ICON_SOURCES[RECENT_ICON] == "GUI/Buttons/Art_Selfie_Timer.dds"
    assert RECENT_ICON not in STATE_SHEET_ICONS


def test_only_the_single_image_badges_escape_the_crop():
    """Monstrology's texture is one 128px picture; cropping it keeps a quarter."""
    assert badge_icon_name("MonsterMagic") not in STATE_SHEET_ICONS
    assert badge_icon_name("4Dungeons") in STATE_SHEET_ICONS
    assert all(key in badge_categories.keys() for key in SINGLE_IMAGE_BADGES)


def test_the_badge_icons_are_read_from_the_games_table():
    """The pairings nobody could guess: the reason the table is not written by hand."""
    assert BADGE_ICON_SOURCES[badge_icon_name("FiveBOXES")].endswith("Badge_Category_Maestro_Events.dds")
    assert BADGE_ICON_SOURCES[badge_icon_name("SpecialEvents")].endswith("Badge_Category_Deckathalon.dds")


# ---------------------------------------------------------------------------
# The list is wide enough for them
# ---------------------------------------------------------------------------


def test_the_longest_caption_fits_beside_its_icon(qapp):
    """Measured with the running style, since the scrollbar and the font differ between
    a dev box and a CI runner. The icon-to-text gap is not a pixel metric Qt exposes, so
    a generous allowance stands in for it."""
    font = QFont(qapp.font())
    font.setPixelSize(THEME.font_small_px)
    metrics = QFontMetrics(font)
    widest = max(metrics.horizontalAdvance(folder_caption(slug)) for slug in FOLDER_BADGES)
    scrollbar = qapp.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
    gap = 8
    border = 1

    assert 2 * CATEGORY_PAD_X + SECTION_ICON_PX + gap + widest + scrollbar + border <= CATEGORY_LIST_WIDTH
