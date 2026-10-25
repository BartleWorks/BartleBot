"""Developer-only timeline of a run, for finding where its time goes.

On only with ``BARTLEBOT_DEV`` set (see ``store_roots.dev_mode``), and only while a bot
runs: ``start`` at the top of ``AppController._start_bot``, ``stop`` in its ``finally``.
Each run writes one JSON-lines file to ``logs/trace/``, which
``tools/run_trace_report.py`` summarizes. Five kinds of record:

- **span** -- a named stretch of work, written when it ends: a pre-run step, a node, a
  combat round and its phases. Spans nest through a context variable, so a task started
  inside one (``gather``, ``create_task``) belongs to it, and a span with no player of its
  own takes its parent's. Each counts the memory reads made while it was the innermost
  span, and how long they took.
- **key**, **hotkey** and **click** -- every input sent to a client, with the BartleBot
  function that sent it (``caller``) and, when it went through a wizwalker extension such
  as ``DeckBuilder`` or wizsprinter, the extension function (``via``). A window opened,
  closed and opened again is two presses of one key from two callers.
- **walk** -- a search of a client's whole UI tree (``get_windows_with_name`` and its
  siblings), the costliest read there is: thousands of ``ReadProcessMemory`` calls, all
  synchronous on the event loop.
- **stall** -- the event loop held for longer than ``STALL_THRESHOLD`` by something
  synchronous. Nothing else runs meanwhile: not another client's combat, not the GUI.
- **tick** -- once a second, the reads made outside any span (the GUI's refresh, the
  machine's watchers), so the background's cost shows too.

Every hook is installed on the wizwalker class when a trace starts and removed when it
stops, so a run with no trace pays nothing. Records name a player by slot, never by name,
and log no text read from the game.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime
import json
import logging
import sys
import time
from collections.abc import Callable, Coroutine
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How often the heartbeat wakes to measure the event loop's lag.
HEARTBEAT_INTERVAL = 0.05

#: A lag past this is written as a stall. Windows' timer tick is ~15.6 ms, so a few tens of
#: milliseconds of lag is the scheduler, not a stall.
STALL_THRESHOLD = 0.1

#: How often buffered records are written out, and the background reads counted.
FLUSH_INTERVAL = 1.0

#: Trace files kept, as session logs keep ten.
KEEP_TRACES = 10


@dataclass
class _Span:
    id: int
    parent: int | None
    name: str
    slot: int | None
    start: float
    attrs: dict[str, Any] = field(default_factory=dict)
    reads: int = 0
    read_s: float = 0.0


_SPAN: contextvars.ContextVar[_Span | None] = contextvars.ContextVar("run_trace_span", default=None)

# Set while inside a hooked call that makes others of its own kind, so only the outer one
# is recorded: get_windows_with_name searches through get_windows_with_predicate, and
# click_window clicks through click.
_IN_WALK: contextvars.ContextVar[bool] = contextvars.ContextVar("run_trace_in_walk", default=False)
_IN_CLICK: contextvars.ContextVar[bool] = contextvars.ContextVar("run_trace_in_click", default=False)


class RunTrace:
    """One run's trace file, and the counters its hooks feed."""

    def __init__(self, path: Path, slot_of: Callable[[Any], int | None]):
        self.path = path
        self._slot_of = slot_of
        self._t0 = time.perf_counter()
        self._next_id = 1
        self._buffer: list[str] = []
        self._file = path.open("w", encoding="utf-8")
        self._loose_reads = 0
        self._loose_read_s = 0.0
        self._heartbeat: asyncio.Task | None = None

    def now(self) -> float:
        """Seconds since the trace started."""
        return time.perf_counter() - self._t0

    def slot(self, client) -> int | None:
        if client is None:
            return None
        try:
            return self._slot_of(client)
        except Exception:
            return None

    def write(self, record: dict) -> None:
        self._buffer.append(json.dumps(record, separators=(",", ":"), default=str))

    def flush(self) -> None:
        if self._buffer:
            self._file.write("\n".join(self._buffer) + "\n")
            self._file.flush()
            self._buffer.clear()

    def new_id(self) -> int:
        span_id, self._next_id = self._next_id, self._next_id + 1
        return span_id

    def count_read(self, seconds: float) -> None:
        span = _SPAN.get()
        if span is None:
            self._loose_reads += 1
            self._loose_read_s += seconds
        else:
            span.reads += 1
            span.read_s += seconds

    def event(self, kind: str, start: float, client=None, **fields) -> None:
        """Write an input or walk that began at *start* (``perf_counter``) and has just ended."""
        span = _SPAN.get()
        slot = self.slot(client)
        record = {
            "kind": kind,
            "t": round(start - self._t0, 4),
            "dur": round(time.perf_counter() - start, 4),
            "slot": slot if slot is not None else (span.slot if span else None),
            "span": span.id if span else None,
        }
        record.update(fields)
        self.write(record)

    def end_span(self, span: _Span) -> None:
        self.write(
            {
                "kind": "span",
                "id": span.id,
                "parent": span.parent,
                "name": span.name,
                "slot": span.slot,
                "t": round(span.start - self._t0, 4),
                "dur": round(time.perf_counter() - span.start, 4),
                "reads": span.reads,
                "read_s": round(span.read_s, 4),
                **({"attrs": span.attrs} if span.attrs else {}),
            }
        )

    async def run_heartbeat(self) -> None:
        last_flush = time.perf_counter()
        while True:
            before = time.perf_counter()
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = time.perf_counter()
            lag = now - before - HEARTBEAT_INTERVAL
            if lag >= STALL_THRESHOLD:
                self.write(
                    {"kind": "stall", "t": round(before + HEARTBEAT_INTERVAL - self._t0, 4), "dur": round(lag, 4)}
                )
            if now - last_flush >= FLUSH_INTERVAL:
                self.write(
                    {
                        "kind": "tick",
                        "t": round(now - self._t0, 4),
                        "reads": self._loose_reads,
                        "read_s": round(self._loose_read_s, 4),
                    }
                )
                self._loose_reads, self._loose_read_s = 0, 0.0
                self.flush()
                last_flush = now

    def close(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
        self.flush()
        self._file.close()


_trace: RunTrace | None = None
_restores: list[Callable[[], None]] = []


def active() -> bool:
    return _trace is not None


def trace_dir() -> Path:
    from bartlebot.logging_setup import session_log_dir  # noqa: PLC0415

    return session_log_dir() / "trace"


def start(slot_of: Callable[[Any], int | None], directory: Path | None = None) -> Path | None:
    """Begin a trace of the run about to start, in developer mode only. Returns its file.

    *slot_of* gives a client's player number, or None for one not in the run. Call from
    the event loop: the heartbeat is a task on it. A trace already running is stopped first.
    """
    from bartlebot.content.store_roots import dev_mode  # noqa: PLC0415

    global _trace
    if not dev_mode():
        return None
    stop()
    directory = directory or trace_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _prune(directory)
        stamp = datetime.datetime.now().strftime("run_%Y-%m-%d_%H-%M-%S.jsonl")
        _trace = RunTrace(directory / stamp, slot_of)
    except OSError:
        logger.warning("Could not start the run trace", exc_info=True)
        return None
    _trace.write({"kind": "start", "wall": datetime.datetime.now().isoformat(timespec="milliseconds")})
    _install_hooks()
    _trace._heartbeat = asyncio.get_running_loop().create_task(_trace.run_heartbeat())
    logger.info("Run trace: %s", _trace.path)
    return _trace.path


def stop() -> None:
    """End the trace, if one is running, and take the hooks back out."""
    global _trace
    while _restores:
        _restores.pop()()
    if _trace is None:
        return
    trace, _trace = _trace, None
    try:
        trace.close()
    except OSError:
        logger.warning("Could not finish the run trace", exc_info=True)


def _prune(directory: Path) -> None:
    traces = sorted(directory.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in traces[KEEP_TRACES - 1 :]:
        try:
            old.unlink()
        except OSError:
            pass


class _SpanContext(AbstractContextManager):
    def __init__(self, trace: RunTrace, name: str, slot: int | None, attrs: dict):
        self._trace = trace
        self._name = name
        self._slot = slot
        self._attrs = attrs
        self._token = None
        self.span: _Span | None = None

    def __enter__(self) -> _Span:
        parent = _SPAN.get()
        slot = self._slot if self._slot is not None else (parent.slot if parent else None)
        self.span = _Span(
            self._trace.new_id(), parent.id if parent else None, self._name, slot, time.perf_counter(), self._attrs
        )
        self._token = _SPAN.set(self.span)
        return self.span

    def __exit__(self, exc_type, exc, tb) -> None:
        _SPAN.reset(self._token)
        if exc_type is not None:
            self.span.attrs["error"] = exc_type.__name__
        # A trace stopped while this span was open has closed its file.
        if _trace is self._trace:
            self._trace.end_span(self.span)


def span(name: str, *, client=None, slot: int | None = None, **attrs) -> AbstractContextManager:
    """A span around the ``with`` block, or nothing at all while no trace runs.

    The player is *slot*, else *client*'s, else the enclosing span's. *attrs* go into the
    record as they are, so pass numbers and ids, never text read from the game.
    """
    trace = _trace
    if trace is None:
        return nullcontext()
    if slot is None and client is not None:
        slot = trace.slot(client)
    return _SpanContext(trace, name, slot, attrs)


def note(**attrs) -> None:
    """Add *attrs* to the innermost open span, as facts learned while it runs."""
    current = _SPAN.get()
    if current is not None and _trace is not None:
        current.attrs.update(attrs)


async def traced(coro: Coroutine, name: str, **kwargs) -> Any:
    """Await *coro* inside a span, for a task that should be one from its first step."""
    with span(name, **kwargs):
        return await coro


def caller() -> tuple[str | None, str | None]:
    """The BartleBot function that led here, and the wizwalker extension functions in between.

    Both ends of the extension's part, outermost first: ``sprinty_combat.handle_round:2924 >
    sprinty_combat.SprintyCombat.get_card_counts.<locals>._inner:1156``. The outer end
    alone is only the entry point, and the inner end alone is often a generic retry helper
    (``_inner``, ``_try_do``), so the qualified name and the pair are what place it.
    """
    found = None
    extension: list[str] = []  # innermost first
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module == __name__:
            pass
        elif module.startswith("bartlebot."):
            found = f"{module.removeprefix('bartlebot.')}.{frame.f_code.co_name}:{frame.f_lineno}"
            break
        elif module.startswith("wizwalker.extensions."):
            extension.append(
                f"{module.removeprefix('wizwalker.extensions.')}.{frame.f_code.co_qualname}:{frame.f_lineno}"
            )
        frame = frame.f_back
    if not extension:
        return found, None
    return found, extension[0] if len(extension) == 1 else f"{extension[-1]} > {extension[0]}"


# --- The hooks ---------------------------------------------------------------------


def _patch(owner, name: str, make: Callable[[Callable], Callable]) -> None:
    original = owner.__dict__[name]
    setattr(owner, name, make(original))
    _restores.append(lambda: setattr(owner, name, original))


def _install_hooks() -> None:
    from wizwalker.client import Client  # noqa: PLC0415
    from wizwalker.combat.card import CombatCard  # noqa: PLC0415
    from wizwalker.memory.memory_objects.window import Window  # noqa: PLC0415
    from wizwalker.memory.memory_reader import MemoryReader  # noqa: PLC0415
    from wizwalker.mouse_handler import MouseHandler  # noqa: PLC0415

    _patch(MemoryReader, "read_bytes", _hook_read)
    _patch(Window, "get_windows_with_name", lambda original: _hook_walk(original, "name"))
    _patch(Window, "get_windows_with_type", lambda original: _hook_walk(original, "type"))
    _patch(Window, "get_windows_with_predicate", lambda original: _hook_walk(original, None))
    _patch(Client, "send_key", _hook_send_key)
    _patch(Client, "send_hotkey", _hook_send_hotkey)
    _patch(MouseHandler, "click_window", _hook_click_window)
    _patch(MouseHandler, "click", _hook_click)
    _patch(CombatCard, "cast", _hook_cast)


def _hook_read(original):
    async def read_bytes(self, address: int, size: int) -> bytes:
        start = time.perf_counter()
        try:
            return await original(self, address, size)
        finally:
            trace = _trace
            if trace is not None:
                trace.count_read(time.perf_counter() - start)

    return read_bytes


def _hook_walk(original, by: str | None):
    async def walk(self, arg):
        trace = _trace
        if trace is None or _IN_WALK.get():
            return await original(self, arg)
        token = _IN_WALK.set(True)
        start = time.perf_counter()
        found = None
        try:
            found = await original(self, arg)
            return found
        finally:
            _IN_WALK.reset(token)
            if _trace is trace:
                where, via = caller()
                fields = {by: arg} if by else {}
                hook_client = getattr(getattr(self, "hook_handler", None), "client", None)
                trace.event(
                    "walk",
                    start,
                    hook_client,
                    found=len(found) if found is not None else None,
                    caller=where,
                    via=via,
                    **fields,
                )

    return walk


def _key_name(key) -> str:
    return getattr(key, "name", None) or str(key)


def _hook_send_key(original):
    async def send_key(self, key, seconds: float = 0):
        trace = _trace
        start = time.perf_counter()
        try:
            return await original(self, key, seconds)
        finally:
            if trace is not None and _trace is trace:
                where, via = caller()
                extra = {"hold": seconds} if seconds else {}
                trace.event("key", start, self, key=_key_name(key), caller=where, via=via, **extra)

    return send_key


def _hook_send_hotkey(original):
    async def send_hotkey(self, modifiers, key):
        trace = _trace
        start = time.perf_counter()
        try:
            return await original(self, modifiers, key)
        finally:
            if trace is not None and _trace is trace:
                where, via = caller()
                keys = "+".join([*(_key_name(m) for m in modifiers), _key_name(key)])
                trace.event("hotkey", start, self, keys=keys, caller=where, via=via)

    return send_hotkey


def _hook_click_window(original):
    async def click_window(self, window, **kwargs):
        trace = _trace
        if trace is None:
            return await original(self, window, **kwargs)
        token = _IN_CLICK.set(True)
        start = time.perf_counter()
        try:
            return await original(self, window, **kwargs)
        finally:
            _IN_CLICK.reset(token)
            if _trace is trace:
                try:
                    # A window's name is the game's identifier for it, never text a player wrote.
                    name = await window.name()
                except Exception:
                    name = None
                where, via = caller()
                trace.event("click", start, self.client, window=name, caller=where, via=via)

    return click_window


def _hook_click(original):
    async def click(self, x: int, y: int, **kwargs):
        trace = _trace
        start = time.perf_counter()
        try:
            return await original(self, x, y, **kwargs)
        finally:
            if trace is not None and _trace is trace and not _IN_CLICK.get():
                where, via = caller()
                trace.event("click", start, self.client, at=[x, y], caller=where, via=via)

    return click


def _hook_cast(original):
    async def cast(self, target, **kwargs):
        # The target's kind (CombatCard, CombatMember, list, NoneType): an enchant is cast onto a card.
        with span("combat.cast", onto=type(target).__name__):
            return await original(self, target, **kwargs)

    return cast
