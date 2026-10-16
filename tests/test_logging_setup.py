"""Tests for the log-readability machinery.

These three pieces are what turn a session log from complete into readable, and
each of them can get a run wrong in a way that is invisible until someone is
reading a 49,000-line file trying to find where a bot broke: a dropped record,
a miscounted repeat, a mangled logger name.
"""

import logging
import os
from pathlib import Path

import pytest

from bartlebot.logging_setup import (
    GUI_ONLY,
    TURN_TRACE_LOGGER,
    GuiOnlyFilter,
    RepeatCollapsingFilter,
    ShortNameFormatter,
    _GuiBridgeHandler,
    prune_session_logs,
    set_turn_trace,
)


class _CapturingHandler(logging.Handler):
    """Handler that keeps the records it emits, formatted and raw."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


@pytest.fixture
def handler():
    return _CapturingHandler()


def _record(name: str = "bartlebot.node.quicksell", msg: str = "hello", level: int = logging.INFO, **extra):
    record = logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ---------------------------------------------------------------------------
# ShortNameFormatter
# ---------------------------------------------------------------------------


def test_the_bartlebot_prefix_is_dropped():
    formatter = ShortNameFormatter("%(name)s: %(message)s")

    assert formatter.format(_record(name="bartlebot.node.quicksell")) == "node.quicksell: hello"


def test_third_party_names_are_left_alone():
    """Keeping their full names is how a wizwalker line still reads as one."""
    formatter = ShortNameFormatter("%(name)s: %(message)s")

    assert formatter.format(_record(name="wizwalker.memory")) == "wizwalker.memory: hello"


def test_formatting_does_not_mutate_the_record():
    """The same record goes on to every other handler, which must see the real name."""
    formatter = ShortNameFormatter("%(name)s: %(message)s")
    record = _record(name="bartlebot.bot.machine")

    formatter.format(record)

    assert record.name == "bartlebot.bot.machine"


def test_the_name_survives_a_formatting_error():
    """A bad format string must not leave every later handler with a truncated name."""
    formatter = ShortNameFormatter("%(nonexistent)s")
    record = _record(name="bartlebot.bot.machine")

    with pytest.raises(ValueError):
        formatter.format(record)

    assert record.name == "bartlebot.bot.machine"


# ---------------------------------------------------------------------------
# GuiOnlyFilter
# ---------------------------------------------------------------------------


def test_gui_only_records_are_dropped():
    assert GuiOnlyFilter().filter(_record(**GUI_ONLY)) is False


def test_ordinary_records_pass():
    assert GuiOnlyFilter().filter(_record()) is True


# ---------------------------------------------------------------------------
# RepeatCollapsingFilter
# ---------------------------------------------------------------------------


def test_the_first_of_a_run_is_kept(handler):
    collapse = RepeatCollapsingFilter(handler)

    assert collapse.filter(_record(msg="timed out")) is True
    assert collapse.filter(_record(msg="timed out")) is False
    assert collapse.filter(_record(msg="timed out")) is False


def test_the_streak_is_counted_when_it_ends(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    for _ in range(4):
        handler.handle(_record(msg="timed out"))
    handler.handle(_record(msg="something else"))

    assert handler.messages == [
        "timed out",
        "... last message repeated 3 more times",
        "something else",
    ]


def test_a_single_repeat_reads_as_one_time(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(msg="timed out"))
    handler.handle(_record(msg="timed out"))
    handler.handle(_record(msg="something else"))

    assert handler.messages[1] == "... last message repeated 1 more time"


def test_the_summary_keeps_the_name_and_level_of_what_it_summarizes(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(name="bartlebot.node.handle_combat", msg="timed out", level=logging.WARNING))
    handler.handle(_record(name="bartlebot.node.handle_combat", msg="timed out", level=logging.WARNING))
    handler.handle(_record(name="bartlebot.node.quicksell", msg="selling", level=logging.INFO))

    summary = handler.records[1]
    assert summary.name == "bartlebot.node.handle_combat"
    assert summary.levelno == logging.WARNING


def test_alternating_messages_never_collapse(handler):
    """Two failures taking turns is the case where a count would hide something."""
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    for _ in range(3):
        handler.handle(_record(msg="a"))
        handler.handle(_record(msg="b"))

    assert handler.messages == ["a", "b", "a", "b", "a", "b"]


def test_the_same_text_from_two_loggers_is_two_messages(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(name="bartlebot.node.interact", msg="clicking"))
    handler.handle(_record(name="bartlebot.node.click_path", msg="clicking"))

    assert handler.messages == ["clicking", "clicking"]


def test_the_same_text_at_two_levels_is_two_messages(handler):
    """A message promoted to WARNING is news, not a repeat."""
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(msg="inventory full", level=logging.DEBUG))
    handler.handle(_record(msg="inventory full", level=logging.WARNING))

    assert [r.levelno for r in handler.records] == [logging.DEBUG, logging.WARNING]


def test_interpolated_args_are_compared_after_interpolation(handler):
    """A "pass %d" line with different numbers is different text, and must not collapse."""
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    for n in (1, 2, 3):
        record = _record(msg="=== pass %d ===")
        record.args = (n,)
        handler.handle(record)

    assert handler.messages == ["=== pass 1 ===", "=== pass 2 ===", "=== pass 3 ==="]


def test_a_failing_summary_does_not_take_the_next_record_down(handler):
    """The next real record matters more than the bookkeeping line in front of it."""

    class _Exploding(_CapturingHandler):
        def emit(self, record):
            if record.getMessage().startswith("..."):
                raise RuntimeError("boom")
            super().emit(record)

    exploding = _Exploding()
    exploding.addFilter(RepeatCollapsingFilter(exploding))

    exploding.handle(_record(msg="timed out"))
    exploding.handle(_record(msg="timed out"))
    exploding.handle(_record(msg="something else"))

    assert exploding.messages == ["timed out", "something else"]


# ---------------------------------------------------------------------------
# prune_session_logs
# ---------------------------------------------------------------------------


def _session_logs(log_dir, count: int) -> list:
    """*count* session logs, oldest first, with mtimes a minute apart."""
    logs = []
    for i in range(count):
        path = log_dir / f"bartlebot_2026-09-{i + 1:02d}_12-00-00.log"
        path.write_text("x")
        os.utime(path, (1_000_000 + i * 60, 1_000_000 + i * 60))
        logs.append(path)
    return logs


def test_prune_keeps_the_ten_newest_session_logs(tmp_path):
    logs = _session_logs(tmp_path, 13)

    prune_session_logs(tmp_path)

    assert sorted(tmp_path.iterdir()) == logs[3:]


def test_prune_goes_by_modification_time_not_name(tmp_path):
    """A log whose name sorts oldest is kept when it was the last one written to."""
    logs = _session_logs(tmp_path, 11)
    os.utime(logs[0], (9_999_999, 9_999_999))

    prune_session_logs(tmp_path)

    assert logs[0].exists()
    assert not logs[1].exists()


def test_prune_leaves_other_files_alone(tmp_path):
    _session_logs(tmp_path, 12)
    others = [tmp_path / "notes.txt", tmp_path / "bartlebot_crash.dmp", tmp_path / "other_2026-01-01.log"]
    for path in others:
        path.write_text("keep me")
        os.utime(path, (1, 1))  # older than every session log

    prune_session_logs(tmp_path)

    assert all(path.exists() for path in others)


def test_prune_skips_a_log_it_cannot_delete(tmp_path, monkeypatch):
    """A log held open by another running instance must not stop startup, or the rest of the prune."""
    logs = _session_logs(tmp_path, 12)
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == logs[0]:
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    prune_session_logs(tmp_path)

    assert logs[0].exists()
    assert not logs[1].exists()


# ---------------------------------------------------------------------------
# _GuiBridgeHandler
# ---------------------------------------------------------------------------


def test_the_bridge_hands_each_callback_the_formatted_line_and_level():
    bridge = _GuiBridgeHandler()
    bridge.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    seen = []
    bridge.add_callback(lambda msg, level: seen.append((msg, level)))

    bridge.handle(_record(msg="hello", level=logging.WARNING))

    assert seen == [("[WARNING] hello", logging.WARNING)]


def test_a_failing_gui_callback_does_not_silence_the_others():
    bridge = _GuiBridgeHandler()
    seen = []

    def broken(_msg, _level):
        raise RuntimeError("widget already deleted")

    bridge.add_callback(broken)
    bridge.add_callback(lambda msg, _level: seen.append(msg))

    bridge.handle(_record(msg="first"))
    bridge.handle(_record(msg="second"))

    assert seen == ["first", "second"]


def test_a_removed_callback_stops_receiving():
    bridge = _GuiBridgeHandler()
    seen = []

    def callback(msg, _level):
        seen.append(msg)

    bridge.add_callback(callback)
    bridge.handle(_record(msg="before"))
    bridge.remove_callback(callback)
    bridge.handle(_record(msg="after"))

    assert seen == ["before"]


# ---------------------------------------------------------------------------
# The turn trace, and configure_logging
# ---------------------------------------------------------------------------


@pytest.fixture
def trace_level():
    """Put the turn trace's logger back as it was: its level is process-wide."""
    logger = logging.getLogger(TURN_TRACE_LOGGER)
    level = logger.level
    yield logger
    logger.setLevel(level)


