"""The DSL check process's own side: ending it when the app ends.

``gui/combat_strategies/dsl_checks.py`` parses strategies in a child process, a
``ProcessPoolExecutor`` worker kept for the life of the app. A worker waits on its call queue for the next job and never
notices that its parent has gone: a spawned ``multiprocessing.Queue`` gives the child
both ends of its pipe, so the read never breaks. The pool tells its workers to leave
from Python's shutdown, which the app skips on quit (``gui/app_exit.py``) and which a
crash or a kill never reaches either, so each such quit left its worker running for
good.

:func:`exit_with_parent` is the pool's ``initializer``. It ends the child the moment its
parent ends, however it ended. It lives outside ``gui/`` so the child imports no Qt to
unpickle it.
"""

import ctypes
import ctypes.wintypes
import os
import threading
from collections.abc import Callable

_SYNCHRONIZE = 0x00100000
_INFINITE = 0xFFFFFFFF


def _kernel32():
    # A private handle, never ctypes.windll's shared one: see clients/window_kill.py's
    # default_user32 for what declaring argtypes on the shared one broke.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
    return kernel32


def exit_with_parent(parent_pid: int, _exit: Callable[[int], object] = os._exit) -> None:
    """Watch *parent_pid* from a daemon thread, and end this process when it ends.

    Pass it as a pool's ``initializer`` with ``initargs=(os.getpid(),)``: the parent is
    alive then, waiting for the worker to start, so the id cannot yet belong to anything
    else. A parent that cannot be opened is taken to be gone already. *_exit* is for the
    tests, which must not end the process running them.
    """
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, parent_pid)
    if not handle:
        _exit(0)
        return

    def watch() -> None:
        kernel32.WaitForSingleObject(handle, _INFINITE)
        _exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()
