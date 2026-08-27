"""Tests for the Qt message handler helpers.

Only the pure parts are covered — classification, stack signatures, burst
throttling, and dump layout.  Installing the handler needs a QApplication, per
CLAUDE.md's testing convention for Qt code.
"""

from bartlebot.qt_paint_debug import (
    BurstTracker,
    format_dump,
    is_painter_message,
    relevant_frames,
    stack_signature,
)

# The four messages that make up one cycle of the issue #72 burst.
BURST = [
    "QPainter::begin: Paint device returned engine == 0, type: 3",
    "QPainter::setCompositionMode: Painter not active",
    "QPainter::fillRect: Painter not active",
    "QPainter::end: Painter not active, aborted",
]

CALLER = '  File "bartlebot/gui/canvas/node_item.py", line 551, in paint\n    painter.drawText(...)\n'
OWN_FRAME = '  File "bartlebot/qt_paint_debug.py", line 130, in _handler\n'


# ---------------------------------------------------------------------------
# Message classification
# ---------------------------------------------------------------------------


def test_every_message_in_the_reported_burst_is_recognised():
    assert all(is_painter_message(m) for m in BURST)


def test_unrelated_qt_warnings_are_ignored():
    assert not is_painter_message("QWindowsWindow::setGeometry: Unable to set geometry")
    assert not is_painter_message("QFontDatabase: Cannot find font directory")


# ---------------------------------------------------------------------------
# Stack signatures
# ---------------------------------------------------------------------------


def test_frames_from_the_handler_itself_are_dropped():
    assert relevant_frames([CALLER, OWN_FRAME]) == [CALLER]


def test_the_same_caller_produces_the_same_signature():
    """The handler's own line number varies; the caller is what identifies a burst."""
    a = [CALLER, OWN_FRAME]
    b = [CALLER, '  File "bartlebot/qt_paint_debug.py", line 999, in _handler\n']
    assert stack_signature(a) == stack_signature(b)


def test_different_callers_produce_different_signatures():
    other = '  File "bartlebot/gui/canvas/region_item.py", line 130, in paint\n'
    assert stack_signature([CALLER]) != stack_signature([other])


def test_a_purely_qt_internal_stack_still_gets_a_signature():
    """No Python frames at all is an informative case, not an error case."""
    assert relevant_frames([OWN_FRAME]) == []
    assert stack_signature([OWN_FRAME])


# ---------------------------------------------------------------------------
# Burst throttling
# ---------------------------------------------------------------------------


def test_the_first_message_on_a_stack_always_dumps():
    assert BurstTracker(interval=10.0).should_dump("abc", now=0.0)


def test_a_burst_collapses_to_a_single_dump():
    tracker = BurstTracker(interval=10.0)
    dumps = [tracker.should_dump("abc", now=t / 1000) for t in range(400)]
    assert sum(dumps) == 1
    assert dumps[0] is True


def test_suppressed_messages_are_counted_and_drained_once():
    tracker = BurstTracker(interval=10.0)
    tracker.should_dump("abc", now=0.0)
    for _ in range(5):
        tracker.should_dump("abc", now=1.0)
    assert tracker.drain_suppressed("abc") == 5
    assert tracker.drain_suppressed("abc") == 0


def test_a_new_burst_after_the_interval_dumps_again():
    tracker = BurstTracker(interval=10.0)
    assert tracker.should_dump("abc", now=0.0)
    assert not tracker.should_dump("abc", now=9.9)
    assert tracker.should_dump("abc", now=10.0)


def test_distinct_stacks_are_throttled_independently():
    tracker = BurstTracker(interval=10.0)
    assert tracker.should_dump("abc", now=0.0)
    assert tracker.should_dump("def", now=0.1)
    assert not tracker.should_dump("abc", now=0.2)


# ---------------------------------------------------------------------------
# Dump layout
# ---------------------------------------------------------------------------


def test_the_dump_names_the_calling_frame_and_the_message():
    out = format_dump(BURST[0], [CALLER, OWN_FRAME], "abc123", suppressed=0)
    assert "node_item.py" in out
    assert "type: 3" in out
    assert "abc123" in out
    assert "qt_paint_debug.py" not in out


def test_a_suppressed_count_is_reported_only_when_nonzero():
    assert "suppressed" not in format_dump(BURST[0], [CALLER], "abc", suppressed=0)
    assert "417 further" in format_dump(BURST[0], [CALLER], "abc", suppressed=417)


def test_a_stack_with_no_python_frames_says_so_rather_than_rendering_blank():
    out = format_dump(BURST[0], [OWN_FRAME], "abc", suppressed=0)
    assert "Qt-internal paint" in out