def test_the_turn_trace_is_on_when_the_setting_is(trace_level):
    assert set_turn_trace(True) is True
    assert trace_level.level == logging.DEBUG


def test_off_the_turn_trace_inherits_wizwalkers_level(trace_level):
    assert set_turn_trace(False) is False
    assert trace_level.level == logging.NOTSET


def test_developer_mode_records_the_turn_trace_whatever_the_setting(trace_level, monkeypatch):
    monkeypatch.setenv("BARTLEBOT_DEV", "1")

    assert set_turn_trace(False) is True
    assert trace_level.level == logging.DEBUG


@pytest.mark.parametrize("record", [True, False])
def test_the_session_file_has_the_turn_trace_only_when_asked(tmp_path, monkeypatch, trace_level, record):
    """And the rest of wizwalker, which reads memory at DEBUG, stays at INFO either way."""
    from bartlebot import logging_setup
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().record_combat_decisions = record
    root = logging.getLogger()
    wizwalker = logging.getLogger("wizwalker")
    saved = (list(root.handlers), root.level, wizwalker.level)
    monkeypatch.setattr(logging_setup, "session_log_dir", lambda: tmp_path)
    try:
        logging_setup.configure_logging()
        logging.getLogger(TURN_TRACE_LOGGER).debug("round 2: the trace")
        logging.getLogger("wizwalker.memory").debug("a memory read")
    finally:
        for added in [h for h in root.handlers if h not in saved[0]]:
            root.removeHandler(added)
            added.close()
        root.setLevel(saved[1])
        wizwalker.setLevel(saved[2])

    (session,) = tmp_path.glob("bartlebot_*.log")
    text = session.read_text(encoding="utf-8")
    assert ("round 2: the trace" in text) is record
    assert ("Combat decision trace on" in text) is record
    assert "a memory read" not in text


def test_the_session_header_names_the_build_and_pillow_stays_quiet(tmp_path, monkeypatch):
    """A pasted log says whether a build or a checkout wrote it, and art extraction adds no Pillow chatter."""
    from bartlebot import logging_setup

    root = logging.getLogger()
    pil = logging.getLogger("PIL")
    saved = (list(root.handlers), root.level, pil.level)
    monkeypatch.setattr(logging_setup, "session_log_dir", lambda: tmp_path)
    try:
        logging_setup.configure_logging()
        logging.getLogger("PIL.Image").debug("Importing DdsImagePlugin")
    finally:
        for added in [h for h in root.handlers if h not in saved[0]]:
            root.removeHandler(added)
            added.close()
        root.setLevel(saved[1])
        pil.setLevel(saved[2])

    (session,) = tmp_path.glob("bartlebot_*.log")
    text = session.read_text(encoding="utf-8")
    assert "Build: checkout" in text
    assert "DdsImagePlugin" not in text
