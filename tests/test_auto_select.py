"""Tests for the auto-select driver: launch → loading screens → character select → in-game.

Everything here runs against a recorder standing in for ctypes.windll.user32, so no real
window is ever touched and no key is ever really pressed. FakeGameClient stands in for the
borrowed WizWalker client, modelling the screen sequence tools/probe_char_select.py
measured: the root window becomes readable a couple of seconds in, WorldView/mainWindow is
absent through the loading screens, present for the whole of character select, and gone
again once Play is taken.
"""

import asyncio
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.clients.client_manager import ClientManager
from bartlebot.clients.launch_tracker import LaunchStage
from bartlebot.launcher import auto_select
from bartlebot.launcher.auto_select import (
    _VK_RETURN,
    _VK_SPACE,
    _WM_KEYDOWN,
    auto_select_character,
    drive_client_in,
    drive_to_character_select,
    wait_for_client_in,
)
from bartlebot.launcher.launch_profile import launch_account
from bartlebot.nodes.paths import CHAR_SELECT_PATH

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


class HookedWhenInWorld:
    """The real completion signal: hooks come up once the character is in a game zone."""

    def __init__(self, client: FakeGameClient) -> None:
        self._client = client

    def __call__(self) -> bool:
        return self._client.screen == "world"


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


async def test_enter_is_pressed_exactly_once_on_character_select(keys, fast, game):
    """The whole point of reading the screen: no blind Enter, ever."""
    reached = await auto_select_character(TITLE, HookedWhenInWorld(game), lambda: game)
    assert reached is True
    assert keys.keys_down.count(_VK_RETURN) == 1


async def test_loading_screens_are_skipped_with_space_not_enter(keys, fast, game):
    """Enter on an unidentified screen could confirm a modal; Space is the safe skip."""
    await auto_select_character(TITLE, HookedWhenInWorld(game), lambda: game)
    before_enter = keys.keys_down[: keys.keys_down.index(_VK_RETURN)]
    assert before_enter, "expected the loading screens to be skipped"
    assert set(before_enter) == {_VK_SPACE}


async def test_nothing_is_pressed_after_play_is_taken(keys, fast, game):
    """Once character select is gone the client is loading in — keys would hit the world."""
    await auto_select_character(TITLE, HookedWhenInWorld(game), lambda: game)
    after_enter = keys.keys_down[keys.keys_down.index(_VK_RETURN) + 1 :]
    assert after_enter == []


async def test_enter_is_retried_while_character_select_persists(keys, fast, game):
    """The screen swallows the first press or two while the Play button resolves."""
    swallowed = {"n": 0}

    def _on_key(vk: int) -> None:
        if vk == _VK_RETURN and game.screen == "charselect":
            swallowed["n"] += 1
            if swallowed["n"] >= 3:
                game.screen = "world"

    keys.on_key = _on_key
    reached = await auto_select_character(TITLE, HookedWhenInWorld(game), lambda: game)
    assert reached is True
    assert keys.keys_down.count(_VK_RETURN) == 3


async def test_gives_up_pressing_enter_after_the_cap(keys, fast, game, monkeypatch):
    """A character select that eats every press must not be hammered forever."""
    monkeypatch.setattr(auto_select, "AUTO_SELECT_MAX_ENTER", 3)
    keys.on_key = None  # Enter never takes
    reached = await auto_select_character(TITLE, Hooked(after=None), lambda: game, timeout=0.4)
    assert reached is False
    assert keys.keys_down.count(_VK_RETURN) == 3


async def test_returns_immediately_if_already_in_game(keys, fast, game):
    """A client that is already hooked must not be pressed at all."""
    reached = await auto_select_character(TITLE, lambda: True, lambda: game)
    assert reached is True
    assert keys.posted == []


async def test_no_keys_before_the_first_screen_delay(monkeypatch, keys, game):
    """The first loading screen ignores input for ~3 s, so nothing is sent before then."""
    monkeypatch.setattr(auto_select, "AUTO_SELECT_FIRST_SCREEN_DELAY", 0.2)
    monkeypatch.setattr(auto_select, "AUTO_SELECT_KEY_INTERVAL", 0.02)
    monkeypatch.setattr(auto_select, "_KEY_HOLD", 0.0)

    started = time.monotonic()
    first_press: list[float] = []
    original = keys.user32.PostMessageW.side_effect

    def _record(hwnd, msg, vk, extra):
        if msg == _WM_KEYDOWN and not first_press:
            first_press.append(time.monotonic() - started)
        return original(hwnd, msg, vk, extra)

    keys.user32.PostMessageW.side_effect = _record

    await auto_select_character(TITLE, HookedWhenInWorld(game), lambda: game)
    assert first_press, "expected at least one keypress"
    assert first_press[0] >= 0.2


