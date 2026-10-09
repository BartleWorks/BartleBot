"""Tests for ``svg_pixmap``'s memoisation.

A render is a file read, an XML parse and up to three off-screen paints, and the
arguments repeat relentlessly: a themed widget re-derives its icons on every theme
pass, and the app draws the same dozen marks at the same two or three sizes. One
test module was rendering 11,649 of them.

What is tested here is the contract that makes returning a *shared* pixmap safe --
same arguments give the same object, different arguments do not collide -- because
that sharing is the whole reason the cache is worth having, and it is also the way
it could go wrong: a caller that paints into the result would edit the cache for
everyone.
"""

from PyQt6.QtGui import QPixmap

from bartlebot.gui.theming.icon_utils import svg_pixmap

ICON = "checkmark"


def test_the_same_request_returns_the_same_pixmap(qapp):
    first = svg_pixmap(ICON, 16, tint="#ffffff")
    second = svg_pixmap(ICON, 16, tint="#ffffff")
    assert first is second


def test_a_different_tint_is_a_different_pixmap(qapp):
    white = svg_pixmap(ICON, 16, tint="#ffffff")
    black = svg_pixmap(ICON, 16, tint="#000000")
    assert white is not black
    assert white.toImage() != black.toImage()


def test_a_different_size_is_a_different_pixmap(qapp):
    small = svg_pixmap(ICON, 12, tint="#ffffff")
    large = svg_pixmap(ICON, 24, tint="#ffffff")
    assert small is not large
    assert small.size() != large.size()


def test_a_different_opacity_is_a_different_pixmap(qapp):
    """The disabled variant is the same mark at a lower opacity -- not the same key."""
    solid = svg_pixmap(ICON, 16, tint="#ffffff")
    faded = svg_pixmap(ICON, 16, tint="#ffffff", opacity=0.35)
    assert solid is not faded
    assert solid.toImage() != faded.toImage()


def test_an_untinted_render_is_its_own_entry(qapp):
    untinted = svg_pixmap(ICON, 16)
    tinted = svg_pixmap(ICON, 16, tint="#ff0000")
    assert untinted is not tinted


def test_the_render_is_a_real_icon_not_an_empty_pixmap(qapp):
    """A missing file makes QSvgRenderer draw nothing, which would cache a blank."""
    pm = svg_pixmap(ICON, 16, tint="#ffffff")
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()
    assert pm.size().width() == 16
    assert any(pm.toImage().pixelColor(x, y).alpha() > 0 for x in range(16) for y in range(16)), (
        "every pixel transparent means nothing was rendered"
    )
