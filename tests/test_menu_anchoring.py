"""Regression tests for issue #119 — popups must keep a top-level transient parent.

Embedding a game client makes its tile a native window, and Qt promotes the
tile's siblings (the sidebar, the canvas) to native windows of their own.  A
menu parented to a button inside one of those then hands Qt a non-top-level
``QWidgetWindow`` as transient parent, which Qt refuses with

    QWidgetWindow(0x..., name="LeftSidebarClassWindow") must be a top level window.

Real widget tests (QApplication required), in the style of
test_initial_state_warnings.  The first test pins the Qt behaviour the fix works
around, so a Qt upgrade that removes it shows up here rather than as a silently
pointless helper.
"""

import sys

import pytest
from PyQt6.QtCore import QPoint, qInstallMessageHandler
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.menu_utils import anchor_menu_to_window, popup_menu


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class _Harness:
    """A tab widget promoted to a native — but not top level — window.

    The same shape as the main window: a splitter holding the sidebar next to
    the widget the game client is embedded into.
    """

    def __init__(self):
        self.window = QMainWindow()
        splitter = QSplitter()
        self.tabs = QTabWidget()
        page = QWidget()
        layout = QVBoxLayout(page)
        self.button = QPushButton("menu")
        layout.addWidget(self.button)
        self.tabs.addTab(page, "Tab")
        self.tile = QWidget()
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.tile)
        self.window.setCentralWidget(splitter)
        self.tile.winId()  # what ClientTile.embed() does — promotes the sibling too


@pytest.fixture
def harness(qapp):
    h = _Harness()
    if h.tabs.windowHandle() is None:
        pytest.skip("Qt did not promote the sibling to a native window on this platform")
    return h


def _popup_warnings(menu: QMenu) -> list[str]:
    """Show and hide *menu*, returning whatever Qt warned about."""
    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _type, _ctx, text: messages.append(text))
    try:
        menu.popup(QPoint(0, 0))
        QApplication.processEvents()
        menu.close()
        QApplication.processEvents()
    finally:
        qInstallMessageHandler(previous)
    return messages


def test_menu_under_a_native_sibling_warns_without_the_fix(harness):
    menu = QMenu(harness.button)
    menu.addAction("action")

    warnings = _popup_warnings(menu)

    assert any("must be a top level window" in w for w in warnings), warnings


def test_anchored_menu_does_not_warn(harness):
    menu = anchor_menu_to_window(QMenu(harness.button))
    menu.addAction("action")

    warnings = _popup_warnings(menu)

    assert warnings == []


def test_anchored_menu_is_handed_back_to_its_owner(harness):
    menu = anchor_menu_to_window(QMenu(harness.button))
    menu.addAction("action")

    menu.popup(QPoint(0, 0))
    QApplication.processEvents()
    assert menu.parentWidget() is harness.window

    menu.close()
    QApplication.processEvents()
    assert menu.parentWidget() is harness.button


def test_anchored_menu_survives_a_second_popup(harness):
    """A menu installed with setMenu() is shown many times, not once."""
    menu = anchor_menu_to_window(QMenu(harness.button))
    menu.addAction("action")

    _popup_warnings(menu)
    warnings = _popup_warnings(menu)

    assert warnings == []
    assert menu.parentWidget() is harness.button


def test_popup_menu_is_owned_by_its_anchor(harness):
    menu = popup_menu(harness.button)
    menu.addAction("action")

    assert menu.parentWidget() is harness.button
    assert _popup_warnings(menu) == []


def test_a_parentless_menu_is_left_alone(qapp):
    menu = QMenu()

    assert anchor_menu_to_window(menu) is menu
    assert menu.parentWidget() is None
