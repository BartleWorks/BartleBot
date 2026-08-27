"""Route Qt's own messages into logging, with a stack trace for QPainter errors.

Qt writes to stderr directly, so its warnings never reached ``logs/`` and a
session log gave no hint that anything had gone wrong at the Qt level.  This
installs a message handler that logs every Qt message, and for ``QPainter::``
messages specifically also dumps the Python stack.

That stack is what resolved issue #72.  QPainter reports the *device* it failed
on, never the caller:

    QPainter::begin: Paint device returned engine == 0, type: 3

``type: 3`` is ``QInternal::Image`` — the enum is sequential (``Widget=1,
Pixmap=2, Image=3``) — so the failing device was a QImage, which pointed away
from every painter we construct ourselves.  It turned out to be Qt's font
engine collapsing its offscreen glyph buffer to 0x0 when the minimap's
transform took node labels sub-pixel; see ``bartlebot/gui/canvas/lod.py``.  The
Python frame in the dump named the exact ``drawText`` call in one run.

Dumps are throttled per distinct stack, because the failure repeats four times
per glyph per repaint — a single pan produced hundreds of lines.
"""

import hashlib
import logging
import sys
import time
import traceback

from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

logger = logging.getLogger(__name__)

#: How long to wait before dumping full detail for the same stack again.
DUMP_INTERVAL_SECONDS = 10.0

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


# ---------------------------------------------------------------------------
# Pure helpers — testable without a QApplication
# ---------------------------------------------------------------------------


def is_painter_message(message: str) -> bool:
    """True for the QPainter messages worth a stack trace."""
    return message.startswith("QPainter::")


def relevant_frames(frames: list[str]) -> list[str]:
    """Drop the frames belonging to this module from a formatted stack.

    The handler runs at the bottom of whatever Python stack Qt re-entered, so
    the last frames are always ``qt_paint_debug`` itself and say nothing about
    the caller.
    """
    return [f for f in frames if "qt_paint_debug.py" not in f]


def stack_signature(frames: list[str]) -> str:
    """Short stable id for a stack, used to collapse a burst into one dump.

    Keying on the stack rather than the message text is what makes one burst
    one event: the messages in a cycle (begin / setCompositionMode / fillRect /
    end) all come from the same frame.
    """
    joined = "".join(relevant_frames(frames))
    return hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()[:12]


class BurstTracker:
    """Rate-limits full stack dumps to one per stack per interval."""

    def __init__(self, interval: float = DUMP_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._last_dump: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def should_dump(self, signature: str, now: float) -> bool:
        last = self._last_dump.get(signature)
        if last is None or now - last >= self._interval:
            self._last_dump[signature] = now
            return True
        self._suppressed[signature] = self._suppressed.get(signature, 0) + 1
        return False

    def drain_suppressed(self, signature: str) -> int:
        """Number of messages suppressed for this stack since the last dump."""
        return self._suppressed.pop(signature, 0)


def format_dump(message: str, frames: list[str], signature: str, suppressed: int) -> str:
    """Render one stack dump. Pure, so the layout is testable."""
    lines = ["", "=" * 72, f"QPainter error — stack {signature}", f"  message: {message}"]
    if suppressed:
        lines.append(f"  ({suppressed} further QPainter messages on this stack suppressed since the last dump)")
    lines.append("  Python stack (innermost last):")
    trimmed = relevant_frames(frames)
    if trimmed:
        lines.extend("    " + line for chunk in trimmed for line in chunk.rstrip().splitlines())
    else:
        lines.append("    <no Python frames — Qt-internal paint, not one of our paintEvents>")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The message handler
# ---------------------------------------------------------------------------

_tracker = BurstTracker()
_installed = False


def _handler(msg_type: QtMsgType, context, message: str) -> None:
    level = _QT_LEVELS.get(msg_type, logging.WARNING)
    location = ""
    try:
        if context is not None and context.file:
            location = f" [{context.file}:{context.line}]"
    except (AttributeError, RuntimeError):
        pass
    logger.log(level, "Qt: %s%s", message, location)

    # Ours replaces Qt's handler, so keep the console output it was providing.
    print(message, file=sys.stderr)

    if not is_painter_message(message):
        return
    try:
        frames = traceback.format_stack()
        signature = stack_signature(frames)
        if _tracker.should_dump(signature, time.monotonic()):
            logger.warning(format_dump(message, frames, signature, _tracker.drain_suppressed(signature)))
    except Exception:
        logger.exception("QPainter stack dump failed")


def install(interval: float = DUMP_INTERVAL_SECONDS) -> None:
    """Install the Qt message handler. Safe to call more than once."""
    global _installed, _tracker
    if _installed:
        return
    _tracker = BurstTracker(interval)
    qInstallMessageHandler(_handler)
    _installed = True
    logger.info("Qt message handler installed")
