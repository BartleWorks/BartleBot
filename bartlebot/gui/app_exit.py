"""Ending the process once the app has shut itself down, without Python's own teardown.

By the time the event loop returns, the app has done everything a quit needs: the clients
are released, the settings written, the hotkeys given back. What is left is the
interpreter's finalization, which frees the remaining Python wrappers of Qt objects in no
order Qt can rely on -- a widget's after the QApplication's, or a child's after its parent
deleted it. Every so often one of those frees reaches a C++ object that is already gone,
and the process dies in PyQt6's ``sip`` module with an access violation after the session
log has ended, which reads as a crash on quit. It is intermittent (about one quit in three)
and goes back at least to September 2026.

Nothing in that teardown is worth keeping, so it is skipped: the logs are flushed and
closed, the standard streams flushed, and the process ends with ``TerminateProcess``.

Not ``os._exit``, which this first used: it skips Python's teardown but still detaches
every DLL, and Qt's ran its static destructors with the QApplication still alive. Its
thread storage then warned ``QThreadStorage: entry 2 destroyed before end of thread`` from
inside the detach, through our Qt message handler -- Python code running under the loader
lock, after logging was shut down. ``TerminateProcess`` notifies no DLL, so none of that
runs. ``os._exit`` stays as the fallback should the call fail.
"""

import ctypes
import logging
import os
import sys
from typing import NoReturn

logger = logging.getLogger(__name__)


def _terminate(code: int) -> None:
    """End this process at once with *code*, notifying no DLL. Returns only if it failed."""
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    except Exception:
        pass


def exit_skipping_teardown(code: int = 0) -> NoReturn:
    """Flush the logs and the standard streams, then end the process with *code*.

    Call only after the event loop has returned. ``atexit`` handlers do not run, which is
    the point for the Qt wrappers. None of ours registers one, but the standard library
    does: ``concurrent.futures`` tells a process pool's workers to leave from one, so a
    worker has to watch for the app ending itself (``combat/dsl_check_worker.py``).
    """
    logger.info("=== BartleBot shutdown ===")
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    _terminate(code)
    os._exit(code)
