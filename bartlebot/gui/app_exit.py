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
closed, the standard streams flushed, and the process ends with ``os._exit``.
"""

import logging
import os
import sys
from typing import NoReturn

logger = logging.getLogger(__name__)


def exit_skipping_teardown(code: int = 0) -> NoReturn:
    """Flush the logs and the standard streams, then end the process with *code*.

    Call only after the event loop has returned. ``atexit`` handlers do not run, which is
    the point for the Qt wrappers and harmless for the rest: none of ours registers one.
    """
    logger.info("=== BartleBot shutdown ===")
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:
            pass
    os._exit(code)
