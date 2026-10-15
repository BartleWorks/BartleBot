"""Checking a combat DSL without holding up the window.

A check is a full Earley parse (``combat_runner.dsl_check``): up to 800 ms for the longest
shipped strategy, on whatever thread asks. Cached answers come back at once, from memory
or from disk, and those are nearly all of them -- but a strategy the user has just written
or imported, or every strategy after a wizsprinter bump, has none yet, and asking for it
on the GUI thread froze the window: once per card while a list was drawn, and once per
*keystroke* while typing in a DSL box, because every edit redraws the strategy's card.

So a surface asks here instead. :func:`checker` runs checks away from the GUI and says, on
the GUI thread, when each is known; :class:`DeferredDslChecks` is how a surface drawing
cards uses it -- draw every card from what is already known, treat the rest as fine for
now, and redraw the cards that turn out not to parse once that is known.

WHY A PROCESS, NOT A THREAD

The parse is pure Python, so a worker *thread* holds the interpreter lock for as long as
it parses -- and the GUI thread, which gives the lock up at every file read and ``stat``,
then waits up to the switch interval to get it back each time. Measured on the strategy
dialog with nothing cached: no parse on the GUI thread at all, and the dialog took 5.4 s
to build instead of 0.8 s, because building it reads the strategy folders. A separate
process shares no lock. One, started on first use and kept: it costs a second or so to
start, in the background, and its imports stay loaded for the next check.

The child only parses (``combat_runner.check_dsl_uncached``); the answer comes back here
and is stored in this process's caches, so the two never write the cache file at once.
If the process cannot be had -- it failed to start, or died -- checks fall back to a
thread, which is slower for the GUI but still never parses on it.
"""

from __future__ import annotations

import logging
import multiprocessing
from collections.abc import Callable, Hashable
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from bartlebot.combat.strategy_summary import Validate
from bartlebot.nodes.combat_runner import DslCheck, check_dsl_uncached, known_dsl_check, store_dsl_check

logger = logging.getLogger(__name__)


class DslChecker(QObject):
    """Checks DSLs in a child process. Lives for the life of the app; see :func:`checker`."""

    #: A DSL whose check is now known, so ``known_dsl_check`` answers it. GUI thread.
    checked = pyqtSignal(str)
    #: Emitted from the executor's own thread with the future and the executor it ran on;
    #: queued to the GUI thread, which stores the answer.
    _finished = pyqtSignal(str, object)

    def __init__(self, executor: Executor | None = None) -> None:
        super().__init__()
        self._executor = executor
        self._pending: set[str] = set()
        self._finished.connect(self._on_finished)

    def request(self, dsl: str) -> DslCheck | None:
        """The check for *dsl* if it is known now; otherwise None, and ``checked`` follows."""
        known = known_dsl_check(dsl)
        if known is not None:
            return known
        if dsl not in self._pending:
            self._pending.add(dsl)
            self._submit(dsl)
        return None

    def is_pending(self, dsl: str) -> bool:
        return dsl in self._pending

    def _submit(self, dsl: str) -> None:
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        try:
            future = self._executor.submit(check_dsl_uncached, dsl)
        except (BrokenProcessPool, RuntimeError, OSError):
            logger.warning("The DSL check process is unavailable; checking on a thread instead", exc_info=True)
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dsl-check")
            future = self._executor.submit(check_dsl_uncached, dsl)
        executor = self._executor
        future.add_done_callback(lambda done, dsl=dsl: self._finished.emit(dsl, (done, executor)))

    def _on_finished(self, dsl: str, ran: tuple[Future, Executor]) -> None:
        future, executor = ran
        try:
            store_dsl_check(dsl, future.result())
        except BrokenProcessPool:
            # The child died: start over on a thread, and check this one again there. Every
            # check still waiting on the child lands here too, and they share one thread.
            if self._executor is executor:
                logger.warning("The DSL check process stopped; checking on a thread instead", exc_info=True)
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dsl-check")
            self._submit(dsl)
            return
        except Exception:
            # check_dsl_uncached reports a bad DSL as an answer; anything raised is a bug.
            logger.exception("Checking a DSL in the background raised")
        self._pending.discard(dsl)
        self.checked.emit(dsl)


_checker: DslChecker | None = None


def checker() -> DslChecker:
    """The one checker. Made on first use, which must be on the GUI thread."""
    global _checker
    if _checker is None:
        _checker = DslChecker()
    return _checker


class DeferredDslChecks:
    """A surface's view of the checks: what is known now, and which cards to redraw later.

    While drawing a card, pass ``validator(key)`` to ``summarize_duel`` or
    ``summarize_player``, *key* naming the card. A DSL with no answer yet reads as valid
    and is remembered against that key. :meth:`request_unknown` then checks every one of
    them in the background -- and so does any other surface checking the same text, the
    DSL editor's own debounce included. When one turns out not to parse, *on_broken* runs
    on the GUI thread with the keys of the cards drawn from it, coalesced into one call.
    One that parses changes nothing on a card, so it costs no redraw.
    """

    def __init__(self, owner: QObject, on_broken: Callable[[set[Hashable]], None]) -> None:
        self._on_broken = on_broken
        #: DSL -> the keys of the cards drawn as if it parsed.
        self._unknown: dict[str, set[Hashable]] = {}
        self._broken_keys: set[Hashable] = set()
        self._timer = QTimer(owner)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._flush)
        # Disconnected by hand when the owner goes: the checker outlives every surface,
        # and this object is not a QObject for Qt to disconnect on its own.
        self._connection = checker().checked.connect(self._on_checked)
        owner.destroyed.connect(self._disconnect)

    def validator(self, key: Hashable = None) -> Validate:
        """A ``validate`` for the card named *key*: the parse error if known, else None."""

        def validate(dsl: str) -> str | None:
            known = known_dsl_check(dsl)
            if known is None:
                self._unknown.setdefault(dsl, set()).add(key)
                return None
            return known.error.message if known.error is not None else None

        return validate

    def reset(self) -> None:
        """Forget what was drawn: call before redrawing every card."""
        self._unknown.clear()
        self._broken_keys.clear()

    def request_unknown(self) -> None:
        """Check, in the background, every DSL a card was drawn without an answer for."""
        for dsl in list(self._unknown):
            if checker().request(dsl) is not None:
                # Known since the card was drawn: another surface checked it meanwhile.
                self._on_checked(dsl)

    @property
    def unknown(self) -> frozenset[str]:
        """The DSLs drawn as valid that are not known to be yet."""
        return frozenset(self._unknown)

    def _on_checked(self, dsl: str) -> None:
        keys = self._unknown.pop(dsl, None)
        if keys is None:
            return
        known = known_dsl_check(dsl)
        if known is not None and known.error is not None:
            self._broken_keys |= keys
            self._timer.start()

    def _flush(self) -> None:
        keys, self._broken_keys = self._broken_keys, set()
        if keys:
            self._on_broken(keys)

    def _disconnect(self) -> None:
        try:
            checker().checked.disconnect(self._connection)
        except (TypeError, RuntimeError):
            pass
