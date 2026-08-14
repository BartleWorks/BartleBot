"""Unit tests for the chat-based drop detection helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

from bartlebot.utils import drop_detection

_SNAP = "bartlebot.utils.drop_detection.snapshot_chat"


def _window(name: str, children=(), visible: bool = True) -> MagicMock:
    window = MagicMock()
    window.name = AsyncMock(return_value=name)
    window.children = AsyncMock(return_value=list(children))
    window.is_visible = AsyncMock(return_value=visible)
    return window


def _client_with_chat(visible: bool = True) -> MagicMock:
    """Build a client whose root window contains the full chat log path."""
    window = _window("chatLog", visible=visible)
    for name in reversed(drop_detection._CHAT_PATH[:-1]):
        window = _window(name, children=[window])
    client = MagicMock()
    client.root_window = _window("Root", children=[window])
    return client


async def test_chat_available_true_when_path_resolves_and_visible():
    assert await drop_detection.chat_available(_client_with_chat()) is True


async def test_chat_available_false_when_hidden():
    assert await drop_detection.chat_available(_client_with_chat(visible=False)) is False


async def test_chat_available_false_when_path_missing():
    client = MagicMock()
    client.root_window = _window("Root", children=[])

    assert await drop_detection.chat_available(client) is False


async def test_poll_new_drops_returns_on_first_read():
    client = MagicMock()

    with (
        patch(_SNAP, new=AsyncMock(return_value="old\nnew Mandrake line")),
        patch.object(drop_detection, "extract_new_drops", return_value=["Mandrake"]),
    ):
        drops, baseline = await drop_detection.poll_new_drops(client, "old", timeout=5.0, poll_interval=0)

    assert drops == ["Mandrake"]
    assert baseline == "old\nnew Mandrake line"


async def test_poll_new_drops_waits_for_a_late_message():
    """The message is absent on the first read and arrives on a later poll."""
    client = MagicMock()

    with (
        patch(_SNAP, new=AsyncMock(side_effect=["old", "old", "old+drop"])),
        patch.object(drop_detection, "extract_new_drops", side_effect=[[], [], ["Mandrake"]]),
    ):
        drops, baseline = await drop_detection.poll_new_drops(client, "old", timeout=5.0, poll_interval=0)

    assert drops == ["Mandrake"]
    assert baseline == "old+drop"


async def test_poll_new_drops_timeout_leaves_baseline_untouched():
    """Nothing arrived, so the original baseline is returned for the next call to reuse."""
    client = MagicMock()

    with (
        patch(_SNAP, new=AsyncMock(return_value="old and then some")),
        patch.object(drop_detection, "extract_new_drops", return_value=[]),
    ):
        drops, baseline = await drop_detection.poll_new_drops(client, "old", timeout=0, poll_interval=0)

    assert drops == []
    assert baseline == "old"
