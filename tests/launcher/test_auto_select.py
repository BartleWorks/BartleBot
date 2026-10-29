"""Tests for the auto-select driver: launch → loading screens → character select → in-game.

Everything here runs against a recorder standing in for ctypes.windll.user32, so no real
window is ever touched and no key is ever really pressed. FakeGameClient stands in for the
borrowed WizWalker client, modelling the screen sequence tools/probe_char_select.py
measured: the root window becomes readable a couple of seconds in, WorldView/mainWindow is
absent through the loading screens, present for the whole of character select, and gone
again once Play is taken.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bartleby.runtime.launcher import auto_select
from bartleby.runtime.launcher.auto_select import (
    _VK_RETURN,
    _WM_KEYDOWN,
)
from bartleby.runtime.paths import CHAR_SELECT_PATH

from bartlebot.clients.client_manager import ClientManager

TITLE = "[Alpha] Wizard101"

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class KeyRecorder:
    """Stands in for ctypes.windll, recording every PostMessageW call."""

    def __init__(self, hwnd: int = 4242) -> None:
        self._hwnd = hwnd
        self._closed = False
        self.posted: list[tuple[int, int, int]] = []
        self.on_key = None
        self.user32 = MagicMock()
        self.user32.FindWindowW.side_effect = lambda _cls, _title: self._hwnd
        self.user32.PostMessageW.side_effect = self._post
        self.user32.IsWindow.side_effect = lambda _hwnd: 0 if self._closed else 1

    def _post(self, hwnd: int, msg: int, vk: int, _extra: int) -> None:
        self.posted.append((hwnd, msg, vk))
        if msg == _WM_KEYDOWN and self.on_key:
            self.on_key(vk)

    @property
    def keys_down(self) -> list[int]:
        """Just the key-down virtual keycodes — one entry per discrete press."""
        return [vk for _hwnd, msg, vk in self.posted if msg == _WM_KEYDOWN]

    def window_missing(self) -> None:
        self._hwnd = 0

    def window_closed(self) -> None:
        """The user closed the game window: no handle resolves, and none is a window."""
        self._closed = True
        self._hwnd = 0


class FakeGameClient:
    """A launching client, walked through the screens the probe recorded.

    `screen` moves loading → charselect after `loading_polls` reads, and charselect →
    world when Enter arrives. `root_ready` gates whether the tree reads at all.
    """

    def __init__(self, *, root_ready: bool = True, loading_polls: int = 3) -> None:
        self.window_handle = 999
        self.root_ready = root_ready
        self.screen = "loading"
        self.polls = 0
        self._loading_polls = loading_polls
        self.hook_handler = MagicMock()
        self.hook_handler.read_current_root_window_base = self._read_root

    async def _read_root(self) -> int:
        if not self.root_ready:
            raise RuntimeError("HookNotActive")
        return 0x1AC9C7F3D70

    async def is_visible(self, path: list[str]) -> bool:
        if path != CHAR_SELECT_PATH:
            return False
        self.polls += 1
        if self.screen == "loading" and self.polls > self._loading_polls:
            self.screen = "charselect"
        return self.screen == "charselect"

    def on_key(self, vk: int) -> None:
        if vk == _VK_RETURN and self.screen == "charselect":
            self.screen = "world"


class Hooked:
    """is_hooked callback that flips to True after a set number of polls."""

    def __init__(self, after: int | None) -> None:
        self._after = after
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self._after is not None and self.calls > self._after


@pytest.fixture
def keys():
    recorder = KeyRecorder()
    with patch.object(auto_select.ctypes, "windll", recorder):
        yield recorder


@pytest.fixture
def fast(monkeypatch):
    """Compress the real cadences so the suite does not spend two minutes asleep.

    These values are balanced against each other and are not free to shrink further.
    AUTO_SELECT_TREE_TIMEOUT has to stay long enough for a loaded machine to read a fake
    tree, or the sighted tests silently take the blind fallback path and stop testing what
    they name; it also has to expire before ``Hooked(after=40)`` fires at the key interval
    above, or the blind fallback tests never reach the fallback at all. Tightening either
    one breaks tests at the other end of that range.
    """
    monkeypatch.setattr(auto_select, "AUTO_SELECT_FIRST_SCREEN_DELAY", 0.03)
    monkeypatch.setattr(auto_select, "AUTO_SELECT_KEY_INTERVAL", 0.01)
    monkeypatch.setattr(auto_select, "AUTO_SELECT_TREE_TIMEOUT", 0.2)
    monkeypatch.setattr(auto_select, "AUTO_SELECT_CLEANUP_INTERVAL", 0.0)
    monkeypatch.setattr(auto_select, "_KEY_HOLD", 0.0)


@pytest.fixture
def game(keys):
    """A readable launching client, wired so its screen reacts to keypresses."""
    client = FakeGameClient()
    keys.on_key = client.on_key
    with patch.object(auto_select, "is_visible_by_path", lambda c, path: c.is_visible(path)):
        yield client


# ---------------------------------------------------------------------------
# The deterministic path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The blind fallback
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# drive_to_character_select — the auto-select-off drive
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# wait_for_client_in — the auto-select-off path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# drive_client_in — drive plus cleanup
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# clean_up_after_auto_select
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The setting gate — ClientManager.drive_client_in is the only place it is read
# ---------------------------------------------------------------------------


class _Gate:
    """Minimal host for the ClientManager methods that read the setting."""

    drive_client_in = ClientManager.drive_client_in
    await_client_in = ClientManager.await_client_in
    has_client_titled = ClientManager.has_client_titled
    client_titled = ClientManager.client_titled
    pending_client_titled = ClientManager.pending_client_titled

    def __init__(self) -> None:
        self._clients: list = []
        self._handler = MagicMock(clients=[])


async def test_setting_off_drives_only_as_far_as_character_select(keys, fast):
    """Auto-select off still clears the loading screens — it just picks no character.

    False is still the answer: the client is not in-game, and recovery has to keep waiting.
    """
    driver = AsyncMock(return_value=True)
    with (
        patch("bartlebot.clients.client_manager.SettingsManager") as sm,
        patch.object(auto_select, "drive_to_character_select", driver),
        patch.object(auto_select, "drive_client_in", AsyncMock(return_value=True)) as full,
    ):
        sm.instance.return_value = MagicMock(auto_select_character=False)
        reached = await _Gate().drive_client_in(TITLE)
    assert reached is False
    driver.assert_awaited_once()
    full.assert_not_awaited()


async def test_setting_on_runs_the_drive(keys, fast):
    driver = AsyncMock(return_value=True)
    with (
        patch("bartlebot.clients.client_manager.SettingsManager") as sm,
        patch.object(auto_select, "drive_client_in", driver),
    ):
        sm.instance.return_value = MagicMock(auto_select_character=True)
        reached = await _Gate().drive_client_in(TITLE)
    assert reached is True
    driver.assert_awaited_once()


async def test_driver_borrows_the_pending_client(keys, fast, monkeypatch):
    """The client must come from the handler, hooks up or not — never a fresh one.

    A second Client over the same process re-prepares WizWalker's autobot region and
    corrupts the hooks the first one installed.
    """
    pending = FakeGameClient()
    gate = _Gate()
    gate._handler = MagicMock(clients=[pending])
    monkeypatch.setattr("bartleby.runtime.clients.client_manager.get_window_title", lambda _h: TITLE)

    assert gate.pending_client_titled(TITLE) is pending
    assert gate.client_titled(TITLE) is None  # not published yet


# ---------------------------------------------------------------------------
# A window that goes away mid-drive (issue #65)
#
# The drive's exits were all "not yet" — hooked, at character select, or out of time — so
# a window the user closed while it was still coming up left the drive pressing keys into
# a dead handle for the rest of its timeout. That timeout is exactly how long the launch
# stayed in flight, and an in-flight launch is one of the two things that make an account
# unlaunchable.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Launcher plumbing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Aiming a keypress at an embedded window
#
# A launched window gets a game-view tile as soon as its title identifies it, which is
# well before the drive is finished with it — so from that moment it is a *child* window
# and FindWindowW, which searches top-level windows only, no longer finds it.
# ---------------------------------------------------------------------------
