"""The title bar's frame: which pixels drag, which click, which resize, and what Windows is told.

The rules are ``title_bar_hit``, tested pure. The rest asks a real window the question
Windows asks -- ``WM_NCHITTEST``, sent to its handle -- so it covers the wiring as well: the
window's ``nativeEvent`` answering before Qt's own hit test, which cannot open a menu in the
bar (see the module docstring).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

import pytest
from bartleby.qt.title_bar.native_frame import (
    HTCAPTION,
    HTCLIENT,
    HTTOP,
    WM_NCHITTEST,
    is_native_frame_supported,
)
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QMenuBar

# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A real window, asked what Windows asks
# ---------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32") if hasattr(ctypes, "WinDLL") else None
if _user32 is not None:
    _user32.SendMessageW.restype = ctypes.c_ssize_t
    _user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

_GWL_STYLE = -16
_CAPTION_STYLES = 0x00C00000 | 0x00080000 | 0x00020000 | 0x00010000


@pytest.fixture
def window(qapp, qtbot):
    if not is_native_frame_supported():
        pytest.skip("the native frame is Windows-only")
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow()  # shows itself, maximized under the default settings
    qtbot.waitUntil(lambda: window.windowHandle() is not None and window.windowHandle().isExposed())
    return window


def _ask_windows(window, widget, pos: QPoint) -> int:
    """What *window* answers when Windows hit-tests *pos* in *widget*'s coordinates."""
    local = widget.mapTo(window, pos)
    dpr = window.devicePixelRatio()
    point = wt.POINT(round(local.x() * dpr), round(local.y() * dpr))
    hwnd = int(window.winId())
    _user32.ClientToScreen(hwnd, ctypes.byref(point))
    lparam = (point.y & 0xFFFF) << 16 | (point.x & 0xFFFF)
    return _user32.SendMessageW(hwnd, WM_NCHITTEST, 0, lparam)


def test_windows_is_told_what_each_part_of_the_window_is(window):
    """One window for every point: building one costs about three seconds."""
    bar = window.title_bar
    menu_bar = bar.findChild(QMenuBar)
    file_menu = menu_bar.actionGeometry(menu_bar.actions()[0]).center()

    assert _ask_windows(window, menu_bar, file_menu) == HTCLIENT, "the File menu"
    for button in [*bar.toggles, *bar.caption_buttons]:
        assert _ask_windows(window, button, button.rect().center()) == HTCLIENT, button.toolTip()
    assert _ask_windows(window, bar, QPoint(bar.width() // 2, bar.height() // 2)) == HTCAPTION, "the title"
    canvas = window.bot_canvas
    assert _ask_windows(window, canvas, canvas.rect().center()) == HTCLIENT, "the bot canvas"


def test_a_restored_window_resizes_from_its_top_edge(window, qtbot):
    window.showNormal()
    qtbot.waitUntil(lambda: not window.isMaximized())
    bar = window.title_bar

    assert _ask_windows(window, bar, QPoint(bar.width() // 2, 0)) == HTTOP


def test_the_window_always_has_one_bottom_edge(window, qtbot):
    """#271. Maximized, Windows draws no border, so the rule shows. Restored, Windows draws
    the border in our color, so the rule goes, except where there is no border color to set
    (before Windows 11, build 22000, which includes CI's Server 2022)."""
    window.showMaximized()
    qtbot.waitUntil(window.isMaximized)

    assert window.bottom_edge.isVisible()

    window.showNormal()
    qtbot.waitUntil(lambda: not window.isMaximized())

    assert window.bottom_edge.isVisible() == (sys.getwindowsversion().build < 22000)


def test_the_window_is_left_a_whole_window(window):
    """The caption styles are back, and Qt's caption overlay is gone.

    Without the styles the taskbar button stops minimizing the window, and Win+Arrow stops.
    """
    user32 = ctypes.windll.user32
    hwnd = int(window.winId())
    assert user32.GetWindowLongW(hwnd, _GWL_STYLE) & _CAPTION_STYLES == _CAPTION_STYLES

    overlays = []
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def visit(child, _lparam):
        name = ctypes.create_unicode_buffer(32)
        user32.GetClassNameW(child, name, 32)
        if name.value == "_q_titlebar" and user32.IsWindowVisible(child):
            overlays.append(child)
        return True

    user32.EnumChildWindows(hwnd, callback_type(visit), 0)
    assert overlays == []
