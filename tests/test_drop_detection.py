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


# ---------------------------------------------------------------------------
# _find_new_stuff — the chat-log diff (#143)
# ---------------------------------------------------------------------------


def _drop_line(item: str) -> str:
    """A chat line shaped the way the game announces a reagent drop."""
    return f"Art_Chat_System.dds> <>{item}<;Reagent>"


def test_find_new_stuff_appends_are_the_fast_path():
    old = "\n".join(["a", "b", "c"])
    new = old + "\n" + "\n".join(["d", "e"])

    assert drop_detection._find_new_stuff(old, new) == "\nd\ne"


def test_find_new_stuff_returns_nothing_when_the_log_is_unchanged():
    old = "\n".join(["a", "b", "c"])

    assert drop_detection._find_new_stuff(old, old) == ""


def test_find_new_stuff_survives_the_log_trimming_from_the_front():
    """The scrollback filled: the oldest lines are gone, so `old` is no longer a substring.

    This is the case that made the old implementation quadratic, and the case it got
    wrong often enough to report a whole chat log as fresh drops.
    """
    old = "\n".join(f"line {i}" for i in range(100))
    new = "\n".join([*(f"line {i}" for i in range(40, 100)), "fresh 1", "fresh 2"])

    assert drop_detection._find_new_stuff(old, new) == "fresh 1\nfresh 2"


def test_find_new_stuff_treats_a_wholly_unrecognisable_log_as_new():
    """No overlap at all — a fresh window, or a trim larger than the whole baseline."""
    assert drop_detection._find_new_stuff("a\nb\nc", "x\ny") == "x\ny"


def test_find_new_stuff_with_no_baseline_returns_everything():
    assert drop_detection._find_new_stuff("", "a\nb") == "a\nb"


def test_find_new_stuff_prefers_the_longest_overlap_when_lines_repeat():
    """Chat logs repeat themselves — the same item drops over and over.

    Taking the *longest* suffix/prefix overlap is what keeps a repeated line from
    being re-reported as new.
    """
    old = "\n".join(["dup"] * 50)
    new = "\n".join(["dup"] * 48 + ["fresh"])

    assert drop_detection._find_new_stuff(old, new) == "fresh"


def test_find_new_stuff_is_linear_on_a_large_trimmed_log():
    """Guards the #143 regression: the old sliding-window version took ~127 ms here.

    A generous ceiling — the point is to catch a return to quadratic behaviour, not to
    pin down a wall-clock figure on a loaded CI runner.
    """
    import time

    old = "\n".join(f"{_drop_line('Sunstone')} {i}" for i in range(4000))
    new = "\n".join([*(f"{_drop_line('Sunstone')} {i}" for i in range(400, 4000)), _drop_line("Mandrake")])

    start = time.perf_counter()
    result = drop_detection._find_new_stuff(old, new)
    elapsed = time.perf_counter() - start

    assert result == _drop_line("Mandrake")
    assert elapsed < 1.0


def test_extract_new_drops_reports_only_the_drops_added_after_a_trim():
    old = "\n".join(_drop_line(f"Item {i}") for i in range(100))
    new = "\n".join([*(_drop_line(f"Item {i}") for i in range(30, 100)), _drop_line("Mandrake")])

    assert drop_detection.extract_new_drops(old, new) == ["Mandrake"]


# ---------------------------------------------------------------------------
# snapshot_chat capping (#143)
# ---------------------------------------------------------------------------


async def test_snapshot_chat_caps_the_text_it_keeps():
    """An hours-long session leaves thousands of lines; a baseline keeps only the tail."""
    client = _client_with_chat()
    log = "\n".join(f"line {i}" for i in range(drop_detection._SNAPSHOT_MAX_LINES * 3))
    window = client.root_window
    for _ in drop_detection._CHAT_PATH:
        window = (await window.children())[0]
    window.maybe_text = AsyncMock(return_value=log)

    snapshot = await drop_detection.snapshot_chat(client)

    lines = snapshot.split("\n")
    assert len(lines) == drop_detection._SNAPSHOT_MAX_LINES
    assert lines[-1] == f"line {drop_detection._SNAPSHOT_MAX_LINES * 3 - 1}"


async def test_snapshot_chat_leaves_a_short_log_alone():
    client = _client_with_chat()
    window = client.root_window
    for _ in drop_detection._CHAT_PATH:
        window = (await window.children())[0]
    window.maybe_text = AsyncMock(return_value="a\nb\nc")

    assert await drop_detection.snapshot_chat(client) == "a\nb\nc"


async def test_a_capped_baseline_still_diffs_against_the_next_snapshot():
    """The cap must not make consecutive snapshots look unrelated.

    Capping keeps the *tail*, and new announcements arrive at the tail, so the overlap
    the diff needs is exactly the part that is kept.
    """
    cap = drop_detection._SNAPSHOT_MAX_LINES
    full_before = "\n".join(f"line {i}" for i in range(cap * 3))
    full_after = "\n".join([*(f"line {i}" for i in range(cap * 3)), _drop_line("Mandrake")])

    before = drop_detection._tail(full_before)
    after = drop_detection._tail(full_after)

    assert drop_detection.extract_new_drops(before, after) == ["Mandrake"]
