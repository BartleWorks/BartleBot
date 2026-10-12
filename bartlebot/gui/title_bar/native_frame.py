"""The window frame under the title bar: what Qt does, and the four things it leaves to us.

The title bar sits where Windows would draw its caption, so the window has to give that
strip to us without giving up what makes it a window: the shadow, the rounded corners,
the resize borders, Aero Snap, Win+Arrow, the minimize and maximize animations, and the
system menu. Qt 6.9 does most of that. ``Qt.WindowType.ExpandedClientAreaHint`` keeps the
native frame and moves the client area up over the caption, and
``NoTitleBarBackgroundHint`` stops Qt painting a caption background over our widgets.

What it leaves is handled here, each piece found the hard way in a spike:

**Which pixels drag.** Windows asks with ``WM_NCHITTEST``, and the answer decides
everything: ``HTCAPTION`` gets a native drag, double-click to maximize, snapping and the
system menu; ``HTCLIENT`` gets an ordinary click. Qt's own answer for the expanded area
fakes a mouse press from inside the hit test (polling ``GetAsyncKeyState``) and calls the
point a caption when no widget accepted it. That is unusable with a menu bar in the strip:
File never opened and double-click did nothing. So the window answers every hit test
itself -- ``title_bar_hit`` for the bar, Windows' own ``DefWindowProc`` everywhere else --
from where the point is, which is what Windows expects. A window's ``nativeEvent`` runs
before Qt's platform handling, so this answer is the one used.

**The system menu.** Right-clicking a caption shows it through ``DefWindowProc``, which
Qt's message handling never reaches. :meth:`NativeFrame._show_system_menu` shows it.

**The window styles.** Leaving out ``WindowTitleHint`` and the button hints is what stops
Qt drawing its own icon, title and buttons over ours (it switches the title back on
whenever any button hint is set). But Qt also drops ``WS_MINIMIZEBOX``, ``WS_MAXIMIZEBOX``,
``WS_SYSMENU`` and ``WS_CAPTION`` with them, which takes click-the-taskbar-to-minimize,
Win+Arrow and the animations away. :meth:`NativeFrame.restore_styles` puts them back;
Windows draws no caption for them, because Qt still owns ``WM_NCCALCSIZE``.

**Qt's overlay.** The flag also makes Qt create a ``_q_titlebar`` layered child window over
the top ~30px, to draw its own caption into, and redraw it at the full width of the window
on every hit test it handles. With no caption of its own to draw it shows nothing; it only
costs a full-width paint and is a native child window sitting over ours, which is how #126
took every click on the canvas. :meth:`NativeFrame.hide_overlays` hides it.

All of it is Windows-only and switched on only on the ``windows`` platform plugin, so a
test run on another one builds the same window with Qt's ordinary frame.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QMainWindow

logger = logging.getLogger(__name__)

WM_NCHITTEST = 0x0084
WM_NCRBUTTONUP = 0x00A5
WM_SYSCOMMAND = 0x0112

HTCLIENT = 1
HTCAPTION = 2
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14

#: How far down from the window's top edge a press resizes rather than drags, in physical
#: pixels. Windows' own band (``SM_CYSIZEFRAME`` + ``SM_CXPADDEDBORDER``) is about 9px at
#: 125%, a quarter of the bar, and a pointer crossing it drops the hover off a button it is
#: still over. Four is still easy to grab and costs the buttons almost nothing.
TOP_RESIZE_BAND_PX = 4

_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_RESTORED_STYLES = _WS_CAPTION | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX
_SWP_FRAMECHANGED_ONLY = 0x0027  # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED
_SW_HIDE = 0

_SC_SIZE = 0xF000
_SC_MOVE = 0xF010
_SC_MINIMIZE = 0xF020
_SC_MAXIMIZE = 0xF030
_SC_CLOSE = 0xF060
_SC_RESTORE = 0xF120
_MF_ENABLED = 0x0
_MF_GRAYED = 0x1
_TPM_RIGHTBUTTON = 0x2
_TPM_RETURNCMD = 0x100

_OVERLAY_CLASS = "_q_titlebar"

FRAME_FLAGS = (
    Qt.WindowType.Window
    | Qt.WindowType.ExpandedClientAreaHint
    | Qt.WindowType.NoTitleBarBackgroundHint
    | Qt.WindowType.CustomizeWindowHint
)


def title_bar_hit(
    pos: tuple[float, float],
    *,
    bar_height: int,
    width: int,
    top_band: float,
    maximized: bool,
    over_control: bool,
) -> int | None:
    """What Windows should take the point *pos* to be, or ``None`` when it is not in the bar.

    *pos* is in the window's client coordinates, logical pixels, and *top_band* is the
    resize band in the same units. *over_control* says whether a button or the menu bar
    is under the point: those take the click, and everything else in the bar -- the logo,
    the title, the space between -- is caption, so it drags.

    A maximized window has no resize band: there is nothing above it to drag to, and
    the top of the screen is where a pointer flung at the close button lands.
    """
    x, y = pos
    if not (0 <= y < bar_height and 0 <= x < width):
        return None
    if not maximized and y < top_band:
        if x < top_band:
            return HTTOPLEFT
        if x >= width - top_band:
            return HTTOPRIGHT
        return HTTOP
    return HTCLIENT if over_control else HTCAPTION


def _lparam_point(lparam: int) -> tuple[int, int]:
    """The signed screen point packed into a mouse message's ``lParam``."""
    return ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value


