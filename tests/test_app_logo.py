"""Tests for the application logo (#26): rasters snapped from the SVG, and where they show.

The logo is pixel art, and the reason it is not just ``QIcon("bartlebot.svg")`` is
that a cell must be a whole number of pixels at every size or the art smears. These
tests pin that down for each framing: the square avatar, the tree alone that is the
window icon, and the disc on the launch screen.
"""

import math

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.gui import logo
from bartlebot.gui.game_embed.game_view import _GamePlaceholder
from bartlebot.gui.logo import (
    GRID,
    ICON_SIZES,
    art_size,
    cell_size,
    disc_cell_size,
    install_app_icon,
    logo_disc_image,
    logo_icon,
    logo_image,
    refresh_taskbar_icon,
    tree_image,
    tree_scale,
)
from bartlebot.gui.widgets.logo_disc import LogoDisc

BACKGROUND = QColor("#4488cc").rgba()
TREE = {QColor(c).rgba() for c in ("#22b14c", "#b97a57", "#7092be", "#3f48cc")}
PALETTE = TREE | {BACKGROUND}
TRANSPARENT = QColor(0, 0, 0, 0).rgba()

# The tree as drawn: 10 cells wide, 9 tall.
ART_WIDTH, ART_HEIGHT = 10, 9


def _colors(image):
    return {image.pixel(x, y) for y in range(image.height()) for x in range(image.width())}


def _art_bounds(image):
    drawn = [(x, y) for y in range(image.height()) for x in range(image.width()) if image.pixel(x, y) in TREE]
    xs, ys = [x for x, _ in drawn], [y for _, y in drawn]
    return min(xs), min(ys), max(xs), max(ys)


def test_the_tree_is_read_from_the_svg(qapp):
    assert art_size() == (ART_WIDTH, ART_HEIGHT)


# --- the square, for avatars ---------------------------------------------------------


@pytest.mark.parametrize("size", ICON_SIZES)
def test_every_square_size_uses_only_the_svgs_colors(qapp, size):
    # A blended color is a cell edge that fell between pixels: the smear this exists to prevent.
    image = logo_image(size)
    assert (image.width(), image.height()) == (size, size)
    assert _colors(image) == PALETTE


@pytest.mark.parametrize("size", ICON_SIZES)
def test_the_square_keeps_whole_cells_centered_with_a_cell_of_margin(qapp, size):
    scale = cell_size(size)
    left, top, right, bottom = _art_bounds(logo_image(size))
    assert (right - left + 1, bottom - top + 1) == (ART_WIDTH * scale, ART_HEIGHT * scale)
    assert abs(left - (size - 1 - right)) <= 1
    assert abs(top - (size - 1 - bottom)) <= 1
    assert min(left, top) >= scale


def test_a_square_on_the_grid_is_the_drawing_exactly(qapp):
    # At 4 px per cell the SVG needs no snapping, so the raster and a straight render agree.
    assert cell_size(48) == 4
    left, top, _, _ = _art_bounds(logo_image(48))
    assert (left, top) == (1 * 4, int(1.5 * 4))
    assert cell_size(GRID * 21) == 21


# --- the tree alone, for the window icon ---------------------------------------------


@pytest.mark.parametrize("size", ICON_SIZES)
def test_the_tree_alone_has_no_background_and_no_blends(qapp, size):
    colors = _colors(tree_image(size))
    assert TRANSPARENT in colors
    assert colors - {TRANSPARENT} == TREE


@pytest.mark.parametrize("size", ICON_SIZES)
def test_the_tree_alone_fills_the_square(qapp, size):
    scale = tree_scale(size)
    left, top, right, bottom = _art_bounds(tree_image(size))
    assert right - left + 1 == math.floor(ART_WIDTH * scale)
    assert bottom - top + 1 == math.floor(ART_HEIGHT * scale)
    # The next whole cell up would not fit.
    assert ART_WIDTH * (math.floor(scale) + 1) > size


def test_the_tree_scale_is_whole_from_two_pixels_up_and_halves_only_below():
    assert tree_scale(16) == 1.5
    assert tree_scale(20) == 2
    assert tree_scale(24) == 2
    assert tree_scale(32) == 3
    assert tree_scale(48) == 4
    assert tree_scale(256) == 25


# --- the disc, for the launch screen -------------------------------------------------


@pytest.mark.parametrize("diameter", [160, 200, 240, 320])
def test_the_disc_keeps_the_tree_inside_it(qapp, diameter):
    image = logo_disc_image(diameter)
    scale = disc_cell_size(diameter)
    assert scale >= 1
    # Corners are outside the disc, and the center is on it.
    assert image.pixel(0, 0) == TRANSPARENT
    assert image.pixel(diameter - 1, diameter - 1) == TRANSPARENT
    left, top, right, bottom = _art_bounds(image)
    assert (right - left + 1, bottom - top + 1) == (ART_WIDTH * scale, ART_HEIGHT * scale)
    # Every pixel of the tree has at least a cell of disc between it and the edge.
    radius = diameter / 2
    tree = [(x, y) for y in range(diameter) for x in range(diameter) if image.pixel(x, y) in TREE]
    assert max(math.hypot(x + 1 - radius, y + 1 - radius) for x, y in tree) + scale <= radius + 1


def test_the_disc_cell_leaves_a_cell_of_disc_past_the_feet(qapp):
    # At 160 px, 12 px per cell would leave less than a cell between a foot and the edge.
    assert disc_cell_size(160) == 11


def test_the_launch_screen_shows_the_logo_disc(qapp):
    placeholder = _GamePlaceholder()
    assert placeholder.findChild(LogoDisc) is not None


# --- the window icon -----------------------------------------------------------------


def test_the_window_icon_is_the_tree_at_every_size(qapp):
    icon = logo_icon()
    assert {(s.width(), s.height()) for s in icon.availableSizes()} == {(s, s) for s in ICON_SIZES}
    assert icon.pixmap(16, 16).toImage().pixel(0, 0) == TRANSPARENT


def test_installing_sets_the_application_window_icon(qapp, monkeypatch):
    # The taskbar id is per process and outlives the test, so the Windows call is stubbed.
    monkeypatch.setattr(logo.sys, "platform", "linux")
    install_app_icon(QApplication.instance())
    assert {(s.width(), s.height()) for s in QApplication.windowIcon().availableSizes()} >= {(16, 16), (256, 256)}


def test_refreshing_the_taskbar_icon_hands_the_window_a_new_icon(qapp):
    window = QWidget()
    window.setWindowIcon(logo_icon())
    before = window.windowIcon().cacheKey()
    refresh_taskbar_icon(window)
    assert window.windowIcon().cacheKey() != before
    assert not window.windowIcon().isNull()
