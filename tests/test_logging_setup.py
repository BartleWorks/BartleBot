"""Tests for the log-readability machinery.

These three pieces are what turn a session log from complete into readable, and
each of them can get a run wrong in a way that is invisible until someone is
reading a 49,000-line file trying to find where a bot broke: a dropped record,
a miscounted repeat, a mangled logger name.
"""

import logging

import pytest

from bartlebot.logging_setup import (
    GUI_ONLY,
    GuiOnlyFilter,
    RepeatCollapsingFilter,
    ShortNameFormatter,
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
