"""Developer-only tracer for #120: which window flashed on screen, and who made it.

On only with ``BARTLEBOT_DEV`` set (see ``store_roots.dev_mode``). A window that
appears and vanishes within a frame is gone before anyone can inspect it, and the
report "empty windows flash in the top left" fits two quite different causes: one
of ours, shown before it had a parent (``gui/CLAUDE.md``, "Never show a widget
before it has a parent"), or a window some other process opened over the embedded
game. So it watches from both sides and logs a WARNING for each:

- **Qt.** An event filter on the ``QApplication`` sees every ``Show``. A plain
  widget becoming a top-level window is the flash, and its Python stack names the
  call that showed it. Dialogs, menus, tooltips and the main window are windows on
  purpose and are left out -- see ``is_stray_window``.
- **Win32.** ``SetWinEventHook(EVENT_OBJECT_SHOW)`` sees a top-level window shown by
  *any* process, which the filter above cannot. Only our own process and the game
  clients are reported, since every other program on the desktop shows windows all
  day. Each line carries the process, the window class and the rect, which is what
  tells a Qt window (class ``Qt6...``) from one of the game's. Our own tooltips and
  popups are left out here as well, by their window class (``is_expected_own_class``):
  this side used to log three lines for every tooltip.

A game window's title is never logged: the launcher titles it by account.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import re
import traceback

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

logger = logging.getLogger(__name__)

GAME_EXE = "WizardGraphicalClient.exe"

#: How many stack frames to log with a stray Qt window.
STACK_FRAMES = 14

_EVENT_OBJECT_SHOW = 0x8002
_WINEVENT_OUTOFCONTEXT = 0x0000
_OBJID_WINDOW = 0
_GA_ROOT = 2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LONG,
    ctypes.wintypes.LONG,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD,
)


def _win32() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """Private, typed handles: ``windll``'s shared ones return ``c_int``, which cuts a
    64-bit window handle in half, and typing those would change them for every caller."""
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    w = ctypes.wintypes
    user32.SetWinEventHook.argtypes = [w.DWORD, w.DWORD, w.HMODULE, _WINEVENTPROC, w.DWORD, w.DWORD, w.DWORD]
    user32.SetWinEventHook.restype = w.HANDLE
    user32.UnhookWinEvent.argtypes = [w.HANDLE]
    user32.GetAncestor.argtypes = [w.HWND, w.UINT]
    user32.GetAncestor.restype = w.HWND
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel32.OpenProcess.restype = w.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel32.CloseHandle.argtypes = [w.HANDLE]
    return user32, kernel32


def is_stray_window(widget: QWidget) -> bool:
    """Whether showing *widget* opened a window nobody meant to open.

    A parentless plain widget is given ``Qt.WindowType.Window``; a dialog, a menu and
    a tooltip each carry a window type of their own, so asking the type is what keeps
    them out. The main window is the one plain top-level that is meant.
    """
    if widget.parent() is not None or isinstance(widget, QMainWindow):
        return False
    return widget.windowType() == Qt.WindowType.Window


#: Window classes of ours that are windows on purpose: Qt's tooltips and popups (menus,
#: combo lists), under any Qt version, with or without their drop shadow, and the shadow
#: Windows draws beside them. The Qt side leaves these out by window type; this is the
#: same list for the Win32 side, which only has the class to go by.
_EXPECTED_OWN_CLASS = re.compile(r"^(Qt\d+QWindow(ToolTip|Popup)|SysShadow$)")


def is_expected_own_class(class_name: str) -> bool:
    """Whether a window of our own process with this class is a tooltip, a popup or their shadow."""
    return _EXPECTED_OWN_CLASS.match(class_name) is not None


def should_report(exe_name: str, pid: int, own_pid: int, class_name: str = "") -> bool:
    """Whether a window shown by process *pid* (``exe_name``) belongs in the log.

    Every window of a game client does. Of our own, all but the tooltips and popups
    *class_name* gives away: hovering a button is not a stray window.
    """
    if pid == own_pid:
        return not is_expected_own_class(class_name)
    return exe_name.lower() == GAME_EXE.lower()


class WindowShowTracer(QObject):
    """Logs every stray top-level window, from Qt and from Win32. See the module docstring."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._own_pid = os.getpid()
        self._exe_names: dict[int, str] = {}
        app.installEventFilter(self)
        # Held on self: ctypes frees a callback nothing references, and Windows would
        # then call into freed memory.
        self._proc = _WINEVENTPROC(self._on_win_event)
        self._hook = None
        self._user32 = self._kernel32 = None
        try:
            self._user32, self._kernel32 = _win32()
            self._hook = self._user32.SetWinEventHook(
                _EVENT_OBJECT_SHOW, _EVENT_OBJECT_SHOW, 0, self._proc, 0, 0, _WINEVENT_OUTOFCONTEXT
            )
        except (AttributeError, OSError):
            logger.debug("SetWinEventHook unavailable; tracing Qt windows only", exc_info=True)
        app.aboutToQuit.connect(self.close)
        logger.info("Window show tracer on (BARTLEBOT_DEV); stray windows are logged here as WARNINGs")

    def close(self) -> None:
        """Stop tracing: drop the event filter and give the WinEvent hook back."""
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._hook:
            self._user32.UnhookWinEvent(self._hook)
            self._hook = None

    # -- Qt -----------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and is_stray_window(obj):
            geometry = obj.geometry()
            logger.warning(
                "Stray Qt window shown: %s (objectName=%r) at %d,%d %dx%d\n%s",
                type(obj).__name__,
                obj.objectName(),
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
                "".join(traceback.format_stack(limit=STACK_FRAMES)[:-1]),
            )
        return False

    # -- Win32 --------------------------------------------------------------

    def _on_win_event(self, _hook, _event, hwnd, id_object, _id_child, _thread, _time) -> None:
        try:
            if id_object != _OBJID_WINDOW or not hwnd:
                return
            user32 = self._user32
            if user32.GetAncestor(hwnd, _GA_ROOT) != hwnd:
                return  # a child control, not a window of its own
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = self.exe_name(pid.value)
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if not should_report(exe, pid.value, self._own_pid, class_name.value):
                return
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            logger.warning(
                "Top-level window shown by %s (pid %d): class=%r hwnd=%#x at %d,%d %dx%d",
                exe or "?",
                pid.value,
                class_name.value,
                hwnd,
                rect.left,
                rect.top,
                rect.right - rect.left,
                rect.bottom - rect.top,
            )
        except Exception:
            # Called from Windows: an exception escaping here would be lost anyway.
            logger.debug("Window show tracer failed on a WinEvent", exc_info=True)

    def exe_name(self, pid: int) -> str:
        """The executable name behind *pid*, or "" if it cannot be read. Cached per pid."""
        if pid in self._exe_names:
            return self._exe_names[pid]
        name = ""
        kernel32 = self._kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                size = ctypes.wintypes.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    name = os.path.basename(buffer.value)
            finally:
                kernel32.CloseHandle(handle)
        self._exe_names[pid] = name
        return name
