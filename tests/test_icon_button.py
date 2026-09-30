"""Tests for ``IconButton`` (issue #88).

``IconButton`` is the half of the button vocabulary ``ActionButton`` is not: a fixed
square with a glyph and no label. What is worth testing is therefore the boundary
between them — that this one does *not* expand and does honor the size its caller
asked for — plus the two things it owns, the toggle glyph and the tint.
"""

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QSizePolicy

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.theme import DARK, LIGHT, THEME
from bartlebot.gui.widgets.icon_button import IconButton, IconTint


@pytest.fixture
def theme():
    """Pin the active theme and restore it, as tests/test_themed_mixin.py does."""
    before = theme_mod.active_theme()
    yield theme_mod.set_theme_instance
    theme_mod.set_theme_instance(before)


def _image(button: IconButton):
    """The glyph as pixels — the only way to see a tint that lives in no stylesheet."""
    return button.icon().pixmap(button.iconSize()).toImage()


# ---------------------------------------------------------------------------
# Construction, and the line between this and ActionButton
# ---------------------------------------------------------------------------


def test_it_does_not_expand(qapp):
    """The distinguishing behavior: an ActionButton shares its row, this one does not."""
    button = IconButton("dismiss", tooltip="Remove")

    assert button.sizePolicy().horizontalPolicy() is not QSizePolicy.Policy.Expanding
    assert button.sizePolicy().verticalPolicy() is not QSizePolicy.Policy.Expanding


def test_it_honors_the_size_its_caller_asked_for(qapp):
    """Sized by the caller, because a toolbar's 32px and a flag row's 24px are both right."""
    button = IconButton("dismiss", tooltip="Remove", size=28, icon_size=18)

    assert button.size().width() == 28
    assert button.size().height() == 28
    assert button.iconSize().width() == 18


def test_a_tooltip_is_not_optional(qapp):
    """An icon-only control with no tooltip is unusable to anyone who does not
    already know the glyph, so it is a keyword argument with no default."""
    with pytest.raises(TypeError):
        IconButton("dismiss")  # type: ignore[call-arg]


def test_the_tooltip_is_what_was_asked_for(qapp):
    assert IconButton("dismiss", tooltip="Remove this condition").toolTip() == "Remove this condition"


# ---------------------------------------------------------------------------
# The toggle form
# ---------------------------------------------------------------------------


def test_a_plain_icon_button_is_not_checkable(qapp):
    """Checkable is what the favorite star needs, not what every ✕ needs."""
    assert not IconButton("dismiss", tooltip="Remove").isCheckable()


def test_the_toggle_form_swaps_its_glyph(qapp):
    button = IconButton("star_outline", checked_icon_name="star_filled", tooltip="Favorite")
    outline = _image(button)

    button.setChecked(True)

    assert button.isCheckable()
    assert _image(button) != outline


def test_the_toggle_form_swaps_back(qapp):
    """A star that filled and never emptied again would be a one-way switch."""
    button = IconButton("star_outline", checked_icon_name="star_filled", tooltip="Favorite")
    outline = _image(button)

    button.setChecked(True)
    button.setChecked(False)

    assert _image(button) == outline


def test_set_icon_name_swaps_a_glyph_that_is_not_a_toggle(qapp):
    """The transport's play/pause: driven by the bot's state, not by the button's."""
    button = IconButton("play", tooltip="Run bot")
    play = _image(button)

    button.set_icon_name("pause")

    assert _image(button) != play


# ---------------------------------------------------------------------------
# Tint — the only thing a variant varies
# ---------------------------------------------------------------------------


def test_the_variants_are_told_apart(qapp):
    """Four tints that rendered alike would be four names for one button."""
    kinds = {
        IconTint.DEFAULT: _image(IconButton("trash", tooltip="x", tint=IconTint.DEFAULT)),
        IconTint.ACCENT: _image(IconButton("trash", tooltip="x", tint=IconTint.ACCENT)),
        IconTint.DANGER: _image(IconButton("trash", tooltip="x", tint=IconTint.DANGER)),
    }
    assert kinds[IconTint.DEFAULT] != kinds[IconTint.ACCENT]
    assert kinds[IconTint.ACCENT] != kinds[IconTint.DANGER]
    assert kinds[IconTint.DEFAULT] != kinds[IconTint.DANGER]


def test_accent_when_enabled_spends_the_accent_only_while_it_can_act(qapp):
    """The Setup gutter's revert: being enabled is one of the two marks saying
    "this value is yours", so the accent is what carries it."""
    button = IconButton("revert", tooltip="Discard", tint=IconTint.ACCENT_WHEN_ENABLED)
    enabled = _image(button)

    button.setEnabled(False)

    assert _image(button) != enabled


def test_a_default_tint_button_looks_the_same_disabled_or_not(qapp, theme):
    """Only ACCENT_WHEN_ENABLED re-renders on enablement; the rest leave Qt's own
    disabled pixmap to say it, as every other control on the screen does."""
    theme(LIGHT)
    button = IconButton("trash", tooltip="Remove")
    enabled = _image(button)

    button.setEnabled(False)

    assert _image(button) == enabled


# ---------------------------------------------------------------------------
# The themed-restyle path
# ---------------------------------------------------------------------------


def test_it_re_renders_its_glyph_on_a_theme_switch(qapp, theme):
    """A bare ``svg_icon`` bakes its tint in at render time — the stale icon has no
    stylesheet anywhere for a text search to find, which is why this is a class."""
    theme(LIGHT)
    button = IconButton("trash", tooltip="Remove")
    light = _image(button)

    theme(DARK)
    button.refresh_theme_styles()

    assert _image(button) != light


def test_the_danger_tint_follows_the_theme_error_color(qapp, theme):
    """Derived, never stored: a theme that changes ``error`` moves the ✕ with it."""
    theme(replace(LIGHT, error="#aa0000"))
    red = _image(IconButton("dismiss", tooltip="Remove", tint=IconTint.DANGER))

    theme(replace(LIGHT, error="#00aa00"))
    green = _image(IconButton("dismiss", tooltip="Remove", tint=IconTint.DANGER))

    assert red != green


def test_it_draws_no_frame_of_its_own(qapp):
    """Fusion's beveled chrome around a 22px glyph is most of the widget."""
    style = IconButton("dismiss", tooltip="Remove").styleSheet()

    assert "background: transparent;" in style
    assert "border: none;" in style
    assert THEME.surface_disabled in style  # the hover, which is where the shape comes from
