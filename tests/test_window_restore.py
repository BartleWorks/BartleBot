"""Tests for the Win32 embed/restore mechanics behind the game-window tiles.

Regression coverage for the pop-out restore fix: a popped-out game window could not be moved, because
``unembed()`` never really restored the window. No Qt and no Wizard101 process are
needed — ``EmbeddedWindow`` takes an injectable ``user32`` stand-in.
"""

from __future__ import annotations

import pytest

from bartlebot.gui.game_embed.win32_window import (
    FRAME_STYLES,
    GWL_EXSTYLE,
    GWL_STYLE,
    SWP_FRAMECHANGED,
    WS_CAPTION,
    WS_CHILD,
    WS_EX_APPWINDOW,
    WS_MAXIMIZEBOX,
    WS_MINIMIZEBOX,
    WS_POPUP,
    WS_SYSMENU,
    WS_THICKFRAME,
    WS_VISIBLE,
    EmbeddedWindow,
    child_style,
    is_click_notification,
    restored_parent,
    restored_style,
    visible_position,
)

# A plausible pre-embed style for a game window: normal framed top-level.
OVERLAPPED = WS_VISIBLE | FRAME_STYLES

GAME_HWND = 0x1234
TILE_HWND = 0x5678
OWNER_HWND = 0x9ABC
ORIG_RECT = (300, 200, 1100, 800)  # 800 x 600 outer
CLIENT_SIZE = (784, 561)


class TestChildStyle:
    def test_strips_every_frame_style(self) -> None:
        style = child_style(OVERLAPPED)
        assert style & FRAME_STYLES == 0
        assert style & WS_CHILD
        assert style & WS_VISIBLE

    def test_clears_ws_popup(self) -> None:
        """WS_POPUP | WS_CHILD is an invalid combination; a popup game window must not hit it."""
        style = child_style(OVERLAPPED | WS_POPUP)
        assert style & WS_POPUP == 0
        assert style & WS_CHILD

    def test_preserves_unrelated_bits(self) -> None:
        marker = 0x00000004  # WS_DISABLED, nothing to do with framing
        assert child_style(OVERLAPPED | marker) & marker


class TestRestoredStyle:
    def test_clears_child_and_restores_frame(self) -> None:
        style = restored_style(OVERLAPPED)
        assert style & WS_CHILD == 0
        assert style & WS_CAPTION == WS_CAPTION
        assert style & WS_THICKFRAME
        assert style & WS_SYSMENU
        assert style & (WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        assert style & WS_VISIBLE

    def test_recovers_from_an_already_embedded_capture(self) -> None:
        """A style captured while the window was still a child must still come back movable."""
        corrupt = child_style(OVERLAPPED)
        style = restored_style(corrupt)
        assert style & WS_CHILD == 0
        assert style & WS_THICKFRAME
        assert style & WS_CAPTION == WS_CAPTION


class TestRestoredParent:
    def test_top_level_window_goes_back_to_the_desktop(self) -> None:
        """GetParent returns the *owner* for a top-level window; reparenting to it would trap it."""
        assert restored_parent(OWNER_HWND, OVERLAPPED) == 0

    def test_real_child_goes_back_to_its_parent(self) -> None:
        assert restored_parent(OWNER_HWND, OVERLAPPED | WS_CHILD) == OWNER_HWND

    def test_no_parent_stays_zero(self) -> None:
        assert restored_parent(0, OVERLAPPED) == 0


class TestVisiblePosition:
    MONITORS = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]

    def test_keeps_an_on_screen_position(self) -> None:
        assert visible_position((300, 200, 1100, 800), self.MONITORS) == (300, 200)

    def test_keeps_a_position_on_a_secondary_monitor(self) -> None:
        assert visible_position((2000, 100, 2800, 700), self.MONITORS) == (2000, 100)

    def test_relocates_a_position_on_a_vanished_monitor(self) -> None:
        x, y = visible_position((5000, 100, 5800, 700), self.MONITORS)
        assert (x, y) == (560, 240)  # centered on the primary

    def test_no_monitors_leaves_the_position_alone(self) -> None:
        assert visible_position((300, 200, 1100, 800), []) == (300, 200)


