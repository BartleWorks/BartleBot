"""Win32 mechanics for embedding a foreign top-level window inside our own.

Kept free of Qt so the embed/restore sequence is unit-testable: ``EmbeddedWindow``
takes an injectable ``user32``, and every decision that does not need the OS is a
module-level pure function.

The restore path is the delicate half. A window that has been reparented into
another process's widget is not a top-level window any more, and putting it back
means undoing every piece of that in the right order — style before parent,
frame flush after both — or the caller is left with a window that has a title bar
drawn but cannot be dragged.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_POPUP = 0x80000000
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_DLGFRAME = 0x00400000
WS_BORDER = 0x00800000
WS_CAPTION = WS_BORDER | WS_DLGFRAME

WS_EX_APPWINDOW = 0x00040000

# Everything that draws a frame or offers a way to move/resize the window. All of
# it has to go while the window is a child, and all of it has to come back after.
FRAME_STYLES = WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX  # 0x00CF0000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020  # Required after SetWindowLongW to flush a style change

SW_SHOW = 5

WM_PARENTNOTIFY = 0x0210
# The button-down events WM_PARENTNOTIFY reports in its wParam's low word.
_BUTTON_DOWN_EVENTS = frozenset({0x0201, 0x0204, 0x0207, 0x020B})  # L, R, M, X

_STYLE_MASK = 0xFFFFFFFF


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hwndActive", ctypes.wintypes.HWND),
        ("hwndFocus", ctypes.wintypes.HWND),
        ("hwndCapture", ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize", ctypes.wintypes.HWND),
        ("hwndCaret", ctypes.wintypes.HWND),
        ("rcCaret", ctypes.wintypes.RECT),
    ]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def child_style(orig_style: int) -> int:
    """The style to give *orig_style*'s window while it is embedded as a child.

    WS_POPUP has to go as well as the frame styles: WS_POPUP | WS_CHILD is an
    invalid combination, and a game window that happens to carry WS_POPUP would
    otherwise end up in it.
    """
    return ((orig_style & ~FRAME_STYLES & ~WS_POPUP) | WS_CHILD | WS_VISIBLE) & _STYLE_MASK


def restored_style(orig_style: int) -> int:
    """The style to give the window when it is popped back out to the desktop.

    The frame styles are forced on rather than taken from *orig_style*. If the
    saved style was captured while the window was already embedded — which is what
    a re-embed after a failed restore looks like — trusting it would hand back a
    window with no caption and no way to move it. Forcing costs nothing for a
    window that was ordinary to begin with, since it already had these bits.
    """
    return ((orig_style & ~WS_CHILD) | FRAME_STYLES | WS_VISIBLE) & _STYLE_MASK


def restored_parent(orig_parent: int, orig_style: int) -> int:
    """The HWND to reparent back to, given what ``GetParent`` reported before embedding.

    ``GetParent`` returns the *owner* for a top-level window, not a parent. Passing
    that owner back to ``SetParent`` would make the window a genuine child of it —
    clipped to the owner and undraggable. Only honor the saved handle when the
    window really was a child; otherwise return 0, which means the desktop.
    """
    return orig_parent if orig_style & WS_CHILD else 0


def is_click_notification(message: int, wparam: int) -> bool:
    """Whether a message the tile received says a mouse button went down on the game.

    Windows sends ``WM_PARENTNOTIFY`` to a child's parent for every button press on
    the child, and only for presses that actually reach it, so a click on a window
    stacked over the tile never produces one. It also arrives before the game
    handles the press, which is the property the focus grant depends on. When the
    click also brings BartleBot to the front, focus is first moved to BartleBot's own
    window and then, some 25 ms later, restored to wherever it was last placed inside
    it. Focus given on this message is in place before that, so it is what gets
    restored; the 50 ms poll this replaced gave it after, and lost it (#93).
    """
    return message == WM_PARENTNOTIFY and (wparam & 0xFFFF) in _BUTTON_DOWN_EVENTS


def _intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def visible_position(
    rect: tuple[int, int, int, int],
    monitors: list[tuple[int, int, int, int]],
) -> tuple[int, int]:
    """Where to put a window whose pre-embed position was *rect* (left, top, right, bottom).

    Keeps the saved position when it still lands on a monitor. A display that has
    been unplugged or rearranged since the window was embedded would otherwise put
    it somewhere unreachable, so fall back to the first monitor in that case.
    """
    if not monitors or any(_intersects(rect, m) for m in monitors):
        return rect[0], rect[1]

    primary = monitors[0]
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    x = primary[0] + max(0, (primary[2] - primary[0] - width) // 2)
    y = primary[1] + max(0, (primary[3] - primary[1] - height) // 2)
    return x, y


# ---------------------------------------------------------------------------
# ctypes plumbing
# ---------------------------------------------------------------------------

_HWND = ctypes.wintypes.HWND
_LONG = ctypes.wintypes.LONG
_BOOL = ctypes.wintypes.BOOL
_UINT = ctypes.wintypes.UINT
_INT = ctypes.c_int
_DWORD = ctypes.wintypes.DWORD

# (name, argtypes, restype). Declaring these is not decoration: without argtypes a
# call with the wrong number of arguments is accepted silently and the callee reads
# garbage off the stack. That is exactly how the SetWindowPos call in the old
# restore path lost its uFlags.
_SIGNATURES: tuple[tuple[str, list, object], ...] = (
    ("GetParent", [_HWND], _HWND),
    ("SetParent", [_HWND, _HWND], _HWND),
    ("GetWindowLongW", [_HWND, _INT], _LONG),
    ("SetWindowLongW", [_HWND, _INT, _LONG], _LONG),
    ("GetWindowRect", [_HWND, ctypes.POINTER(ctypes.wintypes.RECT)], _BOOL),
    ("GetClientRect", [_HWND, ctypes.POINTER(ctypes.wintypes.RECT)], _BOOL),
    ("SetWindowPos", [_HWND, _HWND, _INT, _INT, _INT, _INT, _UINT], _BOOL),
    ("MoveWindow", [_HWND, _INT, _INT, _INT, _INT, _BOOL], _BOOL),
    ("ShowWindow", [_HWND, _INT], _BOOL),
    ("AttachThreadInput", [_DWORD, _DWORD, _BOOL], _BOOL),
    ("SetFocus", [_HWND], _HWND),
    ("IsChild", [_HWND, _HWND], _BOOL),
    ("GetWindowThreadProcessId", [_HWND, ctypes.POINTER(_DWORD)], _DWORD),
    ("GetGUIThreadInfo", [_DWORD, ctypes.POINTER(GUITHREADINFO)], _BOOL),
)

_user32_cache = None


def default_user32():
    """A private user32 handle with argtypes applied. Resolved lazily so this module imports anywhere.

    ``ctypes.WinDLL("user32")`` rather than ``ctypes.windll.user32``, which is cached
    process-wide and would share these prototypes with wizwalker and pymem. Declaring
    argtypes on a shared function object breaks any other caller that passes a
    differently-typed argument — see the EnumWindows failure documented in
    ``bartlebot/clients/window_kill.py``. Nothing here has collided yet; this keeps it
    that way.
    """
    global _user32_cache
    if _user32_cache is None:
        lib = ctypes.WinDLL("user32")
        for name, argtypes, restype in _SIGNATURES:
            fn = getattr(lib, name)
            fn.argtypes = argtypes
            fn.restype = restype
        _user32_cache = lib
    return _user32_cache


def _monitor_rects() -> list[tuple[int, int, int, int]]:
    """Work-area rects of every monitor, primary first. Empty if enumeration fails."""
    try:
        from PyQt6.QtGui import QGuiApplication
    except ImportError:  # pragma: no cover - PyQt6 is a hard dependency of the app
        return []

    primary = QGuiApplication.primaryScreen()
    screens = QGuiApplication.screens()
    if primary is not None:
        screens = [primary] + [s for s in screens if s is not primary]
    rects = []
    for screen in screens:
        geo = screen.availableGeometry()
        ratio = screen.devicePixelRatio() or 1.0
        rects.append(
            (
                int(geo.left() * ratio),
                int(geo.top() * ratio),
                int(geo.right() * ratio),
                int(geo.bottom() * ratio),
            )
        )
    return rects


# ---------------------------------------------------------------------------
# EmbeddedWindow
# ---------------------------------------------------------------------------


class EmbeddedWindow:
    """One foreign top-level window, reparented into ours and restorable afterwards.

    Construct it *before* embedding: everything needed to put the window back is
    captured in ``__init__``.
    """

    def __init__(self, hwnd: int, user32=None, kernel32=None) -> None:
        self._hwnd = hwnd
        self._user32 = user32 if user32 is not None else default_user32()
        self._kernel32 = kernel32 if kernel32 is not None else ctypes.windll.kernel32

        self._orig_parent: int = self._user32.GetParent(hwnd) or 0
        self._orig_style: int = self._user32.GetWindowLongW(hwnd, GWL_STYLE) & _STYLE_MASK
        self._orig_ex_style: int = self._user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & _STYLE_MASK
        self._orig_rect: tuple[int, int, int, int] = self._window_rect()

        self._game_w, self._game_h = self.client_size()

        pid = _DWORD(0)
        self._game_thread_id: int = self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        self._my_thread_id: int = self._kernel32.GetCurrentThreadId()

        self._attached = False
        self._embedded = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def hwnd(self) -> int:
        return self._hwnd

    @property
    def game_size(self) -> tuple[int, int]:
        """Native client-area size in physical pixels, measured before embedding."""
        return self._game_w, self._game_h

    @property
    def is_embedded(self) -> bool:
        return self._embedded

    # ------------------------------------------------------------------
    # Win32 reads
    # ------------------------------------------------------------------

    def _window_rect(self) -> tuple[int, int, int, int]:
        rect = ctypes.wintypes.RECT()
        self._user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def client_size(self) -> tuple[int, int]:
        """(width, height) of the window's client area, in physical pixels."""
        rect = ctypes.wintypes.RECT()
        self._user32.GetClientRect(self._hwnd, ctypes.byref(rect))
        return rect.right - rect.left, rect.bottom - rect.top

    def is_focused(self) -> bool:
        """Whether this window, or a child of it, holds keyboard focus.

        GetGUIThreadInfo queries the owning thread's focus state directly, which is
        what makes this work across the process boundary.
        """
        if not self._game_thread_id:
            return False
        gti = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if not self._user32.GetGUIThreadInfo(self._game_thread_id, ctypes.byref(gti)):
            return False
        focus = gti.hwndFocus
        return bool(focus) and (focus == self._hwnd or bool(self._user32.IsChild(self._hwnd, focus)))

    # ------------------------------------------------------------------
    # Embed / restore
    # ------------------------------------------------------------------

    def embed(self, parent_hwnd: int) -> None:
        """Reparent the window into *parent_hwnd* as a borderless child."""
        self._user32.SetParent(self._hwnd, parent_hwnd)
        self._user32.SetWindowLongW(self._hwnd, GWL_STYLE, _as_long(child_style(self._orig_style)))
        self._user32.SetWindowPos(
            self._hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )

        # Share input queues so our thread may call SetFocus on the game window.
        # Cross-thread SetFocus is silently ignored without this.
        if self._game_thread_id and self._game_thread_id != self._my_thread_id:
            self._user32.AttachThreadInput(self._my_thread_id, self._game_thread_id, True)
            self._attached = True

        self._embedded = True

    def move(self, x: int, y: int, width: int, height: int) -> None:
        """Position the embedded window within its parent (physical pixels)."""
        self._user32.MoveWindow(self._hwnd, x, y, width, height, True)

    def restore(self) -> None:
        """Put the window back the way it was found: top-level, framed, and movable.

        Idempotent — detaching the input queues twice would unbalance the
        AttachThreadInput refcount, and restore is reachable from several paths.
        """
        if not self._embedded:
            return
        self._embedded = False

        if self._attached:
            self._user32.AttachThreadInput(self._my_thread_id, self._game_thread_id, False)
            self._attached = False

        # Style first: WS_CHILD must be gone before the window stops being a child,
        # or the reparent leaves it in a half-child state that never repaints a frame.
        self._user32.SetWindowLongW(self._hwnd, GWL_STYLE, _as_long(restored_style(self._orig_style)))
        # WS_EX_APPWINDOW forces the taskbar button back; being a child loses it.
        self._user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, _as_long(self._orig_ex_style | WS_EX_APPWINDOW))
        self._user32.SetParent(self._hwnd, restored_parent(self._orig_parent, self._orig_style))

        x, y = visible_position(self._orig_rect, _monitor_rects())
        width = self._orig_rect[2] - self._orig_rect[0]
        height = self._orig_rect[3] - self._orig_rect[1]
        self._user32.SetWindowPos(
            self._hwnd,
            0,
            x,
            y,
            width,
            height,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        self._user32.ShowWindow(self._hwnd, SW_SHOW)

    def focus(self) -> None:
        """Give the game window keyboard focus (requires the input queues to be attached)."""
        self._user32.SetFocus(self._hwnd)


def _as_long(style: int) -> int:
    """Reinterpret a 32-bit style word as the signed LONG that SetWindowLongW declares."""
    return style - 0x100000000 if style >= 0x80000000 else style
