"""Tests for the app-wide "this control is disabled" signal.

Disabled state used to be carried entirely by ``QPalette.Disabled.ButtonText``
set to ``text_muted`` — the same color as *enabled* secondary text — on an
otherwise unchanged button, and by icons that Qt could not meaningfully gray out
because they had already been flattened to a single tint color.  The result was
readable either way, so "disabled" was indistinguishable from "enabled".
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QIcon, QPalette

from bartlebot.gui.theming.icon_utils import svg_icon, svg_pixmap
from bartlebot.gui.theming.theme import DARK, LIGHT


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_text_differs_from_enabled_secondary_text(qapp, theme):
    """The whole bug: disabled text was literally text_muted, so it read as content."""
    assert theme.text_disabled.lower() != theme.text_muted.lower()
    assert theme.text_disabled.lower() != theme.text.lower()


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_fill_differs_from_the_enabled_button_fill(qapp, theme):
    """Text alone is too weak a signal — the fill has to move as well."""
    assert theme.surface_disabled.lower() != theme.surface.lower()


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_palette_disabled_group_uses_the_disabled_tokens(qapp, theme):
    pal = theme.build_palette()
    disabled = QPalette.ColorGroup.Disabled

    assert pal.color(disabled, QPalette.ColorRole.ButtonText).name().lower() == theme.text_disabled.lower()
    assert pal.color(disabled, QPalette.ColorRole.Button).name().lower() == theme.surface_disabled.lower()


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_button_differs_from_enabled_button_in_both_roles(qapp, theme):
    """Guards the regression directly: both fill and text must change."""
    pal = theme.build_palette()
    active, disabled = QPalette.ColorGroup.Active, QPalette.ColorGroup.Disabled

    assert pal.color(active, QPalette.ColorRole.Button) != pal.color(disabled, QPalette.ColorRole.Button)
    assert pal.color(active, QPalette.ColorRole.ButtonText) != pal.color(disabled, QPalette.ColorRole.ButtonText)


def test_svg_icon_carries_an_explicit_disabled_pixmap(qapp):
    """Qt's generated disabled variant is a near no-op on a flat single-color icon."""
    icon = svg_icon("more_vert", 16)

    normal = icon.pixmap(16, 16, QIcon.Mode.Normal).toImage()
    disabled = icon.pixmap(16, 16, QIcon.Mode.Disabled).toImage()

    assert normal != disabled


def test_disabled_icon_is_actually_more_transparent(qapp):
    """Not just "different" — visibly faded."""
    icon = svg_icon("more_vert", 16)
    normal = icon.pixmap(16, 16, QIcon.Mode.Normal).toImage()
    disabled = icon.pixmap(16, 16, QIcon.Mode.Disabled).toImage()

    def total_alpha(img) -> int:
        return sum(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height()))

    assert total_alpha(disabled) < total_alpha(normal)


def test_svg_pixmap_opacity_defaults_to_opaque(qapp):
    """The fade is opt-in — existing svg_pixmap callers must be unaffected."""
    plain = svg_pixmap("more_vert", 16, tint="#ffffff").toImage()
    explicit = svg_pixmap("more_vert", 16, tint="#ffffff", opacity=1.0).toImage()

    assert plain == explicit
