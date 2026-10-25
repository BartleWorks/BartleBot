"""Developer-only watch on the game windows: when one stops answering, what it was waiting on.

On only with ``BARTLEBOT_DEV`` set (see ``store_roots.dev_mode``). A game client can go
seconds without handling its window's messages: a friend teleport's click once waited
6.2 s for one. wizwalker now sends each window's messages from a thread of the window's
own, so only that client waits, but why the game stops answering is still open. It could
be busy, or blocked on something -- and BartleBot is a candidate, since an embedded game
window is a child of ours and shares our input queue (``win32_window.embed``).

So every ``INTERVAL`` each hooked client's window is sent ``WM_NULL`` with a short
timeout, from a worker thread. While one does not answer, Windows' wait chain traversal
(``GetThreadWaitChain``) is asked what the window's thread is waiting on: a
``SendMessage`` to another thread (named, with its process, and marked when it is this
app's GUI thread), a lock, an RPC, or nothing at all, because it is running. When the
window answers again after ``REPORT_AFTER`` or more, one WARNING says for how long, what
was seen how often, and what share of the time the thread spent on a CPU. A window is
named by its player number, never its title, which the launcher sets to the account.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import logging
import os
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: How often each window is asked.
INTERVAL = 0.5

#: How long a window has to answer before it counts as not answering.
ANSWER_WITHIN = 0.25

#: How long a window has to go unanswering before it is reported.
REPORT_AFTER = 1.0

_WM_NULL = 0x0000
_SMTO_ABORTIFHUNG = 0x0002
_THREAD_QUERY_LIMITED_INFORMATION = 0x0800
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Out of process, critical sections and network I/O; the COM flag needs COM set up first.
_WCT_FLAGS = 0x1 | 0x4 | 0x8
_WCT_MAX_NODES = 16
_WCT_OBJNAME_LENGTH = 128

#: WCT_OBJECT_TYPE, as the report names them.
WAIT_TYPES = {
    1: "critical section",
    2: "SendMessage",
    3: "mutex",
    4: "ALPC (an RPC)",
    5: "COM",
    6: "thread wait",
    7: "process wait",
    8: "thread",
    9: "COM activation",
    10: "unknown wait",
    11: "socket I/O",
    12: "SMB I/O",
}
_THREAD = 8
_RUNNING = 2
_BLOCKED = 3


class _LockObject(ctypes.Structure):
    _fields_ = [
        ("ObjectName", ctypes.c_wchar * _WCT_OBJNAME_LENGTH),
        ("Timeout", ctypes.c_longlong),
        ("Alertable", wt.BOOL),
    ]


class _ThreadObject(ctypes.Structure):
    _fields_ = [("ProcessId", wt.DWORD), ("ThreadId", wt.DWORD), ("WaitTime", wt.DWORD), ("ContextSwitches", wt.DWORD)]


class _NodeUnion(ctypes.Union):
    _fields_ = [("LockObject", _LockObject), ("ThreadObject", _ThreadObject)]


class _WaitChainNode(ctypes.Structure):  # WAITCHAIN_NODE_INFO
    _anonymous_ = ("u",)
    _fields_ = [("ObjectType", ctypes.c_int), ("ObjectStatus", ctypes.c_int), ("u", _NodeUnion)]


@dataclass(frozen=True)
class ChainNode:
    """One link of a wait chain, as much of it as the report uses."""

    kind: int
    status: int
    pid: int = 0
    tid: int = 0


def describe_chain(nodes: list[ChainNode], *, own_pid: int, gui_tid: int, exe_name: Callable[[int], str]) -> str:
    """What a window thread's wait chain says it is doing, in one line.

    The first node is the thread itself: running, or blocked on the rest of the chain,
    which alternates what is waited on and the thread that holds it.
    """
    if not nodes:
        return "no wait chain"
    head = nodes[0]
    if head.status == _RUNNING:
        return "running"
    if head.status != _BLOCKED or len(nodes) == 1:
        return "blocked on nothing Windows could name"
    parts = []
    for node in nodes[1:]:
        if node.kind != _THREAD:
            parts.append(WAIT_TYPES.get(node.kind, f"wait type {node.kind}"))
        elif node.pid == own_pid:
            parts.append(f"this app's {'GUI ' if node.tid == gui_tid else ''}thread {node.tid:#x}")
        else:
            parts.append(f"thread {node.tid:#x} of {exe_name(node.pid) or '?'} (pid {node.pid})")
    return "blocked: " + " -> ".join(parts)


@dataclass
class _Episode:
    began: float
    thread_id: int
    cpu_at_start: int | None
    seen: Counter[str] = field(default_factory=Counter)


class GameHangTracer:
    """Reports each game window that stops answering, and what it waited on. See the module docstring.

    *windows* returns ``(label, hwnd)`` for each window to watch, ``("P1", 0x1234)``.
    """

    def __init__(self, windows: Callable[[], list[tuple[str, int]]]) -> None:
        self._windows = windows
        self._episodes: dict[int, _Episode] = {}
        self._task: asyncio.Task | None = None
        self._own_pid = os.getpid()
        self._gui_tid = threading.main_thread().native_id
        self._exe_names: dict[int, str] = {}
        self._user32 = ctypes.WinDLL("user32")
        self._kernel32 = ctypes.WinDLL("kernel32")
        self._advapi32 = ctypes.WinDLL("advapi32")
        u, k, a = self._user32, self._kernel32, self._advapi32
        u.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.c_void_p]
        u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId.restype = wt.DWORD
        k.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.OpenThread.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.OpenProcess.restype = wt.HANDLE
        k.GetThreadTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
        k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k.CloseHandle.argtypes = [wt.HANDLE]
        a.OpenThreadWaitChainSession.argtypes = [wt.DWORD, ctypes.c_void_p]
        a.OpenThreadWaitChainSession.restype = wt.HANDLE
        a.GetThreadWaitChain.argtypes = [
            wt.HANDLE,
            ctypes.c_size_t,
            wt.DWORD,
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(_WaitChainNode),
            ctypes.POINTER(wt.BOOL),
        ]
        a.CloseThreadWaitChainSession.argtypes = [wt.HANDLE]

    def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._watch())
        logger.info("Game hang tracer on (BARTLEBOT_DEV); a game window that stops answering is logged here")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(INTERVAL)
            try:
                windows = list(self._windows())
                answers = await asyncio.gather(*[asyncio.to_thread(self._probe, hwnd) for _, hwnd in windows])
            except Exception:
                logger.debug("Game hang tracer could not ask the windows", exc_info=True)
                continue
            now = time.perf_counter()
            for (label, hwnd), sample in zip(windows, answers):
                self._note(label, hwnd, sample, now)
            for gone in set(self._episodes) - {hwnd for _, hwnd in windows}:
                del self._episodes[gone]

    def _note(self, label: str, hwnd: int, sample: tuple[int, str] | None, now: float) -> None:
        """Record one answer: None, or the window thread's id and what it was doing."""
        episode = self._episodes.get(hwnd)
        if sample is not None:
            tid, doing = sample
            if episode is None:
                # It last answered about an interval ago; count from the ask that went unanswered.
                episode = self._episodes[hwnd] = _Episode(now - ANSWER_WITHIN, tid, self._cpu_time(tid))
            episode.seen[doing] += 1
            return
        if episode is None:
            return
        del self._episodes[hwnd]
        held = now - episode.began
        if held < REPORT_AFTER:
            return
        cpu = self._cpu_time(episode.thread_id)
        busy = (
            f"; on a CPU {min(100.0, (cpu - episode.cpu_at_start) / 1e7 / held * 100):.0f}% of the time"
            if cpu is not None and episode.cpu_at_start is not None
            else ""
        )
        seen = ", ".join(f"{doing} ({count}x)" for doing, count in episode.seen.most_common())
        logger.warning("%s's game did not answer for %.1fs; its window thread was %s%s", label, held, seen, busy)

    # -- Win32, on worker threads -----------------------------------------------

    def _probe(self, hwnd: int) -> tuple[int, str] | None:
        """None if the window answered in time; else its thread and what that thread is doing."""
        result = ctypes.c_size_t()
        if self._user32.SendMessageTimeoutW(
            hwnd, _WM_NULL, 0, 0, _SMTO_ABORTIFHUNG, int(ANSWER_WITHIN * 1000), ctypes.byref(result)
        ):
            return None
        tid = self._user32.GetWindowThreadProcessId(hwnd, None)
        return tid, describe_chain(
            self._wait_chain(tid), own_pid=self._own_pid, gui_tid=self._gui_tid, exe_name=self._exe_name
        )

    def _wait_chain(self, tid: int) -> list[ChainNode]:
        session = self._advapi32.OpenThreadWaitChainSession(0, None)
        if not session:
            return []
        try:
            nodes = (_WaitChainNode * _WCT_MAX_NODES)()
            count = wt.DWORD(_WCT_MAX_NODES)
            cycle = wt.BOOL()
            if not self._advapi32.GetThreadWaitChain(
                session, 0, _WCT_FLAGS, tid, ctypes.byref(count), nodes, ctypes.byref(cycle)
            ):
                return []
            return [
                ChainNode(n.ObjectType, n.ObjectStatus, n.ThreadObject.ProcessId, n.ThreadObject.ThreadId)
                if n.ObjectType == _THREAD
                else ChainNode(n.ObjectType, n.ObjectStatus)
                for n in nodes[: count.value]
            ]
        finally:
            self._advapi32.CloseThreadWaitChainSession(session)

    def _cpu_time(self, tid: int) -> int | None:
        """The thread's kernel plus user time, in 100 ns units, or None if it cannot be read."""
        handle = self._kernel32.OpenThread(_THREAD_QUERY_LIMITED_INFORMATION, False, tid)
        if not handle:
            return None
        try:
            times = [wt.FILETIME() for _ in range(4)]
            if not self._kernel32.GetThreadTimes(handle, *[ctypes.byref(t) for t in times]):
                return None
            return sum((t.dwHighDateTime << 32) | t.dwLowDateTime for t in times[2:])
        finally:
            self._kernel32.CloseHandle(handle)

    def _exe_name(self, pid: int) -> str:
        if pid in self._exe_names:
            return self._exe_names[pid]
        name = ""
        handle = self._kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                size = wt.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    name = os.path.basename(buffer.value)
            finally:
                self._kernel32.CloseHandle(handle)
        self._exe_names[pid] = name
        return name
