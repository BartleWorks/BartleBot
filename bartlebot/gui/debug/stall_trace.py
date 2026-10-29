"""Developer-only watchdog for a frozen window: what held the event loop, caught while it held.

On only with ``BARTLEBOT_DEV`` set (see ``store_roots.dev_mode``), for the whole session.
The run trace (``bot/run_trace.py``) measures stalls too, but only during a run, and only
once a stall is over, when whatever held the loop has returned and left nothing behind to
say what it was. A freeze outside a run -- the window opened maximized, TP to player from
the editor -- was measured by nothing at all.

So this keeps :func:`faulthandler.dump_traceback_later` armed a little past the next
heartbeat. A heartbeat that arrives on time re-arms it, which cancels the last; one held
``DUMP_AFTER`` past its time lets it fire. faulthandler's timer is a thread of its own,
written in C, so it needs neither the GIL nor the stuck thread, and the stacks it writes
are every Python thread's *during* the freeze. A main thread whose stack ends in qasync's
``run_forever``, with no BartleBot frame above it, is held in Qt's or Windows' native code
rather than in ours.

Each session writes ``logs/stalls/stalls_<time>.txt``: faulthandler's dump, then a line
with the wall time and how long the loop was held, written once it lets go, when a
WARNING in the session log names the length and the file too. A session that never froze
leaves no file. A dump is file names, line numbers and function names, never a value, so
no player data reaches it.
"""

from __future__ import annotations

import asyncio
import datetime
import faulthandler
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: How often the heartbeat wakes.
HEARTBEAT_INTERVAL = 0.25

#: How long past a heartbeat's time the loop has to be held before the stacks are dumped.
#: Long enough that start-up's ordinary work, and a run's sub-second stalls, dump nothing.
DUMP_AFTER = 1.0

#: Stall files kept, as session logs keep ten.
KEEP_FILES = 10


def stall_dir() -> Path:
    from bartleby.qt.app.logging_setup import session_log_dir  # noqa: PLC0415 - kept off this module's import path

    return session_log_dir() / "stalls"


class StallTracer:
    """Dumps every thread's stack whenever the event loop is held. See the module docstring.

    faulthandler has one timer per process, so only one tracer may be running at a time.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        interval: float = HEARTBEAT_INTERVAL,
        dump_after: float = DUMP_AFTER,
    ) -> None:
        self._directory = directory or stall_dir()
        self._interval = interval
        self._dump_after = dump_after
        self._path: Path | None = None
        self._file = None
        self._task: asyncio.Task | None = None
        self._armed_at = 0.0

    @property
    def path(self) -> Path | None:
        """This session's stall file, once started."""
        return self._path

    def start(self) -> None:
        """Open the session's file and arm the timer, then keep re-arming it from the loop.

        Armed here, not only from the heartbeat, so a freeze before the loop first gets
        round to the heartbeat is caught as well. Call with the app's loop set, running
        or about to run.
        """
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            _prune(self._directory)
            self._path = self._directory / datetime.datetime.now().strftime("stalls_%Y-%m-%d_%H-%M-%S.txt")
            self._file = self._path.open("w", encoding="utf-8")
        except OSError:
            logger.warning("Could not start the stall tracer", exc_info=True)
            return
        self._arm()
        self._task = asyncio.get_event_loop().create_task(self._watch())
        logger.info("Stall tracer on (BARTLEBOT_DEV); the stacks of a held event loop go to %s", self._path)

    def stop(self) -> None:
        """Disarm the timer and close the file, deleting it if nothing was ever held."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._file is None:
            return
        faulthandler.cancel_dump_traceback_later()
        self._file.close()
        self._file = None
        try:
            if self._path.stat().st_size == 0:
                self._path.unlink()
        except OSError:
            pass

    def _arm(self) -> None:
        faulthandler.dump_traceback_later(self._interval + self._dump_after, file=self._file)
        self._armed_at = time.perf_counter()

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            held = time.perf_counter() - self._armed_at - self._interval
            if held >= self._dump_after:
                self._record(held)
            self._arm()

    def _record(self, held: float) -> None:
        """Close off the dump the timer wrote while the loop was held, and say so in the log."""
        end = datetime.datetime.now()
        begin = end - datetime.timedelta(seconds=held)
        try:
            self._file.write(
                f"--- The event loop was held for {held:.1f}s, from {_clock(begin)} to {_clock(end)}; "
                f"the stacks above were taken {self._dump_after:.1f}s in.\n\n"
            )
            self._file.flush()
        except OSError:
            pass
        logger.warning("Event loop held for %.1fs; the stacks taken during it are in %s", held, self._path)


def _clock(moment: datetime.datetime) -> str:
    """*moment* as the session log writes it: to the millisecond."""
    return moment.strftime("%H:%M:%S.%f")[:-3]


def _prune(directory: Path) -> None:
    files = sorted(directory.glob("stalls_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_FILES - 1 :]:
        try:
            old.unlink()
        except OSError:
            pass
