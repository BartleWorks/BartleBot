"""Regression tests: popups must keep a top-level transient parent.

Embedding a game client makes its tile a native window, and Qt promotes the
tile's siblings (the sidebar, the canvas) to native windows of their own.  A
menu parented to a button inside one of those then hands Qt a non-top-level
``QWidgetWindow`` as transient parent, which Qt refuses with

    QWidgetWindow(0x..., name="LeftSidebarClassWindow") must be a top level window.

Real widget tests (QApplication required), in the style of
test_initial_state_warnings.  The first test pins the Qt behavior the fix works
around, so a Qt upgrade that removes it shows up here rather than as a silently
pointless helper.
"""

import time
from collections.abc import Callable

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


_PUMP_TIMEOUT = 2.0
"""Ceiling on a pump, not a cadence — only a failing test ever waits this long."""

_PUMP_INTERVAL = 0.005


def _pump_until(predicate: Callable[[], bool], timeout: float = _PUMP_TIMEOUT) -> bool:
    """Spin the event loop until *predicate* holds, or *timeout* elapses.

    Use this only where the state under test genuinely arrives on a later spin —
    tearing a popup down does, putting one up does not.  Pumping for a state
    that is already true is not free here: see the comment in
    ``test_anchored_menu_is_handed_back_to_its_owner``.
    """
    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_PUMP_INTERVAL)


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
        _pump_until(lambda: not menu.isVisible())
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

    # Assert the swap without pumping first.  ``aboutToShow`` is emitted from
    # inside ``popup()``, so the parent is already the window here — whereas
    # spinning the loop can *lose* it: nothing in this harness is shown or
    # activated, so under load the popup is deactivated and closes itself, and
    # ``aboutToHide`` hands it straight back to the button.  That is the menu-ownership flake.
    menu.popup(QPoint(0, 0))
    assert menu.parentWidget() is harness.window

    menu.close()
    _pump_until(lambda: menu.parentWidget() is harness.button)
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
