"""Tests for ``ActionButton``'s four weights (issue #47).

What matters is not which hex each style lands on but that the four are *told apart*, and
that the default is the quiet one. Most rows in this app are equal-weight verbs beside a
destructive one, and a style that had to be opted out of would make every such row shout.
"""

from dataclasses import replace

import pytest

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.theme import DARK, LIGHT, THEME
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle, _mix


@pytest.fixture
def theme():
    """Pin the active theme and restore it, as tests/test_themed_mixin.py does."""
    before = theme_mod.active_theme()
    yield theme_mod.set_theme_instance
    theme_mod.set_theme_instance(before)


def test_the_default_weight_is_the_quiet_one(qapp):
    """A style is opted into, at the one place a surface really has a primary action."""
    button = ActionButton("plus", "New")

    assert button.action_style is ActionStyle.TERTIARY


def test_tertiary_claims_no_accent(qapp):
    style = ActionButton("plus", "New").styleSheet()

    assert THEME.surface in style
    assert THEME.accent not in style


def test_primary_is_filled_with_the_accent(qapp):
    style = ActionButton("checkmark", "Save", style=ActionStyle.PRIMARY).styleSheet()

    assert f"background: {THEME.accent};" in style
    assert f"color: {THEME.pill_text};" in style


def test_secondary_is_outlined_in_the_accent_over_a_wash_of_it(qapp):
    """Unfilled, the outline alone read as a tinted ``TERTIARY`` on a near-white field."""
    style = ActionButton("revert", "Use default", style=ActionStyle.SECONDARY).styleSheet()

    assert "background: transparent;" not in style
    assert f"border: 1px solid {THEME.accent};" in style
    # Between the page and the accent: a wash, not a fill, and not the page untouched.
    assert f"background: {THEME.bg};" not in style
    assert f"background: {THEME.accent};" not in style


def test_the_secondary_wash_follows_the_theme_accent(qapp, theme):
    """The test that stops someone baking the mixed hex in as a literal."""
    theme(replace(LIGHT, accent="#aa00aa"))
    purple = ActionButton("revert", "Use default", style=ActionStyle.SECONDARY).styleSheet()

    theme(replace(LIGHT, accent="#00aa00"))
    green = ActionButton("revert", "Use default", style=ActionStyle.SECONDARY).styleSheet()

    assert f"background: {_mix('#ffffff', '#aa00aa', 0.10)};" in purple
    assert purple != green


def test_a_dark_page_takes_more_of_the_wash(qapp, theme):
    """10% of the accent over ``#1e1e1e`` is all but invisible; the light theme's is not."""
    theme(LIGHT)
    light = ActionButton("revert", "x", style=ActionStyle.SECONDARY).styleSheet()

    theme(DARK)
    dark = ActionButton("revert", "x", style=ActionStyle.SECONDARY).styleSheet()

    assert f"background: {_mix(LIGHT.bg, LIGHT.accent, 0.10)};" in light
    assert f"background: {_mix(DARK.bg, DARK.accent, 0.18)};" in dark


def test_the_wash_is_picked_off_lightness_not_a_literal_background(qapp, theme):
    """A custom light theme need not be ``#ffffff``, and must not get the dark wash."""
    off_white = replace(LIGHT, bg="#fbfaf6")
    theme(off_white)

    sheet = ActionButton("revert", "x", style=ActionStyle.SECONDARY).styleSheet()

    assert f"background: {_mix(off_white.bg, off_white.accent, 0.10)};" in sheet


def test_caution_is_filled_with_the_error_colour(qapp):
    style = ActionButton("trash", "Delete", style=ActionStyle.CAUTION).styleSheet()

    assert f"background: {THEME.error};" in style


def test_every_style_shares_the_same_metrics(qapp):
    """A weight varies colour. A style that also changed the padding would stop being a
    weight and start being a shape."""
    styles = [ActionButton("plus", "x", style=s).styleSheet() for s in ActionStyle]

    assert all("padding: 4px 10px;" in s for s in styles)
    assert all("border-radius: 4px;" in s for s in styles)


def test_the_disabled_look_is_one_look_for_all_four(qapp):
    """A disabled button is not a weaker version of its style; it is the same "off" every
    disabled control on the screen wears."""
    for style in ActionStyle:
        sheet = ActionButton("plus", "x", style=style).styleSheet()
        assert f"QPushButton:disabled {{ color: {THEME.text_disabled};" in sheet


def test_a_disabled_button_has_no_visible_outline(qapp):
    """Its border is its fill: enabled ``TERTIARY`` is only ~3% off ``surface_disabled``,
    so the outline is what actually separates off from on."""
    for style in ActionStyle:
        sheet = ActionButton("plus", "x", style=style).styleSheet()
        disabled = sheet.split("QPushButton:disabled")[1]
        assert f"background: {THEME.surface_disabled};" in disabled
        assert f"border-color: {THEME.surface_disabled};" in disabled


def test_changing_the_style_restyles_the_button(qapp):
    """Delete becoming Revert is no longer a caution — the shipped file reappears
    underneath, so there is nothing to lose."""
    button = ActionButton("trash", "Delete", style=ActionStyle.CAUTION)

    button.set_action_style(ActionStyle.TERTIARY)

    assert button.action_style is ActionStyle.TERTIARY
    assert THEME.error not in button.styleSheet()


def test_a_button_with_no_glyph_carries_no_icon(qapp):
    button = ActionButton("", "Cancel")

    assert button.icon().isNull()


def test_a_button_with_a_glyph_carries_one(qapp):
    button = ActionButton("plus", "New")

    assert not button.icon().isNull()


# ---------------------------------------------------------------------------
# fill — a row of these shares its width; a dialog's trailing pair must not
# ---------------------------------------------------------------------------


def test_a_filling_button_expands_to_share_its_row(qapp):
    from PyQt6.QtWidgets import QSizePolicy  # noqa: PLC0415

    button = ActionButton("plus", "New")

    assert button.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Expanding


def test_a_non_filling_button_leaves_the_stretch_alone(qapp):
    """A dialog's [Cancel] [Save] pair sits after a stretch, and an expanding button
    there eats it and spreads the pair across the whole footer."""
    from PyQt6.QtWidgets import QSizePolicy  # noqa: PLC0415

    button = ActionButton("checkmark", "Save", style=ActionStyle.PRIMARY, fill=False)

    assert button.sizePolicy().horizontalPolicy() is not QSizePolicy.Policy.Expanding
