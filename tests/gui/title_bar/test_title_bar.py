"""The title bar widget: which of its pixels are controls, and its toggles' binding to actions."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QLabel, QMenuBar

from bartlebot.game.game_sizing import TITLE_BAR_PX
from bartlebot.gui.title_bar.title_bar import TitleBar


def _bar() -> tuple[TitleBar, QMenuBar]:
    menu_bar = QMenuBar()
    menu_bar.addMenu("&File").addAction("Quit")
    return TitleBar(menu_bar), menu_bar


def _pane_action(bar: TitleBar, text: str = "Left Sidebar", shortcut: str = "") -> QAction:
    action = QAction(text, bar)
    action.setCheckable(True)
    action.setChecked(True)
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
    return action


@pytest.fixture
def shown_bar(qapp, qtbot):
    bar, menu_bar = _bar()
    bar.add_toggle(_pane_action(bar), shown_icon="panel_left_shown", hidden_icon="panel_left_hidden")
    bar.set_title("BartleBot — farm.json")
    qtbot.addWidget(bar)
    bar.resize(1000, TITLE_BAR_PX)
    bar.show()
    qtbot.waitExposed(bar)
    return bar, menu_bar


def test_building_the_bar_never_shows_a_parentless_widget(qapp, parentless_shows):
    bar, _menu_bar = _bar()
    bar.add_toggle(_pane_action(bar), shown_icon="panel_left_shown", hidden_icon="panel_left_hidden")

    assert parentless_shows == []


def test_the_bar_is_the_height_the_window_floor_counts(qapp):
    bar, _menu_bar = _bar()

    assert bar.sizeHint().height() == TITLE_BAR_PX
    assert bar.minimumHeight() == bar.maximumHeight() == TITLE_BAR_PX


# ---------------------------------------------------------------------------
# What drags and what clicks
# ---------------------------------------------------------------------------


def test_the_menu_bar_is_a_control(shown_bar):
    bar, menu_bar = shown_bar
    file_menu = menu_bar.actionGeometry(menu_bar.actions()[0]).center()

    assert bar.is_control_at(menu_bar.mapTo(bar, file_menu))


def test_every_button_is_a_control(shown_bar):
    bar, _menu_bar = shown_bar
    for button in [*bar.toggles, *bar.caption_buttons]:
        assert bar.is_control_at(button.geometry().center()), button.toolTip()


def test_the_title_and_the_logo_are_caption(shown_bar):
    bar, _menu_bar = shown_bar
    title = next(label for label in bar.findChildren(QLabel) if label.text() == bar.title)

    assert not bar.is_control_at(title.geometry().center())
    assert not bar.is_control_at(QPoint(15, TITLE_BAR_PX // 2))  # the logo


# ---------------------------------------------------------------------------
# Toggles follow their actions
# ---------------------------------------------------------------------------


def test_a_toggle_starts_as_its_action_is(qapp):
    bar, _menu_bar = _bar()
    action = _pane_action(bar)
    action.setChecked(False)

    toggle = bar.add_toggle(action, shown_icon="panel_left_shown", hidden_icon="panel_left_hidden")

    assert not toggle.isChecked()


def test_a_change_made_elsewhere_reaches_the_toggle(qapp):
    """The View menu, a shortcut, or Maximize Game View hiding the panes."""
    bar, _menu_bar = _bar()
    action = _pane_action(bar)
    toggle = bar.add_toggle(action, shown_icon="panel_left_shown", hidden_icon="panel_left_hidden")

    action.setChecked(False)

    assert not toggle.isChecked()


def test_clicking_a_toggle_triggers_its_action(qapp, qtbot):
    bar, _menu_bar = _bar()
    action = _pane_action(bar)
    asked: list[bool] = []
    action.triggered.connect(asked.append)
    toggle = bar.add_toggle(action, shown_icon="panel_left_shown", hidden_icon="panel_left_hidden")

    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)

    assert asked == [False]
    assert not toggle.isChecked()


def test_a_toggle_whose_action_refuses_shows_the_action_s_state(qapp, qtbot):
    bar, _menu_bar = _bar()
    action = _pane_action(bar)
    action.triggered.connect(lambda _checked: action.setChecked(True))
    toggle = bar.add_toggle(action, shown_icon="panel_left_shown", hidden_icon="panel_left_hidden")

    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)

    assert toggle.isChecked()


def test_a_toggle_s_tooltip_names_its_pane_and_shortcut(qapp):
    bar, _menu_bar = _bar()
    action = _pane_action(bar, "Game View", "Ctrl+G")

    toggle = bar.add_toggle(action, shown_icon="panel_top_shown", hidden_icon="panel_top_hidden")

    assert toggle.toolTip() == "Game View (Ctrl+G)"


# ---------------------------------------------------------------------------
# Window buttons and title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "signal"), [(0, "minimize_requested"), (1, "maximize_requested"), (2, "close_requested")]
)
def test_each_window_button_asks_for_its_verb(qapp, qtbot, index, signal):
    bar, _menu_bar = _bar()

    with qtbot.waitSignal(getattr(bar, signal), timeout=1000):
        qtbot.mouseClick(bar.caption_buttons[index], Qt.MouseButton.LeftButton)


def test_the_maximize_button_offers_restore_while_maximized(qapp):
    bar, _menu_bar = _bar()
    maximize = bar.caption_buttons[1]

    bar.set_maximized(True)
    assert maximize.toolTip() == "Restore Down"

    bar.set_maximized(False)
    assert maximize.toolTip() == "Maximize"


def test_the_title_shows_what_it_was_given(qapp):
    bar, _menu_bar = _bar()

    bar.set_title("BartleBot — farm.json")

    assert bar.title == "BartleBot — farm.json"
