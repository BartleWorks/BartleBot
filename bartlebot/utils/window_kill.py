"""Close a game client for real, error popup or not.

Crash recovery is gated on the client's *process* being gone: ``remove_dead_clients()``
asks ``is_running()``, which is a process-liveness check, and until it answers False the
dead client stays in the handler, ``_on_clients_removed`` never fires, and nothing is
relaunched. A window that is closing is not enough.

That is what makes an error popup fatal rather than cosmetic (#79). A client that is
crashing *sometimes* puts up a modal — its own error box, or the Windows one — which
parks the process on a message pump waiting for a click nobody is going to give it. The
window never finishes closing, the process never exits, and the bot sits there forever
with a dead client it thinks is alive.

Intermittent is the important part, and it is why the fix is a wait-and-check loop
rather than a popup handler. Most crashes close on WM_CLOSE with no dialog at all, so
anything that *assumes* a popup — waiting a fixed time for one, or requiring one to be
dismissed before moving on — would slow down or stall the common case to fix the rare
one. Here the popup sweep is something that happens while waiting for an exit that is
usually already underway, and costs nothing on the runs where no dialog appears.

So closing is a sequence, not a message:

1. ``WM_CLOSE``, so a client that is merely unresponsive gets to shut down cleanly and
   release its account session.
2. While waiting, dismiss any dialog the process puts up — that is the popup, and
   dismissing it is what lets step 1 finish.
3. If it is still alive when the grace period runs out, ``TerminateProcess``. A hard
   kill takes the process's windows with it, popups included.

Step 3 alone would work and is much simpler. It is the fallback rather than the opener
because a hard-killed client leaves its session held server-side, and the relaunch that
follows then has to fight it.

Kept free of Qt and of BartleBot types so it is testable with fake ``user32``/``kernel32``
objects; nothing here needs a real window.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import logging
import time

from bartlebot.nodes.timing import CLIENT_CLOSE_GRACE, CLIENT_CLOSE_POLL, CLIENT_TERMINATE_TIMEOUT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
WM_CLOSE = 0x0010

STILL_ACTIVE = 259
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# QUERY_LIMITED, not QUERY_INFORMATION: it is the one that is still granted for a
# process that has exited but whose handle has not been released yet, which is
# exactly the state we are polling for.

DIALOG_CLASS = "#32770"
# The window class of every standard Windows dialog — MessageBox, the common
# dialogs, and WerFault's "stopped working" box. The game's own windows are class
# "Wizard Graphical Client", so matching on this cannot hit the client itself.

_MAX_CLASS_NAME = 256


# ---------------------------------------------------------------------------
# ctypes plumbing
# ---------------------------------------------------------------------------

_HWND = ctypes.wintypes.HWND
_BOOL = ctypes.wintypes.BOOL
_DWORD = ctypes.wintypes.DWORD
_UINT = ctypes.wintypes.UINT
_INT = ctypes.c_int
_LPARAM = ctypes.wintypes.LPARAM
_WPARAM = ctypes.wintypes.WPARAM
_HANDLE = ctypes.wintypes.HANDLE
_LPWSTR = ctypes.wintypes.LPWSTR

_ENUM_PROC = ctypes.WINFUNCTYPE(_BOOL, _HWND, _LPARAM)

# (name, argtypes, restype). Without argtypes a 64-bit HWND is silently truncated to
# an int and the call goes to the wrong window — see #105 for what that costs.
_USER32_SIGNATURES: tuple[tuple[str, list, object], ...] = (
    ("EnumWindows", [_ENUM_PROC, _LPARAM], _BOOL),
    ("GetWindowThreadProcessId", [_HWND, ctypes.POINTER(_DWORD)], _DWORD),
    ("GetClassNameW", [_HWND, _LPWSTR, _INT], _INT),
    ("IsWindowVisible", [_HWND], _BOOL),
    ("IsWindow", [_HWND], _BOOL),
    ("PostMessageW", [_HWND, _UINT, _WPARAM, _LPARAM], _BOOL),
)

_KERNEL32_SIGNATURES: tuple[tuple[str, list, object], ...] = (
    ("OpenProcess", [_DWORD, _BOOL, _DWORD], _HANDLE),
    ("GetExitCodeProcess", [_HANDLE, ctypes.POINTER(_DWORD)], _BOOL),
    ("TerminateProcess", [_HANDLE, _UINT], _BOOL),
    ("CloseHandle", [_HANDLE], _BOOL),
)

_user32_cache = None
_kernel32_cache = None


def default_user32():
    """The real user32, with argtypes applied. Resolved lazily so this module imports anywhere."""
    global _user32_cache
    if _user32_cache is None:
        lib = ctypes.windll.user32
        for name, argtypes, restype in _USER32_SIGNATURES:
            fn = getattr(lib, name)
            fn.argtypes = argtypes
            fn.restype = restype
        _user32_cache = lib
    return _user32_cache


def default_kernel32():
    """The real kernel32, with argtypes applied."""
    global _kernel32_cache
    if _kernel32_cache is None:
        lib = ctypes.windll.kernel32
        for name, argtypes, restype in _KERNEL32_SIGNATURES:
            fn = getattr(lib, name)
            fn.argtypes = argtypes
            fn.restype = restype
        _kernel32_cache = lib
    return _kernel32_cache


# ---------------------------------------------------------------------------
# Window queries
# ---------------------------------------------------------------------------


def window_process_id(hwnd: int, user32=None) -> int:
    """The PID owning *hwnd*, or 0 if the window is gone."""
    user32 = user32 if user32 is not None else default_user32()
    pid = _DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_class(hwnd: int, user32=None) -> str:
    """The class name of *hwnd*, or "" if it could not be read."""
    user32 = user32 if user32 is not None else default_user32()
    buf = ctypes.create_unicode_buffer(_MAX_CLASS_NAME)
    length = user32.GetClassNameW(hwnd, buf, _MAX_CLASS_NAME)
    return buf.value if length else ""


def top_level_windows(user32=None) -> list[int]:
    """Every top-level window on the desktop, in Z-order."""
    user32 = user32 if user32 is not None else default_user32()
    handles: list[int] = []

    @_ENUM_PROC
    def _collect(hwnd, _lparam):
        handles.append(int(hwnd))
        return True

    user32.EnumWindows(_collect, 0)
    return handles


def popup_windows(pid: int, user32=None) -> list[int]:
    """Visible dialog windows owned by *pid*.

    Only class ``#32770`` counts. Anything the game itself draws — including its
    in-game modals, which are not real windows at all but UI nodes inside the client
    window — is a different class, so this returns the error popups and nothing else.
    """
    user32 = user32 if user32 is not None else default_user32()
    found = []
    for hwnd in top_level_windows(user32):
        if not user32.IsWindowVisible(hwnd):
            continue
        if window_process_id(hwnd, user32) != pid:
            continue
        if window_class(hwnd, user32) == DIALOG_CLASS:
            found.append(hwnd)
    return found


def dismiss_popups(pid: int, user32=None) -> int:
    """Post ``WM_CLOSE`` to every error dialog *pid* has open; return how many.

    Closing a dialog is the same as clicking its X, which for a one-button error box
    is the button. A dialog that ignores it is not a problem — the caller falls back
    to terminating the process, which takes the dialog with it.
    """
    user32 = user32 if user32 is not None else default_user32()
    dismissed = 0
    for hwnd in popup_windows(pid, user32):
        logger.info("Dismissing error popup hwnd=%d belonging to pid=%d", hwnd, pid)
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        dismissed += 1
    return dismissed


# ---------------------------------------------------------------------------
# Process queries
# ---------------------------------------------------------------------------


def is_process_alive(pid: int, kernel32=None) -> bool:
    """Whether *pid* is still running.

    False for a PID that cannot be opened at all: a process we are not allowed to
    query is not one we could have killed either, and treating it as alive would
    stall recovery indefinitely — the failure mode this whole module exists to avoid.
    """
    if not pid:
        return False
    kernel32 = kernel32 if kernel32 is not None else default_kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = _DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate_process(pid: int, kernel32=None) -> bool:
    """Hard-kill *pid* via ``TerminateProcess``. True if the call was made."""
    if not pid:
        return False
    kernel32 = kernel32 if kernel32 is not None else default_kernel32()
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        logger.warning("Could not open pid=%d for termination", pid)
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# The close sequence
# ---------------------------------------------------------------------------


async def force_close_client(
    hwnd: int,
    *,
    pid: int | None = None,
    user32=None,
    kernel32=None,
    grace: float = CLIENT_CLOSE_GRACE,
    poll: float = CLIENT_CLOSE_POLL,
    terminate_timeout: float = CLIENT_TERMINATE_TIMEOUT,
) -> bool:
    """Close the client window at *hwnd* and wait for its process to actually exit.

    Returns True once the process is gone. False means it survived both a clean close
    and a terminate, which leaves the client stuck in the handler — the caller should
    say so rather than wait for a recovery that will not come.

    *pid* may be passed when the caller already knows it (WizWalker's ``Client``
    does); otherwise it is read from the window, which only works while the window
    still exists.
    """
    user32 = user32 if user32 is not None else default_user32()
    kernel32 = kernel32 if kernel32 is not None else default_kernel32()

    if pid is None:
        pid = window_process_id(hwnd, user32)

    if not pid:
        # No window, no process: either it already exited or the handle was never
        # valid. Post the close anyway in case the window outlives the lookup, and
        # report success — there is nothing left to wait for.
        logger.info("No process found for hwnd=%d — treating it as already closed", hwnd)
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True

    logger.warning("Closing client hwnd=%d (pid=%d)", hwnd, pid)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        await asyncio.sleep(poll)
        if not is_process_alive(pid, kernel32):
            logger.info("Client pid=%d exited cleanly", pid)
            return True
        # A popup that appeared after WM_CLOSE is what is holding the shutdown.
        # Sweeping every poll also catches a second dialog behind the first.
        dismiss_popups(pid, user32)

    logger.warning(
        "Client pid=%d did not exit within %.1fs — terminating (an error popup is the usual cause)",
        pid,
        grace,
    )
    terminate_process(pid, kernel32)

    # TerminateProcess is asynchronous: it returns before the process object is torn
    # down, and until then is_running() still reports the client as alive.
    terminate_deadline = time.monotonic() + terminate_timeout
    while time.monotonic() < terminate_deadline:
        if not is_process_alive(pid, kernel32):
            logger.info("Client pid=%d terminated", pid)
            return True
        await asyncio.sleep(poll)

    if is_process_alive(pid, kernel32):
        logger.error("Client pid=%d survived TerminateProcess — recovery cannot proceed for hwnd=%d", pid, hwnd)
        return False
    return True
