"""Diagnostic: measure the chat log string, and process cost, on every running client.

WHY THIS EXISTS
    The end-of-fight lag work -- the game client lags harder at end of fight the longer a bot has been
    running, "truly horrendous" after eight hours. A nine-hour log measured the
    degradation (median fight 23.0s -> 29.0s on the same boss) and ruled out our event
    loop: every state bounded by a fixed sleep stayed flat to two decimals, which a
    congested asyncio loop could not do. So the growing cost is inside the game process.

    A natural experiment then pointed at the chat log. After an overnight run with all
    four clients lagging, two clients were relogged to a different character and back
    -- which clears the chat window -- and two were left alone. BartleBot itself was
    restarted for all four. Only the relogged pair recovered. That rules out our process
    state entirely and leaves something the game accumulates per session.

    A character swap resets far more than chat (world, entities, UI, connection), so it
    does not isolate the cause on its own. This probe measures the one candidate that is
    directly readable.

WHAT THE FIRST RUN FOUND -- THE CHAT LOG IS NOT IT (and see below: nor is its cap)
    The chat log is a single MSVC std::wstring behind one ControlText -- see
    Window.maybe_text(), which reads a length at +712/+736 and one contiguous character
    buffer. Not a ring buffer, not a list of rows. The hypothesis was that it is never
    trimmed, so an eight-hour farm appends every drop announcement into it and both the
    game's text layout and our drop-detection reads scale with its length.

    Measured across four clients -- two relogged (chat cleared), two left running all
    night -- it is FALSE. The two untouched clients sat at 16,360 and 16,366 characters,
    170 and 171 lines. That is a hard cap of about 16 KB, near enough 16,384 to be the
    real number, and it turns over roughly every five fights at thirty drops each. The
    chat log cannot grow, so it cannot be a cost that grows with session length.

    Chat length does still correlate with the lag (3 and 12 lines on the recovered pair,
    170 on the degraded pair) -- but only because a small chat log is a *marker* of having
    relogged, which resets everything else too. 16 KB cannot cost five seconds.

    The second run ruled out the world counts as well: entities were 32 on all four
    clients, and window-tree sizes did not sort by health at all (449 and 406 on the two
    degraded clients, 397 and 434 on the two healthy ones). Handle counts agree to within
    nine across every sample taken.

WORKING-SET TRIM IS NOT IT EITHER
    A 2x working-set split briefly looked decisive -- the lagging clients held ~300 MB
    against ~700 MB on the healthy pair, which is the shape of pages having been trimmed
    away. It did not survive the next sample. All four clients now read 68-71% trimmed,
    healthy and lagging alike, and the healthy pair had fallen to 340 and 373 MB. The
    split was recency of reload, nothing more. Trim level is a precondition all four
    share, not the discriminator.

WHAT IS ACTUALLY HAPPENING: A FAULT STORM AT END OF FIGHT
    Sampling every 15s across a hand-fought duel, in the one window where the fight
    resolved (chat logs changed on all four clients):

        [F2P]     lagging   7,019,214 faults    working set +5.7 MB
        [Stella]  lagging   3,937,807 faults    working set +2.7 MB
        [Emma]    healthy       5,825 faults    working set +2.4 MB
        [Jeff]    healthy       3,847 faults    working set +1.7 MB

    Three orders of magnitude, on exactly the two lagging clients, at exactly the moment
    the issue describes. Two details make it more than a big number:

    * `private_bytes` is EXACTLY constant for every client across every sample. These are
      not new allocations; the same committed pages are being faulted repeatedly.
    * The working set barely moves through the storm. Pages come in and are taken straight
      back out. F2P's peak is ~287,000 pages, so 7.02M faults is each page faulting ~24
      times in fifteen seconds.

    That is thrashing in the strict sense, and at roughly 0.5-1 us per soft fault it costs
    3.5-7 seconds -- which is the end-of-fight lag work's "upward of 5 seconds on fight end", arrived at from
    the other direction.

    It replicated over a 12-fight bot run, and again over 4 more: ~4.6M-6.7M faults per
    fight on the degraded pair against ~11,000-24,500 on the healthy pair. The per-fight
    cost is stable, and it accounts for the whole 23.0s -> 29.0s degradation in the
    overnight log. Because a fight ends when the SLOWEST client is done, two degraded
    clients out of four set the pace for all four.

THE CHAT CAP WAS A COINCIDENCE TOO
    The last standing correlate was that both degraded clients sat pegged at the 16 KB
    chat cap while both healthy ones were below it -- the theory being that a log at its
    cap must trim and re-lay-out on every append. [Emma] then crossed the cap on her own,
    with no relog and nothing else reset, reaching 170 lines / 16,292 chars. Her cost
    stayed at 12,761 faults per fight, LOWER than her 24,525 before the cap. Dead.

    That is the third discriminator in a row that turned out to be a marker of "was this
    client relogged" rather than a cause. What is left is session age itself: the two
    degraded clients have been logged into the same character since the overnight run, and
    the two healthy ones were relogged that morning.

    Nothing observable distinguishes them -- not chat, entities, windows, working set,
    private bytes, handles or GDI. The shape that fits is heap locality decaying inside
    the game over hours: the same committed pages, scattered worse and worse, so end of
    fight touches far more of them and each gets trimmed between touches.

    --pin-working-set tests that from the other end. If these are pure soft faults on
    already-committed pages, a hard minimum working set leaves nothing to fault on and the
    storm should stop regardless of what causes the poor locality. That would be a
    mitigation without needing to identify the accumulation.

HOW *NOT* TO TEST THE PIN
    The first attempt pinned three of four freshly launched clients and farmed for a
    sitting. Every client came back clean: trim 0%, working set equal to peak, 27,000-65,000
    faults across the run, pinned and unpinned alike. That looks like a pass and means
    nothing. Nothing was being trimmed, so there was nothing for a pin to prevent, and the
    unpinned control was equally clean.

    Degradation has never been reproduced in a single sitting. It took an overnight run to
    appear, and it did not show up across ~30 fights of attended daytime farming. Fight
    count is not the driver, or these runs would have triggered it.

    So the pin can only be judged against a run long enough for an UNPINNED control to
    degrade. Pin some clients, leave at least one alone, run overnight, and compare
    faults-per-fight in the morning. Anything shorter cannot distinguish "the pin worked"
    from "nothing was wrong yet".

    Worth noting for that run: unattended is not the same condition as attended. Windows
    trims non-foreground processes harder on an idle machine, which is both a plausible
    reason the effect only appears overnight and exactly what a hard minimum prevents.

WHAT IT DOES
    Per client: chat log length, line count and read cost; entity-list and window-tree
    sizes; and working set, peak working set, percent trimmed, page-fault delta, handles
    and GDI/USER. --no-world skips the two walks, which are the slow part.

    Read-only EXCEPT for --pin-working-set, which changes OS state for another process and
    keeps that change until the process exits. --unpin reverses it. Nothing here ever
    writes to the game's memory; the pin path does not even hook.

    A WARNING ABOUT PINNING, PAID FOR THE HARD WAY
    A client crashed during an Unhook All that followed a pin. The pin was not proven to be
    the cause and there is a strong independent candidate -- HookHandler._rewrite_autobot
    restores the original bytes after a FIXED `asyncio.sleep(0.5)`, commented "Give some
    time for execution point to leave hooks", which is an unsynchronized race. A client
    running seconds behind, which is the entire subject of the end-of-fight lag work, is exactly where 500 ms
    stops being enough, and these clients were being unhooked in that state routinely.

    But it cannot be ruled out either, and a hard minimum working set is a real imposition
    on a process. So: pin ONE client and leave the others as controls. That halves the
    blast radius and gives a better experiment than pinning all four anyway.

    Compare BETWEEN clients of different session lengths, not absolutes.

    The fault column is a raw delta, not a rate, and the counter behind it is cumulative
    and survives unhooking. So the probe does NOT have to watch a farming session to
    measure one -- which matters, because it holds hooks and BartleBot cannot run while it
    does. Snapshot, hand the clients back to the app for N fights, snapshot again: with
    --csv the second run seeds its baseline from the file and prints faults accumulated
    across the whole session, and the app's own log gives the fight count to divide by.

BEFORE YOU RUN IT
    Unhook All in BartleBot, THEN close it, THEN run this. Both halves matter:

    * Unhook All releases the game windows. While embedded they are Win32 children of the
      Qt window and closing BartleBot destroys them with it -- nothing un-embeds on quit.
      Unhook All does: unhook_all -> _finish_hook_change -> _sync_embedded_clients
      vacates every slot once the client list empties. Quitting without it logs out every
      client, which on a diagnostic run destroys the accumulated state being measured.
    * Unhook All also restores the patched bytes and rewrites the autobot pattern
      (HookHandler.close -> _rewrite_autobot), which is what lets this probe hook at all.
      Without it, _prepare_autobot's pattern scan finds a region still full of the
      previous session's hooks and this probe errors out rather than starting. That half
      is merely inconvenient; the window half is the destructive one.

USAGE
    uv run python tools/probe_chat_log_size.py             # one snapshot, all clients
    uv run python tools/probe_chat_log_size.py --watch     # resample every 60s
    uv run python tools/probe_chat_log_size.py --watch --interval 300 --csv out.local.csv
    uv run python tools/probe_chat_log_size.py --pin-working-set 1200   # then farm

    The measurement workflow is snapshot / farm / snapshot, because this probe holds hooks
    and BartleBot cannot run while it does:

        probe --no-world --csv run.local.csv     # baseline
        <rehook in BartleBot, farm N fights, Unhook All, quit>
        probe --no-world --csv run.local.csv     # seeds from the file, prints the delta

    Divide `faults Δ` by the fight count (grep 'SprintyCombat finished' in the app log).

    A --csv path must end in .local.csv: the wizard names and chat contents this reads are
    real player data, and *.local.* is gitignored so a recording cannot be committed by
    mistake. Only sizes are ever written -- never the chat text itself.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import Primitive

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartlebot.utils import drop_detection  # noqa: E402

# Wizard names are real player data. They are printed so the operator can tell which
# window is which, and never written to the CSV.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# maybe_text()'s two candidate offsets for the string struct, in its own order of
# preference. ControlText -- which the chat log is -- uses 712.
_TEXT_OFFSETS = (712, 736)

# (csv column name, key in a sample row). ONE source of truth for both the header and the
# rows, because keeping two literals in step failed twice: the header said 11 columns while
# the rows wrote 17, and a later edit added a column to neither. Anything read by name out
# of this file was silently mislabeled in between.
_CSV_FIELDS: tuple[tuple[str, str], ...] = (
    ("elapsed_s", "elapsed_s"),
    ("pid", "pid"),
    ("chat_chars", "chars"),
    ("capacity", "capacity"),
    ("lines", "lines"),
    ("read_ms", "read_ms"),
    ("entities", "entities"),
    ("entity_ms", "entity_ms"),
    ("windows", "windows"),
    ("window_ms", "window_ms"),
    ("working_set", "working_set"),
    ("peak_working_set", "peak_working_set"),
    ("page_faults", "faults"),
    ("private_bytes", "private_bytes"),
    ("handles", "handles"),
    ("gdi", "gdi"),
    ("user", "user"),
    ("pinned", "pinned"),
)


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _system_memory() -> str:
    """One line of machine-wide memory state.

    Whether the box is actually short of RAM decides how to read the trim columns. Under
    real pressure, trimmed working sets are just the OS coping and the interesting question
    becomes what is consuming the memory. With plenty free, a trimmed client points at
    background-process trimming instead, which is the case that scales with session length.
    """
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return "system memory: unavailable"
    gb = 1 << 30
    return (
        f"system memory: {status.ullTotalPhys / gb:.1f} GB total, "
        f"{status.ullAvailPhys / gb:.1f} GB available, {status.dwMemoryLoad}% in use"
    )


def _last_faults_per_pid(path: Path) -> dict[int, int]:
    """Last recorded cumulative page-fault count for each pid in a probe CSV.

    Tolerates a short or malformed row rather than raising: a half-written line from an
    interrupted run must not cost the baseline for every other client.
    """
    baseline: dict[int, int] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            header = handle.readline().strip().split(",")
            try:
                pid_at = header.index("pid")
                faults_at = header.index("page_faults")
            except ValueError:
                return {}
            for raw in handle:
                fields = raw.strip().split(",")
                if len(fields) <= max(pid_at, faults_at):
                    continue
                try:
                    baseline[int(fields[pid_at])] = int(fields[faults_at])
                except ValueError:
                    continue
    except OSError:
        return {}
    return baseline


_DWORD_MAX = 0xFFFFFFFF
# PageFaultCount is a DWORD, so the counter wraps at ~4.29 billion. A client taking
# millions of faults per fight reaches that inside a long overnight run -- one did, and
# reported 5.46M after 9h47m when the truth was ~4.30 billion.


def fault_delta(before: int, after: int) -> tuple[int, bool]:
    """Faults between two samples, reconstructing a single DWORD wrap.

    Returns ``(delta, wrapped)``. A counter that went backwards can only have wrapped, so
    the span is what remained to the cap plus whatever it has counted since.

    Two wraps inside one interval are indistinguishable from one and are silently
    undercounted by 4.29 billion each. Sampling more often than every ~35 minutes is what
    rules that out on the worst client measured; the flag is there so a row that had to be
    reconstructed at all is never read as a plain measurement.
    """
    if after >= before:
        return after - before, False
    return (_DWORD_MAX - before) + after + 1, True


def is_pinned(pid: int) -> bool:
    """Whether `pid` currently has a hard minimum working set.

    Recorded on every sample so an overnight run is self-describing: which clients were in
    the treatment group is in the data rather than in someone's memory of what they typed
    the night before.
    """
    PROCESS_QUERY_INFORMATION = 0x0400
    QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x00000001

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        minimum = ctypes.c_size_t()
        maximum = ctypes.c_size_t()
        flags = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetProcessWorkingSetSizeEx(
            handle, ctypes.byref(minimum), ctypes.byref(maximum), ctypes.byref(flags)
        ):
            return False
        return bool(flags.value & QUOTA_LIMITS_HARDWS_MIN_ENABLE)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def read_working_set_limits(pid: int) -> str:
    """Current minimum/maximum working set for `pid` and whether either is a hard limit.

    Exists so a pin can be verified and, more importantly, so a pin left behind by an
    earlier run is visible rather than silently in force.
    """
    PROCESS_QUERY_INFORMATION = 0x0400
    QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x00000001

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return "unreadable"
    try:
        minimum = ctypes.c_size_t()
        maximum = ctypes.c_size_t()
        flags = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetProcessWorkingSetSizeEx(
            handle, ctypes.byref(minimum), ctypes.byref(maximum), ctypes.byref(flags)
        )
        if not ok:
            return "unreadable"
        hard = "HARD min" if flags.value & QUOTA_LIMITS_HARDWS_MIN_ENABLE else "soft"
        return f"min {minimum.value / 1_048_576:.0f} MB, max {maximum.value / 1_048_576:.0f} MB ({hard})"
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def unpin_working_set(pid: int) -> str:
    """Undo pin_working_set: hand the process back to the OS's own trimming policy.

    Worth having as its own flag rather than "just restart the client". A pin persists for
    the life of the process, so without this the only way out of a bad pin is to log the
    account out and back in -- which on a diagnostic run is exactly the state change that
    destroys what is being measured.
    """
    PROCESS_SET_QUOTA = 0x0100
    QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000002
    QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SET_QUOTA, False, pid)
    if not handle:
        return f"pid {pid}: OpenProcess failed (error {ctypes.windll.kernel32.GetLastError()})"
    try:
        # NOT -1/-1: that pair is the documented "trim the working set now" special case of
        # SetProcessWorkingSetSize, and against the Ex variant it returns success while
        # leaving the hard limit in place -- which is how the first version of this shipped
        # an undo that silently did nothing. Restore Windows' own defaults (200 KB / 1 MB)
        # with the hard-limit flags explicitly disabled, and verify by reading back.
        ok = ctypes.windll.kernel32.SetProcessWorkingSetSizeEx(
            handle,
            ctypes.c_size_t(200 * 1024),
            ctypes.c_size_t(1380 * 1024),
            ctypes.c_ulong(QUOTA_LIMITS_HARDWS_MIN_DISABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE),
        )
        if not ok:
            return f"pid {pid}: unpin failed, error {ctypes.windll.kernel32.GetLastError()}"
        if "HARD" in read_working_set_limits(pid):
            return f"pid {pid}: unpin call succeeded but the hard limit is STILL set — restart this client"
        return f"pid {pid}: working set limits handed back to the OS"
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def pin_working_set(pid: int, minimum_mb: int) -> str:
    """Ask Windows to stop trimming `pid` below `minimum_mb`, and report what happened.

    The faults measured in the end-of-fight lag work are soft: `private_bytes` never moves, so the pages are
    already committed and are simply being taken out of the working set and faulted back
    in, over and over. If that is the whole story, a hard *minimum* working set stops it
    dead -- Windows keeps the pages resident and there is nothing left to fault on.

    QUOTA_LIMITS_HARDWS_MIN_ENABLE is the flag that makes the minimum binding rather than
    advisory. The maximum is deliberately left soft: enabling a hard maximum would cap the
    process instead and cause exactly the thrashing this is meant to stop.

    Needs PROCESS_SET_QUOTA, and raising a minimum needs SE_INC_WORKING_SET_NAME, which is
    granted to Users by default but can be absent on a locked-down machine. Failure is
    reported rather than raised: an experiment that cannot run should say so, not crash.
    """
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_QUERY_INFORMATION = 0x0400
    QUOTA_LIMITS_HARDWS_MIN_ENABLE = 0x00000001
    QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008

    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        # GetLastError, not ctypes.get_last_error: the latter reads a copy that ctypes only
        # maintains for functions declared with use_last_error=True, and reads 0 otherwise.
        return f"pid {pid}: OpenProcess failed (error {ctypes.windll.kernel32.GetLastError()})"

    try:
        minimum = minimum_mb * 1024 * 1024
        # The maximum has to clear the minimum, and is left generous and soft.
        maximum = minimum * 2
        ok = ctypes.windll.kernel32.SetProcessWorkingSetSizeEx(
            handle,
            ctypes.c_size_t(minimum),
            ctypes.c_size_t(maximum),
            ctypes.c_ulong(QUOTA_LIMITS_HARDWS_MIN_ENABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE),
        )
        if not ok:
            error = ctypes.windll.kernel32.GetLastError()
            hint = " (needs SE_INC_WORKING_SET_NAME — try an elevated shell)" if error == 1314 else ""
            return f"pid {pid}: SetProcessWorkingSetSizeEx failed, error {error}{hint}"
        return f"pid {pid}: minimum working set pinned at {minimum_mb} MB"
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _window_title(hwnd: int) -> str:
    """The game window's title, which is how the launcher tells clients apart."""
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _process_stats(pid: int) -> dict[str, int]:
    """Working set / private bytes / handle and GUI object counts for `pid`.

    Zeroes rather than raising if a counter is unavailable: this is a diagnostic, and a
    missing GDI count must not cost the chat measurement that is the point of the run.
    """
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return {}

    stats: dict[str, int] = {}
    try:
        counters = _PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            stats["working_set"] = counters.WorkingSetSize
            stats["private_bytes"] = counters.PrivateUsage
            # The two that test the working-set-trim theory. `peak` is the discriminator
            # available from a single snapshot: a client whose peak is roughly double its
            # current working set has had pages evicted, and every later touch of one is a
            # fault. `faults` is cumulative, so it is the *delta* between --watch samples
            # that means anything -- and note Windows does not split soft from hard here,
            # so a large delta is suggestive rather than proof of disk I/O.
            stats["peak_working_set"] = counters.PeakWorkingSetSize
            stats["faults"] = counters.PageFaultCount

        count = wintypes.DWORD()
        if ctypes.windll.kernel32.GetProcessHandleCount(handle, ctypes.byref(count)):
            stats["handles"] = count.value

        # 0 = GDI objects, 1 = USER objects.
        stats["gdi"] = ctypes.windll.user32.GetGuiResources(handle, 0)
        stats["user"] = ctypes.windll.user32.GetGuiResources(handle, 1)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    return stats


