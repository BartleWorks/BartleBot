"""Per-process resource counts the OS knows and the game does not report.

Only GDI objects so far, and only because a client can exhaust its quota inside one
session (#164): one reached exactly 10,000, the Windows default per-process
``GDIProcessHandleQuota``, after which every GDI allocation in that process fails.

Kept free of Qt and of BartleBot types, like ``window_kill``, so it is testable with fake
``user32``/``kernel32`` objects and needs no game process.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging

logger = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# The same right window_kill polls liveness with: still granted for a process that has
# exited but whose handle is not yet released, which is a state a relogging client passes
# through.

GR_GDIOBJECTS = 0
GR_USEROBJECTS = 1

_DWORD = ctypes.wintypes.DWORD
_BOOL = ctypes.wintypes.BOOL
_HANDLE = ctypes.wintypes.HANDLE
_UINT = ctypes.wintypes.UINT

_USER32_SIGNATURES: tuple[tuple[str, list, object], ...] = (("GetGuiResources", [_HANDLE, _DWORD], _DWORD),)

_KERNEL32_SIGNATURES: tuple[tuple[str, list, object], ...] = (
    ("OpenProcess", [_DWORD, _BOOL, _DWORD], _HANDLE),
    ("CloseHandle", [_HANDLE], _BOOL),
)

_user32_cache = None
_kernel32_cache = None


def default_user32():
    """A private user32 handle with our argtypes applied.

    ``ctypes.WinDLL("user32")`` rather than ``ctypes.windll.user32`` for the reason spelled
    out in ``window_kill.default_user32``: the latter is cached per name for the whole
    process, so argtypes declared on it apply to every other library's calls too, and that
    has already broken wizwalker's own window enumeration once.
    """
    global _user32_cache
    if _user32_cache is None:
        lib = ctypes.WinDLL("user32")
        for name, argtypes, restype in _USER32_SIGNATURES:
            fn = getattr(lib, name)
            fn.argtypes = argtypes
            fn.restype = restype
        _user32_cache = lib
    return _user32_cache


def default_kernel32():
    """A private kernel32 handle with our argtypes applied. Private for the same reason."""
    global _kernel32_cache
    if _kernel32_cache is None:
        lib = ctypes.WinDLL("kernel32")
        for name, argtypes, restype in _KERNEL32_SIGNATURES:
            fn = getattr(lib, name)
            fn.argtypes = argtypes
            fn.restype = restype
        _kernel32_cache = lib
    return _kernel32_cache


def gui_object_count(pid: int, which: int = GR_GDIOBJECTS, user32=None, kernel32=None) -> int:
    """How many GDI (or USER) objects *pid* currently holds; 0 if it cannot be read.

    Zero is the failure value as well as an impossible real one -- a live game client holds
    thousands -- so callers can treat it as "no reading" without a second return value.
    A process that has exited, or one we may not open, simply has no count.
    """
    if not pid:
        return 0
    user32 = user32 if user32 is not None else default_user32()
    kernel32 = kernel32 if kernel32 is not None else default_kernel32()

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        logger.debug("Could not open pid=%d to read its GUI object count", pid)
        return 0
    try:
        return int(user32.GetGuiResources(handle, which))
    finally:
        kernel32.CloseHandle(handle)


def gdi_object_count(pid: int, user32=None, kernel32=None) -> int:
    """How many GDI objects *pid* holds, against a 10,000 quota. 0 if it cannot be read."""
    return gui_object_count(pid, GR_GDIOBJECTS, user32=user32, kernel32=kernel32)
