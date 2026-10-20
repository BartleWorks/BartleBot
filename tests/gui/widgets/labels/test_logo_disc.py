"""Tests for ``LogoDisc``: the launch screen's logo disc, ringed in the accent.

The ring is the theme's and the disc is the logo's, so the two things worth pinning are
that the ring follows a theme switch and that adding it left the disc's snapped cells
exactly as ``logo_disc_image`` draws them.
"""

import math

from PyQt6.QtGui import QColor

from bartlebot.gui.logo import logo_disc_image
from bartlebot.gui.theming.theme import THEME, set_theme
from bartlebot.gui.widgets.labels.logo_disc import RING_GAP, RING_WIDTH, LogoDisc, ringed_disc_image

TRANSPARENT = QColor(0, 0, 0, 0).rgba()


def test_the_widget_is_the_disc_plus_the_gap_and_ring_on_every_side(qapp):
    disc = LogoDisc(160)

    side = 160 + 2 * (RING_GAP + RING_WIDTH)
    assert (disc.width(), disc.height()) == (side, side)


def _disc_pixels(image, disc, margin):
    """The pixels of a *disc* across disc sitting *margin* in from *image*'s corner.

    A pixel short of its edge, which is antialiased and so blends with whatever is behind.
    """
    radius = disc / 2
    return {
        (x, y): image.pixel(x + margin, y + margin)
        for y in range(disc)
        for x in range(disc)
        if math.hypot(x + 0.5 - radius, y + 0.5 - radius) <= radius - 1
    }


def test_the_disc_inside_the_ring_is_drawn_unchanged(qapp):
    """The disc lands on a whole pixel, so its cells stay as sharp as without a ring."""
    image = ringed_disc_image(172, 160, 2, QColor("#ff00ff"))

    assert _disc_pixels(image, 160, 6) == _disc_pixels(logo_disc_image(160), 160, 0)


def test_rounding_lands_in_the_gap_and_the_disc_stays_on_a_whole_pixel(qapp):
    # 125%: a 172 px widget is 215 pixels and a 160 px disc 200, an odd 15 to share.
    image = ringed_disc_image(215, 200, 2, QColor("#ff00ff"))

    assert _disc_pixels(image, 200, 7) == _disc_pixels(logo_disc_image(200), 200, 0)


def test_the_ring_is_separated_from_the_disc_by_the_gap(qapp):
    image = ringed_disc_image(172, 160, 2, QColor("#ff00ff"))
    middle = image.width() // 2

    assert QColor(image.pixel(middle, 1)).name() == "#ff00ff"
    assert image.pixel(middle, 2 + RING_GAP // 2) == TRANSPARENT


def test_the_widget_draws_its_image_one_to_one(qapp):
    """At any scaling, so neither the ring's color nor the disc's cells are resampled."""
    disc = LogoDisc(160)
    shot = disc.grab().toImage()
    dpr = disc.devicePixelRatioF()
    side, diameter = math.floor(disc.width() * dpr), math.floor(160 * dpr)
    margin = (side - diameter) // 2

    assert shot.width() == side
    assert _disc_pixels(shot, diameter, margin) == _disc_pixels(logo_disc_image(diameter), diameter, 0)


def test_the_ring_takes_the_accent_of_the_theme_in_force(qapp):
    """Read at paint time, so a theme switch repaints it with nothing baked in to go stale."""
    disc = LogoDisc(160)
    set_theme("light")
    try:
        shot = disc.grab().toImage()
        light = shot.pixel(shot.width() // 2, 1)
        assert QColor(light).name() == QColor(THEME.accent).name()
        set_theme("dark")
        shot = disc.grab().toImage()
        dark = shot.pixel(shot.width() // 2, 1)
        assert QColor(dark).name() == QColor(THEME.accent).name()
        assert light != dark  # the switch actually changed something
    finally:
        set_theme("dark")