async def _chat_string_stats(client) -> dict[str, int] | None:
    """Length and capacity of the chat log's std::wstring, without decoding it.

    Deliberately does not read the characters: on a client that has been farming all
    night the buffer may be megabytes, and pulling it across the process boundary is the
    very cost being measured. Only the 32-byte header is read.
    """
    window = await drop_detection._get_window_from_path(client.root_window, drop_detection._CHAT_PATH)
    if window is None:
        return None

    for offset in _TEXT_OFFSETS:
        try:
            base = await window.read_base_address() + offset
            length = await window.read_typed(base + 16, Primitive.int32)
            capacity = await window.read_typed(base + 24, Primitive.uint64)
        except Exception:
            continue
        # A plausible chat log: non-empty, and a capacity that is a real allocation
        # rather than the inline-buffer sentinel or garbage from the wrong offset.
        if 0 < length <= capacity < 1 << 32:
            return {"chars": length, "capacity": capacity, "offset": offset}
    return None


async def _count_lines(client) -> int | None:
    """Line count of the chat log, by actually reading the text.

    Separated from `_chat_string_stats` and only run once per sample because it is the
    expensive read -- the same read the drop poll pays every 0.5s during a fight.
    """
    try:
        text = await drop_detection._get_window_from_path(client.root_window, drop_detection._CHAT_PATH)
        if text is None:
            return None
        return (await text.maybe_text() or "").count("\n") + 1
    except Exception:
        return None