# ---------------------------------------------------------------------------
# The blind fallback
# ---------------------------------------------------------------------------


async def test_falls_back_to_blind_when_the_tree_never_reads(keys, fast):
    """An unreadable tree degrades to the old behavior rather than failing the launch."""
    blind = FakeGameClient(root_ready=False)
    reached = await auto_select_character(TITLE, Hooked(after=40), lambda: blind)
    assert reached is True
    assert keys.keys_down
    assert set(keys.keys_down) == {_VK_RETURN}


async def test_falls_back_to_blind_when_no_client_is_available(keys, fast):
    """No borrowable client (never detected) is the other way sight can fail."""
    reached = await auto_select_character(TITLE, Hooked(after=40))
    assert reached is True
    assert set(keys.keys_down) == {_VK_RETURN}


async def test_timeout_returns_false_without_raising(keys, fast):
    """A client that never loads in gives up rather than hanging the launch."""
    assert await auto_select_character(TITLE, Hooked(after=None), timeout=0.3) is False


async def test_missing_window_does_not_raise(keys, fast):
    """FindWindowW returning 0 (window closed mid-drive) is survivable."""
    keys.window_missing()
    reached = await auto_select_character(TITLE, Hooked(after=None), timeout=0.3)
    assert reached is False
    assert keys.keys_down == []


async def test_press_failure_is_retried(keys, fast):
    """A failed ctypes call must not abort the whole drive."""
    calls = {"n": 0}
    original = keys.user32.PostMessageW.side_effect

    def _flaky(hwnd, msg, vk, extra):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("PostMessageW blew up")
        return original(hwnd, msg, vk, extra)

    keys.user32.PostMessageW.side_effect = _flaky
    reached = await auto_select_character(TITLE, Hooked(after=40))
    assert reached is True
    assert calls["n"] > 1


# ---------------------------------------------------------------------------
# drive_to_character_select — the auto-select-off drive
# ---------------------------------------------------------------------------


async def test_loading_screens_are_skipped_with_auto_select_off(keys, fast, game):
    """The screens carry no decision, so they are cleared either way."""
    reached = await drive_to_character_select(TITLE, lambda: False, lambda: game)
    assert reached is True
    assert _VK_SPACE in keys.keys_down


async def test_play_is_never_pressed_with_auto_select_off(keys, fast, game):
    """The whole point: the client is delivered to character select and left there."""
    await drive_to_character_select(TITLE, lambda: False, lambda: game)
    assert _VK_RETURN not in keys.keys_down
    assert game.screen == "charselect"


async def test_nothing_is_pressed_once_character_select_is_up(keys, fast, game):
    """The drive returns at character select rather than idling on it and pressing."""
    await drive_to_character_select(TITLE, lambda: False, lambda: game)
    assert keys.keys_down.count(_VK_SPACE) <= game.polls


async def test_no_blind_fallback_with_auto_select_off(keys, fast):
    """Blind means Enter, which picks a character — so there is no fallback here."""
    blind = FakeGameClient(root_ready=False)
    reached = await drive_to_character_select(TITLE, Hooked(after=None), lambda: blind)
    assert reached is False
    assert keys.posted == []


async def test_returns_immediately_if_the_client_is_already_in_game(keys, fast, game):
    reached = await drive_to_character_select(TITLE, lambda: True, lambda: game)
    assert reached is True
    assert keys.posted == []


async def test_character_select_drive_times_out_without_raising(keys, fast):
    stuck = FakeGameClient(loading_polls=10**6)
    with patch.object(auto_select, "is_visible_by_path", lambda c, path: c.is_visible(path)):
        reached = await drive_to_character_select(TITLE, Hooked(after=None), lambda: stuck, timeout=0.3)
    assert reached is False
    assert _VK_RETURN not in keys.keys_down


# ---------------------------------------------------------------------------
# wait_for_client_in — the auto-select-off path
# ---------------------------------------------------------------------------


async def test_wait_presses_nothing(keys, fast):
    """With auto-select off, recovery still waits, but must not touch the window."""
    assert await wait_for_client_in(TITLE, Hooked(after=3)) is True
    assert keys.posted == []