def is_native_frame_supported() -> bool:
    """Whether the window is a real Win32 window this module can manage."""
    return sys.platform == "win32" and QGuiApplication.platformName() == "windows"


class NativeFrame:
    """The frame side of the title bar for *window*: flags, hit testing and the system menu.

    *bar* is asked two things: its height, and whether a control is under a point
    (``is_control_at``). Construct it before the window is first shown, since the flags
    decide how the native window is created.
    """

    def __init__(self, window: QMainWindow, bar) -> None:
        self._window = window
        self._bar = bar
        self._active = is_native_frame_supported()
        if not self._active:
            return
        # A handle of our own, so the signatures set here are not set for every other
        # caller of ``ctypes.windll.user32`` in the process.
        self._user32 = ctypes.WinDLL("user32")
        self._user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self._user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        self._user32.TrackPopupMenu.restype = ctypes.c_int
        window.setWindowFlags(FRAME_FLAGS)
        window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)

    @property
    def active(self) -> bool:
        """Whether the native frame is in use (Windows, on the ``windows`` platform plugin)."""
        return self._active

    # -- messages -----------------------------------------------------------------

    def native_event(self, message) -> tuple[bool, int]:
        """The window's ``nativeEvent``: ``(handled, result)``.

        Never forwards to ``super().nativeEvent`` -- calling the base implementation
        from a Python override crashes PyQt6 (see ``ClientTile.nativeEvent``), and it
        only ever answers "not handled" anyway.
        """
        if not self._active:
            return False, 0
        msg = wt.MSG.from_address(int(message))
        if msg.message == WM_NCHITTEST:
            return True, self._hit_test(msg.hWnd, msg.wParam, msg.lParam)
        if msg.message == WM_NCRBUTTONUP and msg.wParam == HTCAPTION:
            self._show_system_menu(msg.lParam)
            return True, 0
        return False, 0

    def _hit_test(self, hwnd, wparam: int, lparam: int) -> int:
        x, y = _lparam_point(lparam)
        pt = wt.POINT(x, y)
        self._user32.ScreenToClient(int(self._window.winId()), ctypes.byref(pt))
        dpr = self._window.devicePixelRatio() or 1.0
        pos = (pt.x / dpr, pt.y / dpr)
        local = QPoint(int(pos[0]), int(pos[1]))
        hit = title_bar_hit(
            pos,
            bar_height=self._bar.height(),
            width=self._window.width(),
            top_band=TOP_RESIZE_BAND_PX / dpr,
            maximized=self._window.isMaximized(),
            over_control=self._bar.is_control_at(self._bar.mapFrom(self._window, local)),
        )
        if hit is not None:
            return hit
        # Below the bar: Windows' own answer, which is the side and bottom resize borders
        # outside the client area and HTCLIENT inside it.
        return self._user32.DefWindowProcW(hwnd, WM_NCHITTEST, wparam, lparam)

    def _show_system_menu(self, lparam: int) -> None:
        """What ``DefWindowProc`` does on a caption right-click: grey what the window's state
        rules out, track the menu, and run the pick."""
        hwnd = int(self._window.winId())
        menu = self._user32.GetSystemMenu(hwnd, False)
        if not menu:
            return
        maximized = self._window.isMaximized()
        for command, enabled in (
            (_SC_RESTORE, maximized),
            (_SC_MOVE, not maximized),
            (_SC_SIZE, not maximized),
            (_SC_MINIMIZE, True),
            (_SC_MAXIMIZE, not maximized),
            (_SC_CLOSE, True),
        ):
            self._user32.EnableMenuItem(menu, command, _MF_ENABLED if enabled else _MF_GRAYED)
        self._user32.SetMenuDefaultItem(menu, _SC_CLOSE, False)
        x, y = _lparam_point(lparam)
        command = self._user32.TrackPopupMenu(menu, _TPM_RIGHTBUTTON | _TPM_RETURNCMD, x, y, 0, hwnd, None)
        if command:
            self._user32.PostMessageW(hwnd, WM_SYSCOMMAND, command, 0)

    # -- window state ---------------------------------------------------------------

    def on_shown(self) -> None:
        """Re-assert the styles and hide Qt's overlay. Cheap, idempotent, call on every show."""
        if not self._active:
            return
        self.restore_styles()
        self.hide_overlays()

    def restore_styles(self) -> None:
        """Put back the caption styles that leaving out the title and button hints drops."""
        hwnd = int(self._window.winId())
        style = self._user32.GetWindowLongW(hwnd, _GWL_STYLE)
        if style & _RESTORED_STYLES == _RESTORED_STYLES:
            return
        self._user32.SetWindowLongW(hwnd, _GWL_STYLE, style | _RESTORED_STYLES)
        self._user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_FRAMECHANGED_ONLY)

    def hide_overlays(self) -> None:
        """Hide the ``_q_titlebar`` child window Qt draws its own caption into."""
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def visit(child, _lparam):
            name = ctypes.create_unicode_buffer(32)
            self._user32.GetClassNameW(child, name, 32)
            if name.value == _OVERLAY_CLASS:
                found.append(child)
            return True

        self._user32.EnumChildWindows(int(self._window.winId()), callback_type(visit), 0)
        for child in found:
            # Unconditionally, not "if IsWindowVisible": a show event arrives before Windows
            # shows the top-level window, and IsWindowVisible is false for a child of a
            # hidden parent -- so the overlay Qt showed at creation read as already hidden
            # and appeared with the window.
            self._user32.ShowWindow(child, _SW_HIDE)
