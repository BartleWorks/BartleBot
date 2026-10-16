"""Logging configuration: a per-session file, and a bridge to the in-app log widget.

Three things here exist to make a session log readable rather than merely complete.
A five-hour four-client run produces tens of thousands of lines, and before the log-readability work the
overwhelming majority of them were noise of three specific kinds:

  * every node logged through ``BotMachine.log()``, so every record was named
    ``bartlebot.bot.machine`` and no node could be grepped for. ``ShortNameFormatter``
    and the per-node loggers ``BotMachine`` now creates are the other half of that fix.
  * the canvas echoed each transition to the log widget, and those records went to the
    file too, saying a second time what the machine had just said. ``gui_only`` records
    are dropped from the file for that reason.
  * a single failing step repeated the same warning hundreds of times.
    ``RepeatCollapsingFilter`` keeps the first and counts the rest.
"""

import datetime
import importlib.metadata
import logging
import logging.handlers
import platform
import sys
import threading
from collections.abc import Callable
from pathlib import Path

#: Records carrying this attribute are for the in-app log widget only and never
#: reach the session file. Set it with ``logger.info(msg, extra=GUI_ONLY)`` on a
#: message that restates something another logger has already written.
GUI_ONLY = {"gui_only": True}

#: Libraries whose versions the session header names, beside the app's own. A packaged
#: build carries their metadata only because ``tools/build_package.py`` copies it.
HEADER_LIBRARIES = ("PyQt6", "qasync", "transitions", "wizwalker")

#: Third-party loggers whose DEBUG output is not actionable here. Every one of these
#: is startup chatter about platforms we do not run on — keyring probing KWallet and
#: SecretService, wakepy enumerating GNOME and macOS methods — and it used to be the
#: first thing anyone opening a log saw.
_NOISY_LIBRARIES = (
    "asyncio",
    "keyring",
    # Pillow logs every image plugin it imports and every TIFF tag it reads, on each art extraction.
    "PIL",
    "qasync",
    "transitions",
    "wakepy",
    "win32ctypes",
)

#: wizwalker's own DEBUG output is per-memory-read; INFO is the useful level.
_WIZWALKER_LEVEL = logging.INFO

#: wizsprinter's record of what each turn decided and why: the hand, every priority
#: walked, every fuse, the card cast and at which slot. It writes at DEBUG, which the
#: wizwalker level above drops, so ``set_turn_trace`` is what lets it through.
TURN_TRACE_LOGGER = "wizwalker.extensions.wizsprinter.turns"

_FILE_FORMAT = "[%(asctime)s] [%(levelname)-7s] %(name)s: %(message)s"


class ShortNameFormatter(logging.Formatter):
    """Formatter that drops the ``bartlebot.`` prefix from logger names.

    Every line in a session log is from this application, so the prefix is eleven
    characters of nothing on every one of them. What is left is what distinguishes
    the lines — ``node.quicksell``, ``bot.machine``, ``gui.canvas.bot_canvas`` —
    and third-party loggers keep their full names, which is how you can still tell
    them apart at a glance.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Restored afterwards: the same record is handed to every other handler.
        original = record.name
        record.name = original.removeprefix("bartlebot.")
        try:
            return super().format(record)
        finally:
            record.name = original


class GuiOnlyFilter(logging.Filter):
    """Drops records marked GUI_ONLY. Installed on the session-file handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "gui_only", False)


class RepeatCollapsingFilter(logging.Filter):
    """Emits the first of a run of identical messages and counts the rest.

    A bot that fails the same step for four hours writes the same WARNING several
    hundred times; one representative run held 1,282 warnings that were five
    distinct messages. Suppressing the repeats outright would lose the fact that it
    kept happening, so the count is reported when the streak ends::

        [WARNING] node.handle_combat: timed out after 30.0s waiting for combat
        ... last message repeated 521 more times
        [INFO   ] node.quicksell: quicksell: keep_list=[...]

    Only *consecutive* identical messages from the same logger at the same level
    collapse, so two failures alternating are both still visible — which is the
    case where a count would actually hide something.

    The summary goes straight to the handler this filter is installed on rather
    than through a logger, because a logger would hand it to every other handler
    and re-enter this filter.
    """

    def __init__(self, handler: logging.Handler) -> None:
        super().__init__()
        self._handler = handler
        self._last: tuple[str, int, str] | None = None
        self._repeats = 0

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.getMessage())
        if key == self._last:
            self._repeats += 1
            return False
        self._flush(record)
        self._last = key
        return True

    def _flush(self, following: logging.LogRecord) -> None:
        """Emit the "repeated N more times" line for the streak that just ended."""
        if not self._repeats or self._last is None:
            return
        count, self._repeats = self._repeats, 0
        name, levelno, _ = self._last
        summary = logging.LogRecord(
            name=name,
            level=levelno,
            pathname=following.pathname,
            lineno=following.lineno,
            msg="... last message repeated %d more time%s",
            args=(count, "" if count == 1 else "s"),
            exc_info=None,
        )
        # emit() is normally called by handle() under the handler's lock; this call
        # bypasses handle() to avoid re-entering this filter, so it takes the lock itself.
        try:
            self._handler.acquire()
            try:
                self._handler.emit(summary)
            finally:
                self._handler.release()
        except Exception:
            pass  # a summary line must never take the record that follows it down


