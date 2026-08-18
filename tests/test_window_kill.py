"""Tests for the client close sequence (#79).

No real windows or processes: user32/kernel32 are fakes driven by a scripted timeline,
so each test states exactly when the process dies and what popups it puts up.
"""

import ctypes
import sys

import pytest

from bartlebot.gui.game_embed import win32_window
from bartlebot.utils import window_kill
from bartlebot.utils.window_kill import (
    DIALOG_CLASS,
    STILL_ACTIVE,
    WM_CLOSE,
    dismiss_popups,
    force_close_client,
    is_process_alive,
    popup_windows,
    terminate_process,
)

GAME_CLASS = "Wizard Graphical Client"


class FakeWindow:
    def __init__(self, hwnd: int, pid: int, cls: str, visible: bool = True) -> None:
        self.hwnd = hwnd
        self.pid = pid
        self.cls = cls
        self.visible = visible


class FakeUser32:
    """Enough of user32 to drive window_kill, recording every message posted."""

    def __init__(self, windows: list[FakeWindow] | None = None) -> None:
        self.windows = windows or []
        self.posted: list[tuple[int, int]] = []  # (hwnd, message)

    def _find(self, hwnd):
        return next((w for w in self.windows if w.hwnd == int(hwnd)), None)

    def EnumWindows(self, callback, _lparam):  # noqa: N802 - Win32 name
        for window in list(self.windows):
            callback(window.hwnd, 0)
        return True

    def GetWindowThreadProcessId(self, hwnd, pid_ptr):  # noqa: N802
        window = self._find(hwnd)
        pid_ptr._obj.value = window.pid if window else 0
        return 1

    def GetClassNameW(self, hwnd, buf, _size):  # noqa: N802
        window = self._find(hwnd)
        if window is None:
            return 0
        buf.value = window.cls
        return len(window.cls)

    def IsWindowVisible(self, hwnd):  # noqa: N802
        window = self._find(hwnd)
        return bool(window and window.visible)

    def PostMessageW(self, hwnd, msg, _wparam, _lparam):  # noqa: N802
        self.posted.append((int(hwnd), int(msg)))
        return True


class FakeKernel32:
    """A process that is alive until `alive_polls` liveness checks have been made.

    Counting polls rather than watching the clock keeps the tests independent of how
    long force_close_client's sleeps actually take.
    """

    def __init__(self, pid: int = 4242, alive_polls: int | None = None, openable: bool = True) -> None:
        self.pid = pid
        self.alive_polls = alive_polls  # None = never dies on its own
        self.openable = openable
        self.polls = 0
        self.terminated = False
        self.dies_on_terminate = True
        self.closed_handles = 0

    def _alive(self) -> bool:
        if self.terminated and self.dies_on_terminate:
            return False
        return self.alive_polls is None or self.polls <= self.alive_polls

    def OpenProcess(self, _access, _inherit, pid):  # noqa: N802
        return 99 if (self.openable and pid == self.pid) else 0

    def GetExitCodeProcess(self, _handle, code_ptr):  # noqa: N802
        self.polls += 1
        code_ptr._obj.value = STILL_ACTIVE if self._alive() else 0
        return True

    def TerminateProcess(self, _handle, _exit_code):  # noqa: N802
        self.terminated = True
        return True

    def CloseHandle(self, _handle):  # noqa: N802
        self.closed_handles += 1
        return True


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Make the poll sleeps free so the grace period costs no wall-clock time."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(window_kill.asyncio, "sleep", _instant)


# ---------------------------------------------------------------------------
# Popup detection
# ---------------------------------------------------------------------------


def test_popup_windows_finds_only_this_process_dialogs():
    user32 = FakeUser32(
        [
            FakeWindow(1, pid=100, cls=GAME_CLASS),  # the client itself
            FakeWindow(2, pid=100, cls=DIALOG_CLASS),  # its error popup
            FakeWindow(3, pid=200, cls=DIALOG_CLASS),  # another process's dialog
        ]
    )
    assert popup_windows(100, user32) == [2], "only the crashed client's own dialog should match"


def test_popup_windows_ignores_hidden_dialogs():
    user32 = FakeUser32([FakeWindow(2, pid=100, cls=DIALOG_CLASS, visible=False)])
    assert popup_windows(100, user32) == []


def test_dismiss_popups_closes_each_dialog():
    user32 = FakeUser32(
        [
            FakeWindow(1, pid=100, cls=GAME_CLASS),
            FakeWindow(2, pid=100, cls=DIALOG_CLASS),
            FakeWindow(3, pid=100, cls=DIALOG_CLASS),
        ]
    )
    assert dismiss_popups(100, user32) == 2
    assert user32.posted == [(2, WM_CLOSE), (3, WM_CLOSE)]
    assert (1, WM_CLOSE) not in user32.posted, "the game window must not be closed by the popup sweep"


# ---------------------------------------------------------------------------
# Process liveness
# ---------------------------------------------------------------------------


def test_is_process_alive_true_while_running():
    kernel32 = FakeKernel32()
    assert is_process_alive(4242, kernel32) is True
    assert kernel32.closed_handles == 1, "the process handle must be released on every check"


def test_is_process_alive_false_once_exited():
    kernel32 = FakeKernel32(alive_polls=0)
    kernel32.polls = 1
    assert is_process_alive(4242, kernel32) is False


def test_is_process_alive_false_when_unopenable():
    """A PID we cannot open counts as gone — treating it as alive would stall recovery."""
    assert is_process_alive(4242, FakeKernel32(openable=False)) is False


