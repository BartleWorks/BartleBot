"""Tests for ClientHealthMonitor._network_link_watcher.

The game does not always show a disconnect modal when a connection fails; sometimes the
only sign is the HUD's network status icon (#75). Each icon's sprite sets its own visible
flag only while that state is showing, while the image inside it always reads visible.

Covers:
- Broken recovers after NETWORK_LINK_BROKEN_POLLS consecutive polls, not before.
- Bad recovers after NETWORK_LINK_BAD_TIMEOUT, and its clock restarts when the icon clears.
- Slow warns and never recovers.
- A visible image under a hidden sprite is not a signal.
- A loading screen pauses a streak without resetting it; a failed read leaves it untouched.
- Flagged clients are skipped, and a failed kill still fires recovery.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.bot.client_health_monitor import (
    LINK_BAD,
    LINK_BROKEN,
    LINK_SLOW,
    ClientHealthMonitor,
    _network_link_level,
)
from bartlebot.nodes.timing import NETWORK_LINK_BAD_TIMEOUT, NETWORK_LINK_BROKEN_POLLS

_SPRITE_NAMES = {
    LINK_SLOW: "spriteNetworkLinkSlow",
    LINK_BAD: "spriteNetworkLinkBad",
    LINK_BROKEN: "spriteNetworkLinkBroken",
}


def _make_window(name: str, visible: bool = False, children: list | None = None):
    w = MagicMock()
    w.name = AsyncMock(return_value=name)
    w.is_visible = AsyncMock(return_value=visible)
    w.children = AsyncMock(return_value=children or [])
    return w


def _make_hud_client(hwnd: int = 1, pid: int = 100, showing: str | None = None):
    """A client whose HUD holds all three icons, each with an always-visible image inside.

    Returns the client and the sprites by level, so a test can change what is showing
    between polls by setting ``sprites[level].is_visible.return_value``.
    """
    sprites = {}
    for level, name in _SPRITE_NAMES.items():
        image = _make_window("spriteNetworkLinkImage", visible=True)
        sprites[level] = _make_window(name, visible=(level == showing), children=[image])
    other = _make_window("btnPotions", visible=True)
    hud = _make_window("windowHUD", visible=True, children=[other, *sprites.values()])
    world = _make_window("WorldView", visible=True, children=[hud])
    root = _make_window("root", children=[world])

    client = MagicMock()
    client.window_handle = hwnd
    client.process_id = pid
    client.root_window = root
    client.is_loading = AsyncMock(return_value=False)
    return client, sprites


def _show(sprites: dict, level: str | None) -> None:
    for name, sprite in sprites.items():
        sprite.is_visible.return_value = name == level


def _make_monitor(clients):
    stop_flag = asyncio.Event()
    on_pause = MagicMock()
    on_disconnected = MagicMock()
    monitor = ClientHealthMonitor(
        clients_ref=lambda: clients,
        stop_flag=stop_flag,
        on_pause=on_pause,
        on_client_disconnected=on_disconnected,
    )
    return monitor, stop_flag, on_pause, on_disconnected


@pytest.fixture
def kill():
    with patch.object(ClientHealthMonitor, "_force_kill_process") as mock_kill:
        yield mock_kill


# ---------------------------------------------------------------------------
# Reading the icons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [LINK_SLOW, LINK_BAD, LINK_BROKEN])
async def test_reads_the_visible_sprite(level):
    client, _ = _make_hud_client(showing=level)
    assert await _network_link_level(client) == level


async def test_a_visible_image_under_hidden_sprites_is_not_a_signal():
    """The images read visible on a healthy link; only the sprites mean anything."""
    client, _ = _make_hud_client(showing=None)
    assert await _network_link_level(client) is None


async def test_the_most_severe_visible_icon_wins():
    client, sprites = _make_hud_client(showing=LINK_SLOW)
    sprites[LINK_BROKEN].is_visible.return_value = True
    assert await _network_link_level(client) == LINK_BROKEN


async def test_no_hud_reads_as_no_signal():
    client, _ = _make_hud_client()
    client.root_window = _make_window("root", children=[])
    assert await _network_link_level(client) is None


# ---------------------------------------------------------------------------
# Broken
# ---------------------------------------------------------------------------


async def test_broken_recovers_after_consecutive_polls(kill):
    client, _ = _make_hud_client(hwnd=7, pid=700, showing=LINK_BROKEN)
    monitor, _, on_pause, on_disconnected = _make_monitor([client])
    streaks = {}

    for t in range(NETWORK_LINK_BROKEN_POLLS - 1):
        await monitor._check_network_link(client, streaks, float(t))
    on_disconnected.assert_not_called()

    await monitor._check_network_link(client, streaks, float(NETWORK_LINK_BROKEN_POLLS))
    kill.assert_called_once_with(700)
    on_pause.assert_called_once()
    on_disconnected.assert_called_once_with(7)
    assert monitor.is_flagged(7)
    assert 7 not in streaks


async def test_broken_that_clears_starts_its_count_over(kill):
    client, sprites = _make_hud_client(showing=LINK_BROKEN)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    for t in range(NETWORK_LINK_BROKEN_POLLS - 1):
        await monitor._check_network_link(client, streaks, float(t))
    _show(sprites, None)
    await monitor._check_network_link(client, streaks, 10.0)
    _show(sprites, LINK_BROKEN)
    for t in range(NETWORK_LINK_BROKEN_POLLS - 1):
        await monitor._check_network_link(client, streaks, 20.0 + t)

    on_disconnected.assert_not_called()


async def test_bad_turning_broken_starts_the_broken_count(kill):
    client, sprites = _make_hud_client(showing=LINK_BAD)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    for t in range(5):
        await monitor._check_network_link(client, streaks, float(t))
    _show(sprites, LINK_BROKEN)
    for t in range(NETWORK_LINK_BROKEN_POLLS - 1):
        await monitor._check_network_link(client, streaks, 5.0 + t)
    on_disconnected.assert_not_called()

    await monitor._check_network_link(client, streaks, 10.0)
    on_disconnected.assert_called_once()


# ---------------------------------------------------------------------------
# Bad
# ---------------------------------------------------------------------------


async def test_bad_recovers_only_after_the_timeout(kill):
    client, _ = _make_hud_client(hwnd=3, showing=LINK_BAD)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    await monitor._check_network_link(client, streaks, 0.0)
    await monitor._check_network_link(client, streaks, NETWORK_LINK_BAD_TIMEOUT - 1.0)
    on_disconnected.assert_not_called()

    await monitor._check_network_link(client, streaks, NETWORK_LINK_BAD_TIMEOUT)
    on_disconnected.assert_called_once_with(3)


async def test_bad_that_clears_restarts_its_clock(kill):
    client, sprites = _make_hud_client(showing=LINK_BAD)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    await monitor._check_network_link(client, streaks, 0.0)
    _show(sprites, None)
    await monitor._check_network_link(client, streaks, 10.0)
    _show(sprites, LINK_BAD)
    await monitor._check_network_link(client, streaks, 11.0)
    await monitor._check_network_link(client, streaks, 11.0 + NETWORK_LINK_BAD_TIMEOUT - 1.0)
    on_disconnected.assert_not_called()

    await monitor._check_network_link(client, streaks, 11.0 + NETWORK_LINK_BAD_TIMEOUT)
    on_disconnected.assert_called_once()


# ---------------------------------------------------------------------------
# Slow
# ---------------------------------------------------------------------------


async def test_slow_warns_once_and_never_recovers(kill, caplog):
    client, _ = _make_hud_client(hwnd=9, showing=LINK_SLOW)
    monitor, _, on_pause, on_disconnected = _make_monitor([client])
    streaks = {}

    with caplog.at_level(logging.WARNING, logger="bartlebot.bot.client_health_monitor"):
        for t in range(0, 600, 30):
            await monitor._check_network_link(client, streaks, float(t))

    on_pause.assert_not_called()
    on_disconnected.assert_not_called()
    warnings = [r for r in caplog.records if "shows slow" in r.getMessage()]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Loading, read failures, flags
# ---------------------------------------------------------------------------


async def test_loading_pauses_the_bad_clock_without_resetting_it(kill):
    client, _ = _make_hud_client(showing=LINK_BAD)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    await monitor._check_network_link(client, streaks, 0.0)
    await monitor._check_network_link(client, streaks, 10.0)
    client.is_loading.return_value = True
    await monitor._check_network_link(client, streaks, 11.0)
    await monitor._check_network_link(client, streaks, 100.0)
    client.is_loading.return_value = False

    # 10 s before the screen, 0 s after it: the 89 s spent loading is not counted.
    await monitor._check_network_link(client, streaks, 101.0)
    on_disconnected.assert_not_called()

    await monitor._check_network_link(client, streaks, 101.0 + NETWORK_LINK_BAD_TIMEOUT - 10.0)
    on_disconnected.assert_called_once()


async def test_loading_does_not_reset_the_broken_count(kill):
    client, _ = _make_hud_client(showing=LINK_BROKEN)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    for t in range(NETWORK_LINK_BROKEN_POLLS - 1):
        await monitor._check_network_link(client, streaks, float(t))
    client.is_loading.return_value = True
    await monitor._check_network_link(client, streaks, 5.0)
    on_disconnected.assert_not_called()

    client.is_loading.return_value = False
    await monitor._check_network_link(client, streaks, 6.0)
    on_disconnected.assert_called_once()


async def test_a_failed_read_leaves_the_streak_alone(kill):
    client, _ = _make_hud_client(showing=LINK_BROKEN)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    for t in range(NETWORK_LINK_BROKEN_POLLS - 1):
        await monitor._check_network_link(client, streaks, float(t))
    client.is_loading.side_effect = RuntimeError("read failed")
    await monitor._check_network_link(client, streaks, 5.0)
    on_disconnected.assert_not_called()

    client.is_loading.side_effect = None
    await monitor._check_network_link(client, streaks, 6.0)
    on_disconnected.assert_called_once()


async def test_a_flagged_client_is_skipped_and_its_streak_dropped(kill):
    client, _ = _make_hud_client(hwnd=4, showing=LINK_BROKEN)
    monitor, _, _, on_disconnected = _make_monitor([client])
    streaks = {}

    await monitor._check_network_link(client, streaks, 0.0)
    monitor.flag(4)
    for t in range(1, 10):
        await monitor._check_network_link(client, streaks, float(t))

    on_disconnected.assert_not_called()
    assert 4 not in streaks


async def test_recovery_still_fires_when_the_kill_raises():
    client, _ = _make_hud_client(hwnd=5, showing=LINK_BROKEN)
    monitor, _, on_pause, on_disconnected = _make_monitor([client])
    streaks = {}

    with patch.object(ClientHealthMonitor, "_force_kill_process", side_effect=OSError("access denied")):
        for t in range(NETWORK_LINK_BROKEN_POLLS):
            await monitor._check_network_link(client, streaks, float(t))

    on_pause.assert_called_once()
    on_disconnected.assert_called_once_with(5)


# ---------------------------------------------------------------------------
# The loop itself
# ---------------------------------------------------------------------------


async def test_watcher_recovers_a_broken_client_once(kill, monkeypatch):
    monkeypatch.setattr("bartlebot.bot.client_health_monitor.NETWORK_LINK_POLL_INTERVAL", 0.005)
    broken, _ = _make_hud_client(hwnd=1, pid=10, showing=LINK_BROKEN)
    healthy, _ = _make_hud_client(hwnd=2, pid=20, showing=None)
    monitor, stop_flag, _, on_disconnected = _make_monitor([broken, healthy])

    task = asyncio.create_task(monitor._network_link_watcher())
    # Several times the polls recovery needs, so a second report would have had room to happen.
    await asyncio.sleep(0.005 * NETWORK_LINK_BROKEN_POLLS * 10)
    stop_flag.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    kill.assert_called_once_with(10)
    on_disconnected.assert_called_once_with(1)