class _GuiBridgeHandler(logging.Handler):
    """Forwards formatted log records to registered GUI callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: list[Callable[[str, int], None]] = []

    def add_callback(self, cb: Callable[[str, int], None]) -> None:
        self._callbacks.append(cb)

    def remove_callback(self, cb: Callable[[str, int], None]) -> None:
        self._callbacks = [c for c in self._callbacks if c is not cb]

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        for cb in list(self._callbacks):
            try:
                cb(msg, record.levelno)
            except Exception:
                pass  # never let GUI errors silence the logger


_gui_bridge = _GuiBridgeHandler()


def prune_session_logs(log_dir: Path, keep: int = 10) -> None:
    """Delete all but the *keep* most recent session logs in *log_dir*.

    Only ``bartlebot_*.log`` files are touched. A file that cannot be deleted -- still
    open in another running instance -- is left for a later startup to try again.
    """
    logs = sorted(log_dir.glob("bartlebot_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def session_log_dir() -> Path:
    """Where session logs go: ``logs/`` under the user root, never the install directory.

    An update replaces the install directory whole, so logs there would be lost with it
    (#168). In developer mode the user root is the repo, so they stay in its ``logs/``.
    """
    from bartlebot.content.store_roots import resolve_roots  # noqa: PLC0415 - kept off this module's import path

    _shipped, user = resolve_roots(None, None)
    return user / "logs"


def configure_logging() -> None:
    """Call once at startup before any other bartlebot imports."""
    log_dir = session_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    session_name = datetime.datetime.now().strftime("bartlebot_%Y-%m-%d_%H-%M-%S.log")
    file_handler = logging.FileHandler(log_dir / session_name, encoding="utf-8")
    # The file carries the date. A session that starts at 21:49 and ends at 01:13 reads
    # as though it runs backwards without one, and a bare "%H:%M:%S" gives a line nothing
    # to be lined up against anything outside the file.
    file_handler.setFormatter(ShortNameFormatter(_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    file_handler.addFilter(GuiOnlyFilter())
    file_handler.addFilter(RepeatCollapsingFilter(file_handler))

    prune_session_logs(log_dir)

    # The widget is watched live, where the date is never in question and the column
    # would cost width the message needs, so it keeps the time alone.
    _gui_bridge.setFormatter(ShortNameFormatter(_FILE_FORMAT, datefmt="%H:%M:%S"))
    _gui_bridge.addFilter(RepeatCollapsingFilter(_gui_bridge))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(_gui_bridge)

    # Suppress chatty third-party debug output
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("wizwalker").setLevel(_WIZWALKER_LEVEL)

    logger = logging.getLogger(__name__)
    logger.info("=== BartleBot startup ===")
    logger.info("Version: %s", _distribution_version("bartlebot"))
    logger.info("Python %s", sys.version.split()[0])
    logger.info("Platform: %s", platform.platform())
    from bartlebot.packaging.build_kind import build_kind  # noqa: PLC0415

    logger.info("Build: %s", build_kind())
    for lib in HEADER_LIBRARIES:
        logger.info("%s==%s", lib, _distribution_version(lib))

    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 - read once the handlers exist

    if set_turn_trace(SettingsManager.instance().record_combat_decisions):
        logger.info("Combat decision trace on; each turn's choices are logged at DEBUG")


def _distribution_version(name: str) -> str:
    """The installed version of ``name``, or ``unknown``, said rather than left out, when no metadata is found."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def set_turn_trace(enabled: bool) -> bool:
    """Let wizsprinter's turn trace into the log when *enabled*, or in developer mode.

    It is the only record of why a turn cast what it did, but it runs to a few kilobytes
    a round for each client, so a user turns it on from Settings › Combat to look into a
    fight, and developer mode always has it. Off, the logger inherits wizwalker's level,
    which drops it. Returns whether it is now on.
    """
    from bartlebot.content.store_roots import dev_mode  # noqa: PLC0415 - kept off this module's import path

    on = enabled or dev_mode()
    logging.getLogger(TURN_TRACE_LOGGER).setLevel(logging.DEBUG if on else logging.NOTSET)
    return on


def install_excepthook() -> None:
    """Log an exception nothing caught, and carry on (#154).

    PyQt6 aborts the process on an exception that escapes a slot unless ``sys.excepthook``
    has been replaced. A packaged build has no console, so the abort left no trace at all,
    and it took every embedded game window with it. Replaced, the exception is logged at
    CRITICAL with its traceback and the app keeps running. Threads get the same, since a
    worker's uncaught exception otherwise goes only to the missing console.
    """
    log = logging.getLogger(__name__)

    def _hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        log.critical(
            "Uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def add_gui_callback(cb: Callable[[str, int], None]) -> None:
    _gui_bridge.add_callback(cb)


def remove_gui_callback(cb: Callable[[str, int], None]) -> None:
    _gui_bridge.remove_callback(cb)
