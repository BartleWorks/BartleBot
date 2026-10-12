"""Tests for bartlebot.gui.theming.theme: reading a saved custom theme across a token rename."""

from __future__ import annotations

from bartlebot.gui.theming.theme import LIGHT, RENAMED_TOKENS, build_custom_theme, saved_color


def test_a_color_saved_under_a_renamed_tokens_old_name_is_still_read():
    """#204 renamed ``pill_profile_bg``; a custom theme saved before keeps its color."""
    theme = build_custom_theme({"pill_profile_bg": "#123456"}, base=LIGHT)

    assert theme.pill_this_bot_bg == "#123456"


def test_the_new_name_wins_over_the_old():
    theme = build_custom_theme({"pill_profile_bg": "#123456", "pill_this_bot_bg": "#abcdef"}, base=LIGHT)

    assert theme.pill_this_bot_bg == "#abcdef"


def test_with_neither_name_saved_the_base_answers():
    assert saved_color({}, "pill_this_bot_bg", LIGHT.pill_this_bot_bg) == LIGHT.pill_this_bot_bg
    assert saved_color({"accent": "#000000"}, "accent", "#ffffff") == "#000000"


def test_every_renamed_token_is_a_real_one():
    assert all(hasattr(LIGHT, new) and not hasattr(LIGHT, old) for new, old in RENAMED_TOKENS.items())
