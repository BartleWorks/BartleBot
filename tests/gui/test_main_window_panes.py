"""The main window's four panes, the View menu that shows and hides them, and the title bar's
toggles bound to that menu.

A ``MainWindow`` costs about three seconds to build, so each test checks everything one
window can show rather than one assertion apiece.
"""

from __future__ import annotations

import pytest

from bartlebot.gui.main_window import MainWindow


@pytest.fixture
def window(qapp):
    return MainWindow()


def _panes(window: MainWindow):
    """Each pane toggle with the pane it shows, in the title bar's order."""
    return [
        (window.action_toggle_left_sidebar, window.left_sidebar),
        (window.action_toggle_game_view, window.game_view_panel),
        (window.action_toggle_right_sidebar, window.right_sidebar),
        (window.action_toggle_bot_canvas, window.bot_canvas),
    ]


def _checked(window: MainWindow) -> list[bool]:
    return [action.isChecked() for action, _pane in _panes(window)]


def test_the_title_bar_holds_the_menus_a_toggle_per_pane_and_the_title(window):
    assert window.menuWidget() is window.title_bar
    assert [toggle.toolTip() for toggle in window.title_bar.toggles] == [
        "Left Sidebar",
        "Game View (Ctrl+G)",
        "Right Sidebar",
        "Bot Canvas",
    ]
    for action, pane in _panes(window):
        assert action.isChecked() and not pane.isHidden(), action.text()
    assert all(toggle.isChecked() for toggle in window.title_bar.toggles)

    window.set_bot_name("Farm", "farm.json")
    assert window.title_bar.title == "BartleBot — farm.json"
    # #267: spelled out, since the title bar would print Qt's [*] placeholder as it is.
    window.set_unsaved(True)
    assert window.title_bar.title == "BartleBot — *farm.json"
    window.set_bot_name("Farm", "farm-2.json")
    assert window.title_bar.title == "BartleBot — *farm-2.json", "a Save As keeps the mark until the flag drops"
    window.set_unsaved(False)
    assert window.title_bar.title == "BartleBot — farm-2.json"


def test_each_toggle_hides_and_shows_its_pane(window):
    for index, (action, pane) in enumerate(_panes(window)):
        toggle = window.title_bar.toggles[index]

        toggle.click()
        assert pane.isHidden() and not action.isChecked() and not toggle.isChecked(), action.text()

        toggle.click()
        assert not pane.isHidden() and action.isChecked() and toggle.isChecked(), action.text()


def test_hiding_the_game_view_from_elsewhere_unchecks_its_toggle(window):
    """The game view can hide itself (``GameView`` calls ``show_game_view``)."""
    window.show_game_view(False)

    assert not window.action_toggle_game_view.isChecked()
    assert not window.title_bar.toggles[1].isChecked()


def test_maximizing_the_game_view_unchecks_what_it_hides_and_restores_it(window):
    window.action_maximize_game_view.trigger()
    assert _checked(window) == [False, True, False, False]

    window.action_maximize_game_view.trigger()
    assert _checked(window) == [True, True, True, True]


def test_a_canvas_hidden_before_maximizing_stays_hidden_after(window):
    window.action_toggle_bot_canvas.trigger()

    window.action_maximize_game_view.trigger()
    window.action_maximize_game_view.trigger()

    assert window.bot_canvas.isHidden()
    assert _checked(window) == [True, True, True, False]
