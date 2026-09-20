"""Tests for ``LinkButton`` (issue #88).

A link is the weight *below* ``ActionStyle.TERTIARY``: a control that sits inline in
prose or under a card and must not claim button weight at all. So what is tested is
mostly what it does not draw — no frame, no fill — plus the underline that only
appears once the pointer is on it.
"""

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QSizePolicy

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.theme import DARK, LIGHT, THEME
from bartlebot.gui.widgets.link_button import LinkButton


@pytest.fixture
def theme():
    """Pin the active theme and restore it, as tests/test_themed_mixin.py does."""
    before = theme_mod.active_theme()
    yield theme_mod.set_theme_instance
    theme_mod.set_theme_instance(before)


def test_it_draws_no_border_and_no_fill(qapp):
    """The distinguishing behaviour: anything framed or filled is a button, not a link."""
    style = LinkButton("Game settings").styleSheet()

    assert "border: none;" in style
    assert "background: transparent;" in style


def test_it_is_accent_coloured(qapp):
    """The accent alone is what says "clickable" before the pointer arrives."""
    assert f"color: {THEME.accent};" in LinkButton("Game settings").styleSheet()


def test_the_underline_is_on_hover_only(qapp):
    """Three of these sit in prose or under a card; a permanent underline there is one
    more rule on a surface already full of them."""
    style = LinkButton("Change resolution").styleSheet()
    base, _, hover = style.partition("QPushButton:hover")

    assert "text-decoration: underline;" not in base
    assert "text-decoration: underline;" in hover


def test_it_does_not_expand(qapp):
    """Sized to its own text: a link stretched across a column reads as a button."""
    policy = LinkButton("Game settings").sizePolicy()

    assert policy.horizontalPolicy() is not QSizePolicy.Policy.Expanding
    assert policy.verticalPolicy() is not QSizePolicy.Policy.Expanding


def test_it_takes_a_font_size_token(qapp):
    """Sized by token, never by literal — see tests/test_font_sizes.py."""
    assert f"font-size: {THEME.font_subtext_px}px;" in LinkButton("See also").styleSheet()
    assert f"font-size: {THEME.font_body_px}px;" in LinkButton("See also", font_size=THEME.font_body_px).styleSheet()


def test_it_recolours_on_a_theme_switch(qapp, theme):
    theme(LIGHT)
    button = LinkButton("Game settings")
    light = button.styleSheet()

    theme(DARK)
    button.refresh_theme_styles()

    assert button.styleSheet() != light
    assert THEME.accent in button.styleSheet()


def test_the_colour_follows_the_theme_accent(qapp, theme):
    """Derived, never stored: a custom theme moves the link with it."""
    theme(replace(LIGHT, accent="#aa00aa"))
    button = LinkButton("Game settings")

    assert "color: #aa00aa;" in button.styleSheet()