class FakeUser32:
    """Records every call, and answers the reads EmbeddedWindow makes at capture time."""

    def __init__(self, parent: int = OWNER_HWND, style: int = OVERLAPPED, ex_style: int = 0) -> None:
        self.calls: list[tuple] = []
        self._parent = parent
        self._styles = {GWL_STYLE: style, GWL_EXSTYLE: ex_style}

    def _record(self, name: str, *args) -> None:
        self.calls.append((name,) + args)

    @property
    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def call(self, name: str) -> tuple:
        matches = [c for c in self.calls if c[0] == name]
        assert len(matches) == 1, f"expected exactly one {name} call, got {len(matches)}"
        return matches[0]

    # -- reads -------------------------------------------------------------

    def GetParent(self, hwnd):
        return self._parent

    def GetWindowLongW(self, hwnd, index):
        return self._styles[index]

    def GetWindowRect(self, hwnd, out):
        rect = out._obj  # byref() wrapper around the caller's RECT
        rect.left, rect.top, rect.right, rect.bottom = ORIG_RECT
        return True

    def GetClientRect(self, hwnd, out):
        rect = out._obj
        rect.left, rect.top = 0, 0
        rect.right, rect.bottom = CLIENT_SIZE
        return True

    def GetWindowThreadProcessId(self, hwnd, out):
        return 4242

    # -- writes ------------------------------------------------------------

    def SetParent(self, hwnd, parent):
        self._record("SetParent", hwnd, parent)
        return 0

    def SetWindowLongW(self, hwnd, index, value):
        self._record("SetWindowLongW", hwnd, index, value)
        return 0

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self._record("SetWindowPos", hwnd, after, x, y, cx, cy, flags)
        return True

    def MoveWindow(self, hwnd, x, y, w, h, repaint):
        self._record("MoveWindow", hwnd, x, y, w, h, repaint)
        return True

    def ShowWindow(self, hwnd, cmd):
        self._record("ShowWindow", hwnd, cmd)
        return True

    def AttachThreadInput(self, a, b, attach):
        self._record("AttachThreadInput", a, b, attach)
        return True

    def SetFocus(self, hwnd):
        self._record("SetFocus", hwnd)
        return 0


class FakeKernel32:
    def GetCurrentThreadId(self):
        return 1111


@pytest.fixture(autouse=True)
def _fake_monitors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out monitor enumeration — it needs a live QGuiApplication."""
    monkeypatch.setattr(
        "bartlebot.gui.game_embed.win32_window._monitor_rects",
        lambda: [(0, 0, 1920, 1080)],
    )


@pytest.fixture
def user32() -> FakeUser32:
    return FakeUser32()


@pytest.fixture
def window(user32: FakeUser32) -> EmbeddedWindow:
    return EmbeddedWindow(GAME_HWND, user32=user32, kernel32=FakeKernel32())


class TestEmbeddedWindowCapture:
    def test_captures_native_resolution_before_embedding(self, window: EmbeddedWindow) -> None:
        assert window.game_size == CLIENT_SIZE

    def test_starts_unembedded(self, window: EmbeddedWindow) -> None:
        assert not window.is_embedded


class TestEmbed:
    def test_reparents_strips_frame_and_attaches_input(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        window.embed(TILE_HWND)

        assert user32.call("SetParent")[1:] == (GAME_HWND, TILE_HWND)
        assert user32.call("SetWindowLongW")[1:] == (GAME_HWND, GWL_STYLE, child_style(OVERLAPPED))
        assert user32.call("SetWindowPos")[7] & SWP_FRAMECHANGED
        assert user32.call("AttachThreadInput")[1:] == (1111, 4242, True)
        assert window.is_embedded


class TestIsClickNotification:
    """#93: the tile grants focus on the WM_PARENTNOTIFY a press on the game sends it."""

    WM_PARENTNOTIFY = 0x0210

    @pytest.mark.parametrize("button_down", [0x0201, 0x0204, 0x0207, 0x020B])  # L, R, M, X
    def test_every_button_press(self, button_down: int) -> None:
        assert is_click_notification(self.WM_PARENTNOTIFY, button_down)

    def test_reads_only_the_low_word(self) -> None:
        """The high word carries the X button's identity (or a child id), not the event."""
        assert is_click_notification(self.WM_PARENTNOTIFY, 0x0002_020B)

    @pytest.mark.parametrize("event", [0x0001, 0x0002])  # WM_CREATE, WM_DESTROY
    def test_not_a_child_being_created_or_destroyed(self, event: int) -> None:
        assert not is_click_notification(self.WM_PARENTNOTIFY, event)

    def test_not_a_button_press_sent_to_the_tile_itself(self) -> None:
        assert not is_click_notification(0x0201, 0x0201)