async def test_wait_times_out(keys, fast):
    assert await wait_for_client_in(TITLE, Hooked(after=None), timeout=0.05) is False


# ---------------------------------------------------------------------------
# drive_client_in — drive plus cleanup
# ---------------------------------------------------------------------------


async def test_drive_runs_cleanup_on_success(keys, fast, game):
    cleanup = AsyncMock()
    with patch.object(auto_select, "clean_up_after_auto_select", cleanup):
        reached = await drive_client_in(TITLE, HookedWhenInWorld(game), lambda: game)
    assert reached is True
    cleanup.assert_awaited_once_with(game, remember_realm=False)


async def test_drive_skips_cleanup_on_timeout(keys, fast):
    cleanup = AsyncMock()
    with patch.object(auto_select, "clean_up_after_auto_select", cleanup):
        reached = await drive_client_in(TITLE, Hooked(after=None), lambda: None, timeout=0.3)
    assert reached is False
    cleanup.assert_not_awaited()


async def test_drive_survives_a_missing_client(keys, fast):
    """Hooked but not yet retrievable is a race, not a failure."""
    cleanup = AsyncMock()
    with patch.object(auto_select, "clean_up_after_auto_select", cleanup):
        reached = await drive_client_in(TITLE, Hooked(after=2), lambda: None)
    assert reached is True
    cleanup.assert_not_awaited()


# ---------------------------------------------------------------------------
# clean_up_after_auto_select
# ---------------------------------------------------------------------------


async def test_cleanup_dismisses_the_crowns_shop(keys, fast):
    """The crowns shop opens by itself on login for some accounts."""
    client = MagicMock(window_handle=999)
    # ESC dance: settings already up, then gone. Then crowns shop: present, then gone.
    enabled = AsyncMock(side_effect=[True, False, True, False])
    click = AsyncMock()
    with (
        patch.object(auto_select, "is_enabled_by_path", enabled),
        patch.object(auto_select, "poll_until", AsyncMock(return_value=True)),
        patch.object(auto_select, "click_by_path", click),
    ):
        await auto_select.clean_up_after_auto_select(client)
    click.assert_awaited()


async def test_cleanup_never_raises(keys, fast):
    """Cleanup is cosmetic — it must not be able to fail an otherwise good launch."""
    client = MagicMock(window_handle=999)
    with patch.object(auto_select, "is_enabled_by_path", AsyncMock(side_effect=RuntimeError("boom"))):
        await auto_select.clean_up_after_auto_select(client)


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
    monkeypatch.setattr("bartlebot.clients.client_manager.get_window_title", lambda _h: TITLE)

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


async def test_a_closed_window_ends_the_sighted_drive(keys, fast, game):
    keys.on_key = lambda _vk: keys.window_closed()

    started = time.monotonic()
    reached = await auto_select_character(TITLE, Hooked(None), lambda: game, timeout=10)

    assert reached is False
    # The ceiling is what a regression would spend; the window is what the drive actually
    # costs -- a handful of polls -- kept an order of magnitude wider for a loaded runner.
    assert time.monotonic() - started < 2.0, "the drive waited out its timeout on a closed window"


async def test_a_closed_window_ends_the_blind_drive(keys, fast):
    """The fallback path has the same exit, learning the handle from its first press."""
    client = FakeGameClient(root_ready=False)
    keys.on_key = lambda _vk: keys.window_closed()

    started = time.monotonic()
    reached = await auto_select_character(TITLE, Hooked(None), lambda: client, timeout=10)

    assert reached is False
    assert time.monotonic() - started < 2.0


async def test_a_window_that_is_merely_embedded_does_not_end_the_drive(keys, fast, game):
    """FindWindowW stops finding a window the moment it is embedded — that is not a close."""
    keys.window_missing()
    reached = await auto_select_character(TITLE, HookedWhenInWorld(game), lambda: game)
    assert reached is True


# ---------------------------------------------------------------------------
# Launcher plumbing
# ---------------------------------------------------------------------------


@contextmanager
def _stubbed_launch():
    """Everything launch_account touches outside the drive, stubbed out."""
    with (
        patch("bartlebot.launcher.launch_profile.patch_open_browser"),
        patch("bartlebot.launcher.launch_profile._patch_window_config"),
        patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
        patch("bartlebot.launcher.launch_profile._start_and_claim_handle", AsyncMock(return_value=77)),
        patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
        patch("bartlebot.launcher.launch_profile.instance_login"),
        patch("bartlebot.launcher.launch_profile.set_window_title"),
        patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
    ):
        sm.instance.return_value = MagicMock(game_resolution=(800, 600))
        yield sm


