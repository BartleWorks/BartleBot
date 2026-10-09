"""Tests for the per-process GDI count.

No real process: user32/kernel32 are fakes, so each test states exactly what the OS
answers and what the caller is left holding.
"""

from bartlebot.clients.process_resources import (
    GR_GDIOBJECTS,
    GR_USEROBJECTS,
    PROCESS_QUERY_LIMITED_INFORMATION,
    gdi_object_count,
    gui_object_count,
)


class FakeKernel32:
    """OpenProcess/CloseHandle, recording what was opened and whether it was released."""

    def __init__(self, handle: int = 0x1234) -> None:
        self._handle = handle
        self.opened: list[tuple[int, int]] = []  # (access, pid)
        self.closed: list[int] = []

    def OpenProcess(self, access, _inherit, pid):  # noqa: N802 — Win32 name
        self.opened.append((access, pid))
        return self._handle

    def CloseHandle(self, handle):  # noqa: N802 — Win32 name
        self.closed.append(handle)
        return True


class FakeUser32:
    def __init__(self, counts: dict[int, int]) -> None:
        self.counts = counts  # which -> count
        self.calls: list[tuple[int, int]] = []  # (handle, which)

    def GetGuiResources(self, handle, which):  # noqa: N802 — Win32 name
        self.calls.append((handle, which))
        return self.counts.get(which, 0)


def test_reads_the_gdi_count_for_a_live_process():
    kernel32 = FakeKernel32()
    user32 = FakeUser32({GR_GDIOBJECTS: 3_197})
    assert gdi_object_count(4242, user32=user32, kernel32=kernel32) == 3_197
    assert kernel32.opened == [(PROCESS_QUERY_LIMITED_INFORMATION, 4242)]


def test_the_handle_is_always_released():
    kernel32 = FakeKernel32(handle=0xBEEF)
    gdi_object_count(4242, user32=FakeUser32({GR_GDIOBJECTS: 10_000}), kernel32=kernel32)
    assert kernel32.closed == [0xBEEF]


def test_the_handle_is_released_even_when_the_read_raises():
    class Exploding(FakeUser32):
        def GetGuiResources(self, handle, which):  # noqa: N802 — Win32 name
            raise OSError("access denied")

    kernel32 = FakeKernel32(handle=0xBEEF)
    try:
        gdi_object_count(4242, user32=Exploding({}), kernel32=kernel32)
    except OSError:
        pass
    assert kernel32.closed == [0xBEEF]


def test_a_pid_that_cannot_be_opened_reads_zero():
    kernel32 = FakeKernel32(handle=0)  # OpenProcess failed — exited, or not permitted
    user32 = FakeUser32({GR_GDIOBJECTS: 3_197})
    assert gdi_object_count(4242, user32=user32, kernel32=kernel32) == 0
    assert user32.calls == []  # nothing read through a null handle
    assert kernel32.closed == []  # and nothing to release


def test_pid_zero_is_not_even_opened():
    kernel32 = FakeKernel32()
    assert gdi_object_count(0, user32=FakeUser32({}), kernel32=kernel32) == 0
    assert kernel32.opened == []


def test_user_objects_are_reachable_through_the_same_call():
    user32 = FakeUser32({GR_GDIOBJECTS: 3_197, GR_USEROBJECTS: 412})
    count = gui_object_count(4242, GR_USEROBJECTS, user32=user32, kernel32=FakeKernel32())
    assert count == 412
