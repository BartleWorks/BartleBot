"""The quick-launch placeholder and the game view's overlay button.

Between them they cover the two things a theme switch can break that are not a
stylesheet on a long-lived widget: a label rebuilt on every refresh, and an icon
whose tint was baked in at render time by svg_icon.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.game_embed.game_view import GameView, _GamePlaceholder
from bartlebot.gui.settings.canvas_tab import _hint
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.themed_label import ThemedTextLabel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _switch(qapp, widget, theme, mode: str) -> None:
    theme(mode)
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))


def _switch_app(qapp, theme, mode: str) -> None:
    """The app-wide broadcast, which is what reaches nested themed children."""
    theme(mode)
    qapp.setStyleSheet(THEME.global_stylesheet())


@pytest.fixture
def game_view(qapp):
    view = GameView()
    yield view
    view._focus_timer.stop()  # a 50 ms poll left running outlives the test
    view.deleteLater()


# ---------------------------------------------------------------------------
# _GamePlaceholder
# ---------------------------------------------------------------------------


def test_the_logo_and_link_recolour(qapp, theme):
    theme("light")
    ph = _GamePlaceholder()
    light_border, light_muted = THEME.border, THEME.text_muted

    _switch(qapp, ph, theme, "dark")

    assert light_border not in ph._logo.styleSheet()
    assert THEME.border in ph._logo.styleSheet()
    assert light_muted not in ph._manage_btn.styleSheet()


def test_the_column_headers_follow_the_theme(qapp, theme):
    """They are ThemedTextLabels, so the app-wide broadcast reaches them directly
    rather than the placeholder having to hold and restyle each one."""
    theme("light")
    ph = _GamePlaceholder()
    headers = ph.findChildren(ThemedTextLabel)
    assert len(headers) >= 3, "title plus the two column headers"
    before = [h.styleSheet() for h in headers]

    _switch_app(qapp, theme, "dark")

    assert [h.styleSheet() for h in headers] != before


def test_an_empty_list_hint_recolours_after_a_refresh(qapp, theme):
    """The hints are rebuilt by a *static* helper on every refresh, so there is
    nothing for the placeholder to hold a reference to."""
    theme("light")
    ph = _GamePlaceholder()
    ph.refresh()
    hints = ph.findChildren(ThemedTextLabel)
    before = [h.styleSheet() for h in hints]

    _switch_app(qapp, theme, "dark")

    assert [h.styleSheet() for h in hints] != before


# ---------------------------------------------------------------------------
# GameView's overlay button
# ---------------------------------------------------------------------------


def test_the_overlay_icon_is_re_tinted(qapp, theme, game_view):
    """svg_icon bakes THEME.icon_tint in at render time, so the icon has to be
    rebuilt rather than restyled."""
    theme("light")
    game_view._tiles[0] = object()  # pretend one client is embedded
    game_view._update_overlay()
    before = game_view._overlay_btn.icon().cacheKey()

    _switch(qapp, game_view, theme, "dark")

    assert game_view._overlay_btn.icon().cacheKey() != before


def test_the_restyle_keeps_the_state_the_icon_means(qapp, theme, game_view):
    """Two icons share the button, and which one belongs there is a function of
    whether the tiles are embedded or detached — a restyle must not pick the wrong
    one, which is why it defers to _update_overlay."""
    theme("light")
    game_view._detached_tiles[0] = object()
    game_view._update_overlay()

    _switch(qapp, game_view, theme, "dark")

    assert game_view._overlay_btn.toolTip() == "Re-attach all windows"
    # isHidden, not isVisible: the view is never shown in a test, so isVisible is
    # False for every child regardless and would assert nothing.
    assert not game_view._overlay_btn.isHidden()


def test_with_nothing_embedded_the_button_stays_hidden(qapp, theme, game_view):
    theme("light")

    _switch(qapp, game_view, theme, "dark")

    assert game_view._overlay_btn.isHidden()


# ---------------------------------------------------------------------------
# The settings-tab hint factory
# ---------------------------------------------------------------------------


def test_the_canvas_tab_hint_follows_the_theme(qapp, theme):
    theme("light")
    lbl = _hint("Some explanation")
    light = THEME.text_muted

    _switch(qapp, lbl, theme, "dark")

    assert light not in lbl.styleSheet()
    assert "padding-bottom: 4px" in lbl.styleSheet()
