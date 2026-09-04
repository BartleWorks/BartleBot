"""Tests for the Qt message handler helpers.

Only the pure parts are covered — classification, stack signatures, burst
throttling, and dump layout.  Installing the handler needs a QApplication, per
CLAUDE.md's testing convention for Qt code.
"""

from bartlebot.qt_paint_debug import (
    DISPLAY_NOISE_SIGNATURE,
    FONT_NOISE_SIGNATURE,
    LEGACY_BITMAP_FONTS,
    BurstTracker,
    format_dump,
    is_display_noise,
    is_legacy_font_noise,
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
# Monitor-asleep noise (#42, #120)
# ---------------------------------------------------------------------------

# Verbatim from issue #42, in both the rendering that carries the category
# prefix and the bare one a message handler receives.
DISPLAY_NOISE = [
    r'qt.qpa.screen: "Unable to open monitor interface to \\.\DISPLAY1:" "Unknown error 0xe0000225."',
    'qt.qpa.screen: "Unable to get device information for 516:" "The operation completed successfully."',
    r"Unable to open monitor interface to \\.\DISPLAY1: Unknown error 0xe0000225.",
    "Unable to get device information for 516: The operation completed successfully.",
]


def test_every_monitor_asleep_message_is_recognised():
    assert all(is_display_noise(m) for m in DISPLAY_NOISE)


def test_the_category_qt_reports_them_under_is_accepted():
    assert is_display_noise(DISPLAY_NOISE[0], "qt.qpa.screen")
    assert is_display_noise(DISPLAY_NOISE[0], "default")


def test_the_same_text_under_another_category_is_not_suppressed():
    assert not is_display_noise(DISPLAY_NOISE[0], "qt.qpa.window")


def test_other_screen_warnings_are_still_reported():
    assert not is_display_noise("QScreen: Cannot find window ancestor", "qt.qpa.screen")
    assert not is_display_noise("Unable to set geometry", "qt.qpa.screen")


def test_monitor_asleep_noise_is_not_mistaken_for_a_painter_message():
    assert not any(is_painter_message(m) for m in DISPLAY_NOISE)


def test_an_overnight_burst_of_noise_collapses_to_one_note_per_interval():
    tracker = BurstTracker(interval=300.0)
    now = 1000.0
    assert tracker.should_dump(DISPLAY_NOISE_SIGNATURE, now)
    for i in range(1, 600):  # every half second for five minutes
        assert not tracker.should_dump(DISPLAY_NOISE_SIGNATURE, now + i * 0.5)
    assert tracker.should_dump(DISPLAY_NOISE_SIGNATURE, now + 300.0)
    assert tracker.drain_suppressed(DISPLAY_NOISE_SIGNATURE) == 599


# ---------------------------------------------------------------------------
# Legacy stock-font noise (#169)
# ---------------------------------------------------------------------------

# Verbatim from issue #169 — one line per legacy family, all ten of them.
_FONT_NOISE_TEMPLATE = (
    "DirectWrite: CreateFontFaceFromHDC() failed "
    "(Indicates an error in an input file such as a font file.) for "
    'QFontDef(Family="{family}", stylename=Regular, pointsize=9, pixelsize={px}, '
    "styleHint=5, weight=400, stretch=100, hintingPreference=0) "
    'LOGFONT("{family}", lfWidth=0, lfHeight=-{px}) dpi=96'
)

# The pixel sizes are the ones the report carried; they are the bitmap fonts'
# own native sizes, which is why they differ per family.
FONT_NOISE = [
    _FONT_NOISE_TEMPLATE.format(family=family, px=px)
    for family, px in zip(LEGACY_BITMAP_FONTS, (20, 15, 46, 13, 13, 46, 45, 12, 16, 12))
]


def test_every_legacy_font_warning_in_the_report_is_recognised():
    assert len(FONT_NOISE) == 10
    assert all(is_legacy_font_noise(m) for m in FONT_NOISE)


def test_the_same_failure_for_a_real_font_is_still_reported():
    """A TrueType font failing to build a face is a genuine problem."""
    real = _FONT_NOISE_TEMPLATE.format(family="Segoe UI", px=12)
    assert not is_legacy_font_noise(real)


def test_a_family_merely_containing_a_legacy_name_is_not_swallowed():
    """``Modern``, ``Roman`` and ``System`` are words other families also use."""
    for family in ("Modern No. 20", "Times New Roman", "System Sans"):
        assert not is_legacy_font_noise(_FONT_NOISE_TEMPLATE.format(family=family, px=12))


def test_another_directwrite_failure_naming_a_legacy_font_is_still_reported():
    """Only the CreateFontFaceFromHDC failure is known-harmless."""
    assert not is_legacy_font_noise(
        'DirectWrite: CreateFontFromLOGFONT() failed for QFontDef(Family="Fixedsys", pointsize=9)'
    )


def test_font_noise_is_not_mistaken_for_the_other_two_kinds():
    assert not any(is_painter_message(m) for m in FONT_NOISE)
    assert not any(is_display_noise(m) for m in FONT_NOISE)
    assert not any(is_legacy_font_noise(m) for m in DISPLAY_NOISE + BURST)


def test_the_startup_burst_collapses_to_one_note():
    """All ten arrive together at startup, and share one signature."""
    tracker = BurstTracker(interval=300.0)
    dumps = [tracker.should_dump(FONT_NOISE_SIGNATURE, now=i * 0.001) for i in range(len(FONT_NOISE))]
    assert sum(dumps) == 1
    assert tracker.drain_suppressed(FONT_NOISE_SIGNATURE) == 9


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