async def test_launch_account_drives_the_new_window():
    """The launcher hands the drive the title it just set, and waits for it."""
    drive = AsyncMock(return_value=True)
    with _stubbed_launch():
        title = await launch_account("user1", "pw", "Alpha", drive=drive)

    assert title == TITLE
    drive.assert_awaited_once_with(TITLE)


async def test_launch_account_without_a_driver_does_not_wait():
    """drive=None is how a caller says 'leave it at character select'."""
    with _stubbed_launch():
        assert await launch_account("user1", "pw", "Alpha") == TITLE


async def test_a_drive_that_ends_because_its_window_closed_is_not_a_failure():
    """A failed attempt keeps its tile, and a tile keeps the account unlaunchable.

    So a window the user closed on purpose must not be filed as one -- that is the
    "cannot relaunch" bug (issue #65) coming back through the other door.
    """
    progress = MagicMock()
    # _stubbed_launch reports no live wizard handles, which is what a closed window is.
    with _stubbed_launch():
        await launch_account("user1", "pw", "Alpha", drive=AsyncMock(return_value=False), progress=progress)

    assert LaunchStage.FAILED not in [call.args[1] for call in progress.call_args_list]


async def test_profile_drives_every_account_concurrently():
    """Each account's drive starts as its own login is typed, not after the whole batch."""
    active = {"now": 0, "peak": 0}

    async def _drive(_title: str) -> bool:
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.05)
        active["now"] -= 1
        return True

    from bartlebot.launcher.launch_profile import launch_profile

    with (
        patch("bartlebot.launcher.launch_profile.patch_open_browser"),
        patch("bartlebot.launcher.launch_profile._patch_window_config"),
        patch("bartlebot.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
        patch("bartlebot.launcher.launch_profile._wait_for_n_new_handles", AsyncMock(return_value=[1, 2, 3])),
        patch("bartlebot.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
        patch("bartlebot.launcher.launch_profile.start_instance"),
        patch("bartlebot.launcher.launch_profile.instance_login"),
        patch("bartlebot.launcher.launch_profile.set_window_title"),
        patch("bartlebot.launcher.launch_profile.jitter", return_value=0.0),
        patch("bartlebot.launcher.launch_profile.CredentialStore") as creds,
        patch("bartlebot.launcher.launch_profile.SettingsManager") as sm,
    ):
        creds.retrieve.return_value = "pw"
        creds.display_name.side_effect = lambda u, _a: u
        sm.instance.return_value = MagicMock(
            game_resolution=(800, 600),
            launch_profiles=[{"name": "Team", "accounts": ["a", "b", "c"]}],
            accounts=[{"username": u} for u in ("a", "b", "c")],
        )
        await launch_profile("Team", drive=_drive)

    assert active["peak"] == 3


# ---------------------------------------------------------------------------
# Aiming a keypress at an embedded window
#
# A launched window gets a game-view tile as soon as its title identifies it, which is
# well before the drive is finished with it — so from that moment it is a *child* window
# and FindWindowW, which searches top-level windows only, no longer finds it.
# ---------------------------------------------------------------------------


def test_press_target_prefers_the_title_lookup(keys):
    """It re-resolves across a crash and relaunch mid-drive; a captured handle would not."""
    assert auto_select._press_target(TITLE, lambda: MagicMock(window_handle=999)) == 4242


def test_press_target_falls_back_to_the_borrowed_client(keys):
    """The regression this exists for: an embedded window is not top level."""
    keys.window_missing()
    assert auto_select._press_target(TITLE, lambda: MagicMock(window_handle=999)) == 999


def test_press_target_is_zero_when_there_is_nothing_to_aim_at(keys):
    keys.window_missing()
    assert auto_select._press_target(TITLE, lambda: None) == 0


async def test_the_drive_still_reaches_an_embedded_window(keys, fast, game):
    """End to end: the window is embedded before character select and Enter still lands."""
    keys.window_missing()  # FindWindowW finds nothing once the window is a child
    reached = await auto_select_character(TITLE, HookedWhenInWorld(game), lambda: game)
    assert reached is True
    assert keys.keys_down.count(_VK_RETURN) == 1
    assert {hwnd for hwnd, _msg, _vk in keys.posted} == {game.window_handle}