def test_is_process_alive_false_for_zero_pid():
    assert is_process_alive(0, FakeKernel32()) is False


def test_terminate_process_reports_failure_when_unopenable():
    kernel32 = FakeKernel32(openable=False)
    assert terminate_process(4242, kernel32) is False
    assert kernel32.terminated is False


# ---------------------------------------------------------------------------
# The close sequence
# ---------------------------------------------------------------------------


async def test_clean_exit_needs_no_terminate():
    """The common case: no popup, the client exits on WM_CLOSE."""
    user32 = FakeUser32([FakeWindow(1, pid=4242, cls=GAME_CLASS)])
    kernel32 = FakeKernel32(alive_polls=0)

    assert await force_close_client(1, user32=user32, kernel32=kernel32) is True
    assert user32.posted == [(1, WM_CLOSE)]
    assert kernel32.terminated is False, "a client that closed on its own must not be killed"


async def test_popup_is_dismissed_while_waiting_for_exit():
    """A dialog that appears after WM_CLOSE is closed, and the exit is still clean."""
    user32 = FakeUser32(
        [
            FakeWindow(1, pid=4242, cls=GAME_CLASS),
            FakeWindow(2, pid=4242, cls=DIALOG_CLASS),
        ]
    )
    kernel32 = FakeKernel32(alive_polls=2)

    assert await force_close_client(1, user32=user32, kernel32=kernel32) is True
    assert (2, WM_CLOSE) in user32.posted, "the error popup should have been dismissed"
    assert kernel32.terminated is False


async def test_stuck_client_is_terminated_after_grace():
    """The bug in #79: the popup wins, so the process has to be killed outright."""
    user32 = FakeUser32(
        [
            FakeWindow(1, pid=4242, cls=GAME_CLASS),
            FakeWindow(2, pid=4242, cls=DIALOG_CLASS),
        ]
    )
    kernel32 = FakeKernel32()  # never exits on its own

    assert await force_close_client(1, user32=user32, kernel32=kernel32, grace=0.05) is True
    assert kernel32.terminated is True


async def test_returns_false_when_process_survives_terminate():
    user32 = FakeUser32([FakeWindow(1, pid=4242, cls=GAME_CLASS)])
    kernel32 = FakeKernel32()
    kernel32.dies_on_terminate = False

    result = await force_close_client(1, user32=user32, kernel32=kernel32, grace=0.05, terminate_timeout=0.05)
    assert result is False, "an unkillable client must be reported, not silently waited on"


async def test_missing_window_is_already_closed():
    """A window that is gone by the time we look has no PID to wait on."""
    user32 = FakeUser32([])
    kernel32 = FakeKernel32()

    assert await force_close_client(999, user32=user32, kernel32=kernel32) is True
    assert kernel32.terminated is False


async def test_explicit_pid_is_used_over_the_window_lookup():
    """WizWalker knows the PID; passing it survives the window disappearing first."""
    user32 = FakeUser32([])
    kernel32 = FakeKernel32(pid=4242, alive_polls=0)

    assert await force_close_client(1, pid=4242, user32=user32, kernel32=kernel32) is True
    assert kernel32.polls > 0, "liveness should have been polled against the supplied PID"


@pytest.mark.skipif(sys.platform != "win32", reason="needs the real Win32 DLLs")
def test_our_prototypes_do_not_leak_onto_the_shared_dll_handles():
    """Regression: argtypes on ctypes.windll.* are shared with wizwalker and pymem.

    ctypes.windll caches one library object per name for the whole process, so
    declaring EnumWindows.argtypes there also declared them for wizwalker's
    get_all_wizard_handles() — which passes a callback built from its own
    WINFUNCTYPE and was rejected outright, killing client detection during a
    recovery. Private WinDLL instances keep our prototypes off the shared handles.
    """
    window_kill._user32_cache = None
    window_kill._kernel32_cache = None

    user32 = window_kill.default_user32()
    kernel32 = window_kill.default_kernel32()

    assert user32 is not ctypes.windll.user32
    assert kernel32 is not ctypes.windll.kernel32
    assert win32_window.default_user32() is not ctypes.windll.user32
    assert getattr(ctypes.windll.user32.EnumWindows, "argtypes", None) is None


@pytest.mark.skipif(sys.platform != "win32", reason="needs the real Win32 DLLs")
def test_wizwalker_style_enum_windows_still_works_after_our_init():
    """The exact call from wizwalker.utils.get_windows_from_predicate, which #79's fix broke."""
    window_kill._user32_cache = None
    window_kill.default_user32()

    enumwindows_func_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    )
    callback = enumwindows_func_type(lambda _handle, _lparam: 1)

    # Raised ctypes.ArgumentError before the fix.
    assert ctypes.windll.user32.EnumWindows(callback, 0)


def test_ctypes_signatures_cover_every_call_site():
    """Guards the argtypes table: a missing entry means silent HWND truncation."""
    user32_names = {name for name, _, _ in window_kill._USER32_SIGNATURES}
    kernel32_names = {name for name, _, _ in window_kill._KERNEL32_SIGNATURES}
    assert {"EnumWindows", "GetWindowThreadProcessId", "GetClassNameW", "PostMessageW"} <= user32_names
    assert {"OpenProcess", "GetExitCodeProcess", "TerminateProcess", "CloseHandle"} <= kernel32_names
    assert all(isinstance(argtypes, list) for _, argtypes, _ in window_kill._USER32_SIGNATURES)
    assert window_kill._HWND is ctypes.wintypes.HWND
