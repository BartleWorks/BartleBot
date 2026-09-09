"""Tests for ``ActionButton``'s four weights (issue #47).

What matters is not which hex each style lands on but that the four are *told apart*, and
that the default is the quiet one. Most rows in this app are equal-weight verbs beside a
destructive one, and a style that had to be opted out of would make every such row shout.
"""

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle


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


def test_secondary_is_outlined_in_the_accent_and_unfilled(qapp):
    style = ActionButton("revert", "Use default", style=ActionStyle.SECONDARY).styleSheet()

    assert "background: transparent;" in style
    assert f"border: 1px solid {THEME.accent};" in style


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