class TestRestore:
    def test_puts_the_window_back_where_it_was(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        window.embed(TILE_HWND)
        window.move(0, 39, *CLIENT_SIZE)  # what the tile does to it
        user32.calls.clear()

        window.restore()

        _, hwnd, after, x, y, cx, cy, flags = user32.call("SetWindowPos")
        assert hwnd == GAME_HWND
        assert (x, y) == (ORIG_RECT[0], ORIG_RECT[1])
        assert (cx, cy) == (ORIG_RECT[2] - ORIG_RECT[0], ORIG_RECT[3] - ORIG_RECT[1])
        assert flags & SWP_FRAMECHANGED

    def test_restores_style_before_reparenting(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        """WS_CHILD has to be gone before SetParent, or the window never repaints a frame."""
        window.embed(TILE_HWND)
        user32.calls.clear()

        window.restore()

        names = user32.names
        assert names.index("SetWindowLongW") < names.index("SetParent")
        assert names.index("SetParent") < names.index("SetWindowPos")
        assert names[-1] == "ShowWindow"

    def test_restores_frame_styles_and_taskbar_button(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        window.embed(TILE_HWND)
        user32.calls.clear()

        window.restore()

        writes = {c[2]: c[3] for c in user32.calls if c[0] == "SetWindowLongW"}
        assert writes[GWL_STYLE] == restored_style(OVERLAPPED)
        assert writes[GWL_EXSTYLE] & WS_EX_APPWINDOW

    def test_reparents_to_the_desktop_not_the_owner(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        window.embed(TILE_HWND)
        user32.calls.clear()

        window.restore()

        assert user32.call("SetParent")[1:] == (GAME_HWND, 0)

    def test_detaches_input_queues_once(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        window.embed(TILE_HWND)
        window.restore()

        attaches = [c for c in user32.calls if c[0] == "AttachThreadInput"]
        assert [c[3] for c in attaches] == [True, False]

    def test_is_idempotent(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        """Unembed is reachable from several paths; a second one must not unbalance anything."""
        window.embed(TILE_HWND)
        window.restore()
        user32.calls.clear()

        window.restore()

        assert user32.calls == []

    def test_does_nothing_if_never_embedded(self, window: EmbeddedWindow, user32: FakeUser32) -> None:
        window.restore()
        assert user32.calls == []


class TestRestoreWithoutQt:
    def test_falls_back_to_the_saved_position_when_monitors_are_unknown(
        self, window: EmbeddedWindow, user32: FakeUser32, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("bartlebot.gui.game_embed.win32_window._monitor_rects", lambda: [])
        window.embed(TILE_HWND)
        user32.calls.clear()

        window.restore()

        _, _, _, x, y, _, _, _ = user32.call("SetWindowPos")
        assert (x, y) == (ORIG_RECT[0], ORIG_RECT[1])