async def _world_counts(client) -> dict[str, int]:
    """Entity and UI-tree sizes — the "client-side accumulation" candidate.

    The first probe run ruled the chat log out (it is hard-capped, see the module
    docstring), and ruled out working set, private bytes, handles and GDI along with it —
    on the four clients measured, memory ran *opposite* to the lag. What is left is
    something that accumulates inside the game world rather than in an OS counter:
    entities that are never released, or a UI tree that grows a node per fight.

    Both are counted by walking, so both cost real time on a laggy client. That is not a
    flaw here: `entity_ms` and `window_ms` are reported, and a walk that itself slows down
    over a session is evidence in its own right.
    """
    counts: dict[str, int] = {}

    started = time.perf_counter()
    try:
        counts["entities"] = len(await client.get_base_entity_list())
    except Exception:
        pass
    counts["entity_ms"] = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    try:
        total = 0
        stack = [client.root_window]
        while stack:
            window = stack.pop()
            total += 1
            # Depth is bounded by the UI tree, but a runaway tree is exactly what this
            # looks for — stop counting rather than hang the sample.
            if total > 50_000:
                break
            stack.extend(await window.children())
        counts["windows"] = total
    except Exception:
        pass
    counts["window_ms"] = int((time.perf_counter() - started) * 1000)

    return counts


