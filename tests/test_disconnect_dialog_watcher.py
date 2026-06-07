"""Tests for ClientHealthMonitor._disconnect_dialog_watcher.

Covers:
- Detection fires when MessageBoxModalWindow is visible with a disconnect caption.
- Detection does NOT fire for non-disconnect modals (caption mismatch).
- Already-flagged clients are skipped.
- Clients with no modal visible are skipped.
- _is_disconnect_modal_visible returns False when modal root is missing.
- _is_disconnect_modal_visible returns False when caption text is absent.
- Force-kill failure is swallowed and recovery still fires.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.bot.client_health_monitor import ClientHealthMonitor, _traverse_path

# ---------------------------------------------------------------------------
# Fake window tree helpers
# ---------------------------------------------------------------------------


def _make_window(name: str, visible: bool = False, text: str | None = None, children: list | None = None):
    """Create a fake UI window node with async-compatible attributes."""
    w = MagicMock()
    w.name = AsyncMock(return_value=name)
    w.is_visible = AsyncMock(return_value=visible)
    w.maybe_text = AsyncMock(return_value=text)
    w.children = AsyncMock(return_value=children or [])
    return w


def _make_disconnect_tree(caption_text: str, modal_visible: bool = True):
    """Build a minimal MessageBoxModalWindow tree with the given caption."""
    caption = _make_window("CaptionText", text=caption_text)
    text_area = _make_window("TextArea", children=[caption])
    layout = _make_window("messageBoxLayout", children=[text_area])
    bg = _make_window("messageBoxBG", children=[layout])
    modal = _make_window("MessageBoxModalWindow", visible=modal_visible, children=[bg])
    root = _make_window("root", children=[modal])
    return root


def _make_client(hwnd: int = 1234, pid: int = 9999, root_window=None):
    client = MagicMock()
    client.window_handle = hwnd
    client.process_id = pid
    client.root_window = root_window or _make_window("root")
    return client


# ---------------------------------------------------------------------------
# Monitor factory
# ---------------------------------------------------------------------------


def _make_monitor(clients, flagged: set | None = None):
    stop_flag = asyncio.Event()
    on_pause = MagicMock()
    on_disconnected = MagicMock()
    monitor = ClientHealthMonitor(
        clients_ref=lambda: clients,
        stop_flag=stop_flag,
        on_pause=on_pause,
        on_client_disconnected=on_disconnected,
    )
    if flagged:
        monitor._flagged = flagged
    return monitor, stop_flag, on_pause, on_disconnected


# ---------------------------------------------------------------------------
# _traverse_path unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traverse_path_finds_nested_node():
    c = _make_window("C")
    b = _make_window("B", children=[c])
    a = _make_window("A", children=[b])
    result = await _traverse_path(a, ["B", "C"])
    assert result is c


@pytest.mark.asyncio
async def test_traverse_path_returns_none_for_missing_segment():
    a = _make_window("A", children=[])
    result = await _traverse_path(a, ["B"])
    assert result is None


@pytest.mark.asyncio
async def test_traverse_path_empty_path_returns_parent():
    a = _make_window("A")
    result = await _traverse_path(a, [])
    assert result is a


# ---------------------------------------------------------------------------
# _is_disconnect_modal_visible unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_disconnect_modal_visible_true_on_disconnect_text():
    root = _make_disconnect_tree("You have been disconnected from the game.")
    client = _make_client(root_window=root)
    result = await ClientHealthMonitor._is_disconnect_modal_visible(client)
    assert result is True


@pytest.mark.asyncio
async def test_is_disconnect_modal_visible_false_on_non_matching_text():
    root = _make_disconnect_tree("Are you sure you want to exit?")
    client = _make_client(root_window=root)
    result = await ClientHealthMonitor._is_disconnect_modal_visible(client)
    assert result is False


@pytest.mark.asyncio
async def test_is_disconnect_modal_visible_false_when_modal_not_visible():
    root = _make_disconnect_tree("You have been disconnected.", modal_visible=False)
    client = _make_client(root_window=root)
    result = await ClientHealthMonitor._is_disconnect_modal_visible(client)
    assert result is False


@pytest.mark.asyncio
async def test_is_disconnect_modal_visible_false_when_no_modal():
    root = _make_window("root", children=[])
    client = _make_client(root_window=root)
    result = await ClientHealthMonitor._is_disconnect_modal_visible(client)
    assert result is False


@pytest.mark.asyncio
async def test_is_disconnect_modal_visible_false_when_caption_text_empty():
    root = _make_disconnect_tree(caption_text=None)
    client = _make_client(root_window=root)
    result = await ClientHealthMonitor._is_disconnect_modal_visible(client)
    assert result is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Connection lost.",
        "Lost connection to server.",
        "Network error occurred.",
        "You have been kicked.",
        "DISCONNECTED",  # uppercase — matching is case-insensitive
    ],
)
async def test_is_disconnect_modal_visible_matches_all_keywords(text):
    root = _make_disconnect_tree(text)
    client = _make_client(root_window=root)
    result = await ClientHealthMonitor._is_disconnect_modal_visible(client)
    assert result is True


# ---------------------------------------------------------------------------
# _disconnect_dialog_watcher integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watcher_detects_disconnect_fires_recovery():
    root = _make_disconnect_tree("You have been disconnected.")
    client = _make_client(hwnd=1, pid=100, root_window=root)
    monitor, stop_flag, on_pause, on_disconnected = _make_monitor([client])

    with patch.object(ClientHealthMonitor, "_force_kill_process") as mock_kill:
        task = asyncio.create_task(monitor._disconnect_dialog_watcher())
        await asyncio.sleep(0.4)  # allow one POLL_INTERVAL (0.25 s) to pass
        stop_flag.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    mock_kill.assert_called_once_with(100)
    on_pause.assert_called_once()
    on_disconnected.assert_called_once_with(1)
    assert 1 in monitor._flagged


@pytest.mark.asyncio
async def test_watcher_does_not_fire_for_non_disconnect_modal():
    root = _make_disconnect_tree("Are you sure you want to exit?")
    client = _make_client(hwnd=2, root_window=root)
    monitor, stop_flag, on_pause, on_disconnected = _make_monitor([client])

    with patch.object(ClientHealthMonitor, "_force_kill_process") as mock_kill:
        task = asyncio.create_task(monitor._disconnect_dialog_watcher())
        await asyncio.sleep(0.4)
        stop_flag.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    mock_kill.assert_not_called()
    on_pause.assert_not_called()
    assert 2 not in monitor._flagged


@pytest.mark.asyncio
async def test_watcher_skips_already_flagged_client():
    root = _make_disconnect_tree("You have been disconnected.")
    client = _make_client(hwnd=3, pid=200, root_window=root)
    monitor, stop_flag, on_pause, on_disconnected = _make_monitor([client], flagged={3})

    with patch.object(ClientHealthMonitor, "_force_kill_process") as mock_kill:
        task = asyncio.create_task(monitor._disconnect_dialog_watcher())
        await asyncio.sleep(0.4)
        stop_flag.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    mock_kill.assert_not_called()
    on_pause.assert_not_called()


@pytest.mark.asyncio
async def test_watcher_does_not_fire_when_no_modal():
    root = _make_window("root", children=[])
    client = _make_client(hwnd=4, root_window=root)
    monitor, stop_flag, on_pause, on_disconnected = _make_monitor([client])

    with patch.object(ClientHealthMonitor, "_force_kill_process") as mock_kill:
        task = asyncio.create_task(monitor._disconnect_dialog_watcher())
        await asyncio.sleep(0.4)
        stop_flag.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    mock_kill.assert_not_called()
    on_pause.assert_not_called()


@pytest.mark.asyncio
async def test_watcher_still_fires_recovery_when_kill_raises():
    """A TerminateProcess failure must not prevent _fire_disconnected from running."""
    root = _make_disconnect_tree("You have been disconnected.")
    client = _make_client(hwnd=5, pid=300, root_window=root)
    monitor, stop_flag, on_pause, on_disconnected = _make_monitor([client])

    with patch.object(ClientHealthMonitor, "_force_kill_process", side_effect=OSError("access denied")):
        task = asyncio.create_task(monitor._disconnect_dialog_watcher())
        await asyncio.sleep(0.4)
        stop_flag.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    on_pause.assert_called_once()
    on_disconnected.assert_called_once_with(5)