async def _sample(clients, names: list[str], world: bool) -> list[dict]:
    rows = []
    for client, name in zip(clients, names):
        stats = await _chat_string_stats(client)
        row: dict = {"name": name, "pid": client.process_id}
        if stats:
            row.update(stats)
            started = time.perf_counter()
            row["lines"] = await _count_lines(client)
            row["read_ms"] = (time.perf_counter() - started) * 1000
        if world:
            row.update(await _world_counts(client))
        row.update(_process_stats(client.process_id))
        row["pinned"] = is_pinned(client.process_id)
        rows.append(row)
    return rows


def _print_sample(rows: list[dict], elapsed: float, world: bool, prev_faults: dict[int, int]) -> None:
    print(f"\n=== t+{elapsed / 60:.1f} min " + "=" * 52)
    header = f"{'client':<22} {'chat':>7} {'lines':>6} {'read':>8}"
    if world:
        header += f" {'entities':>9} {'windows':>8} {'win ms':>7}"
    header += f" {'wkset MB':>9} {'peak MB':>9} {'trim':>6} {'pin':>5} {'faults Δ':>13} {'GDI':>6}"
    print(header)
    print("-" * len(header))
    for row in rows:
        chars = row.get("chars")
        line = f"{row['name'][:22]:<22} "
        line += f"{chars:>7,} " if chars is not None else f"{'n/a':>7} "
        line += f"{row.get('lines') or 0:>6,} {row.get('read_ms', 0):>6.1f}ms"
        if world:
            line += f" {row.get('entities', 0):>9,} {row.get('windows', 0):>8,} {row.get('window_ms', 0):>6,}ms"

        working = row.get("working_set", 0)
        peak = row.get("peak_working_set", 0)
        # How much of its own high-water mark the process is no longer holding. A client
        # sitting well under its peak has had pages taken back by the OS.
        trimmed = f"{100 * (peak - working) / peak:>5.0f}%" if peak else f"{'—':>6}"

        # A raw delta rather than a rate. Faults arrive in a burst at end of fight, so a
        # per-second figure just divides that burst by whatever interval happened to
        # contain it and reads as precision that is not there. The delta is also what
        # carries across probe runs: the counter is cumulative and survives unhooking, so
        # a snapshot before a farming session and one after gives faults-per-fight without
        # having to watch anything.
        faults = row.get("faults")
        before = prev_faults.get(row["pid"])
        if before is not None and faults is not None:
            span, wrapped = fault_delta(before, faults)
            # "~" rather than a footnote: the number is a reconstruction across a counter
            # wrap, and a reader scanning a column of deltas has to be able to see that
            # without reading anything else.
            delta = f"{'~' if wrapped else ''}{span:,}".rjust(13)
        else:
            delta = f"{'—':>13}"

        pin = "HARD" if row.get("pinned") else "—"
        line += (
            f" {working / 1_048_576:>9.1f} {peak / 1_048_576:>9.1f} {trimmed} {pin:>5} {delta} {row.get('gdi', 0):>6,}"
        )
        print(line)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--watch", action="store_true", help="keep sampling until interrupted")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between samples with --watch")
    ap.add_argument("--csv", type=Path, help="append samples to this file; must end in .local.csv")
    ap.add_argument(
        "--pin-working-set",
        type=int,
        metavar="MB",
        help=(
            "ask Windows to stop trimming each client below this many MB, then exit. "
            "Tests whether the end-of-fight lag fault storm is pure working-set thrash: pin, farm, "
            "and compare faults-per-fight against the unpinned baseline."
        ),
    )
    ap.add_argument("--unpin", action="store_true", help="undo --pin-working-set on every client, then exit")
    ap.add_argument(
        "--only",
        metavar="SUBSTRING",
        help="restrict --pin-working-set / --unpin to clients whose window title contains this",
    )
    ap.add_argument(
        "--no-world",
        action="store_true",
        help="skip the entity and window-tree walks (they are the slow part of a sample)",
    )
    args = ap.parse_args()

    if args.csv and not args.csv.name.endswith(".local.csv"):
        print(f"Refusing to write {args.csv.name}: a recording path must end in .local.csv so it stays gitignored.")
        return 2

    sprinter = WizSprinter()
    clients = sprinter.get_new_clients()
    if not clients:
        print("No Wizard101 clients found.")
        return 1

    if args.pin_working_set or args.unpin:
        targets = clients
        if args.only:
            targets = [c for c in clients if args.only.lower() in _window_title(c.window_handle).lower()]
            if not targets:
                print(f"No client's window title contains {args.only!r}. Nothing done.")
                return 2
        elif args.pin_working_set:
            # Pinning every client at once was how this was first suggested, and it left no
            # controls and no way to attribute a crash. Make the safe shape the easy one.
            print(
                "Pinning ALL clients. Prefer --only <name> to pin one and keep the rest as\n"
                "controls — it is both safer and a better experiment.\n"
            )

        for client in targets:
            title = _window_title(client.window_handle) or f"pid {client.process_id}"
            if args.unpin:
                print(f"  {title}: " + unpin_working_set(client.process_id))
            else:
                print(f"  {title}: " + pin_working_set(client.process_id, args.pin_working_set))
            print(f"      now: {read_working_set_limits(client.process_id)}")
        print(
            "\nUnhook All is a known crash risk on a heavily lagging client regardless of\n"
            "pinning: wizwalker restores the hook bytes after a fixed 0.5s sleep, which a\n"
            "slow client can still be executing inside. Not caused by this tool, but it is\n"
            "when you will meet it."
        )
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
        return 0

    print(
        f"Found {len(clients)} client(s). Activating hooks.\n"
        "If BartleBot is still running, stop now: Unhook All and quit it first — closing it\n"
        "while the windows are still embedded destroys them."
    )
    for client in clients:
        await client.activate_hooks()

    # ClientObject.display_name() holds no name string -- a wizard's name is three table
    # indices, which is what bartlebot/wizard_names.py exists to decode. The first run of
    # this probe called it anyway and every row came back as a bare pid, which made the
    # side-by-side comparison the probe is *for* impossible to line up with which window
    # had been relogged. The window title is what the launcher already uses to tell
    # clients apart, and it costs one Win32 call.
    names = [_window_title(client.window_handle) or f"pid {client.process_id}" for client in clients]

    handle = args.csv.open("a", encoding="utf-8") if args.csv else None
    if handle and handle.tell() == 0:
        # Must match the row write below field for field. It did not, once: the rows grew
        # to 17 columns while this line still named 11, so every column past read_ms was
        # mislabeled in the file even though the printed table was right.
        handle.write(",".join(name for name, _ in _CSV_FIELDS) + "\n")

    world = not args.no_world
    # pid -> (cumulative page faults, elapsed when read). A cumulative counter says
    # nothing on its own; the rate between samples is the signal.
    prev_faults: dict[int, int] = {}
    prev_elapsed: dict[int, float] = {}

    # Seed from the CSV's last row per pid, so the very first sample of a NEW probe run
    # reports faults accumulated since the previous run rather than a dash. That is what
    # makes "snapshot, farm for an hour under BartleBot, snapshot" work: this probe cannot
    # hold hooks while the app does, but the counter is cumulative and survives unhooking,
    # so the boundaries are all that need measuring. Pids must match — a relaunched client
    # is a new process with a counter starting at zero, and simply will not seed.
    if args.csv and args.csv.exists():
        # Only seed pids that are actually running now. The file accumulates across
        # sessions, so it holds rows for clients that have since been relaunched, and
        # counting those made the startup line claim 8 baselines for 4 clients.
        live = {c.process_id for c in clients}
        prev_faults.update({pid: n for pid, n in _last_faults_per_pid(args.csv).items() if pid in live})
        missing = len(live) - len(prev_faults)
        note = f" ({missing} client(s) are new since the last run)" if missing else ""
        print(f"Seeded fault baseline for {len(prev_faults)} of {len(live)} client(s){note}.")
    started = time.monotonic()
    try:
        while True:
            elapsed = time.monotonic() - started
            rows = await _sample(clients, names, world)
            for row in rows:
                row["prev_elapsed"] = prev_elapsed.get(row["pid"], elapsed)
            _print_sample(rows, elapsed, world, prev_faults)
            for row in rows:
                if row.get("faults") is not None:
                    prev_faults[row["pid"]] = row["faults"]
                    prev_elapsed[row["pid"]] = elapsed
            if handle:
                for row in rows:
                    # Sizes only. The chat text and the wizard name never reach disk.
                    values = {**row, "elapsed_s": f"{elapsed:.0f}"}
                    handle.write(",".join(str(values.get(key, "")) for _, key in _CSV_FIELDS) + "\n")
                handle.flush()
            if not args.watch:
                break
            await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if handle:
            handle.close()
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass

    print(
        "\nHow to read this:\n"
        "  `faults Δ` is the headline. Divide it by the number of fights in the run (the\n"
        "  app log's 'SprintyCombat finished' count) to compare clients. Measured so far:\n"
        "  ~2,000-25,000 per fight healthy, ~4.6M-6.7M on a client degraded overnight.\n"
        "  `trim` at 0% with working set ≈ peak means nothing is being taken away, and no\n"
        "  storm has ever been seen in that state. A trim PERCENTAGE does not discriminate\n"
        "  though — degraded clients read 47-51% against 53% on a healthy one. It is the\n"
        "  churn, not the level, and `faults Δ` is the only direct measure of that.\n"
        "  The chat cap is NOT the trigger: three clients reached ~171 lines with no storm.\n"
        "  Degradation has only ever been reproduced over many hours, never in one sitting,\n"
        "  so a short attended run showing nothing is the expected result, not evidence."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
