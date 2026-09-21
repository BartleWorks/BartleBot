"""What a layout trace means.

The analysis is pure functions over records precisely so that this file can exist: the
capture needs a ``QApplication`` and a real interaction, but deciding whether a trace
describes a bug does not.

The numbers in these tests are the ones the tool actually printed for #83 against the
unfixed code, so each function is exercised against the shape of a real defect rather
than an invented one.
"""

from __future__ import annotations

from bartlebot.qt_layout_debug import (
    Record,
    SizeSnapshot,
    format_records,
    format_snapshots,
    incomplete_paints,
    summarize,
    timeline,
    transient_values,
    unsettled,
)


def _record(
    seq: int, *, phase="p", widget="BotHeader", name="", event="resize", geometry=(0, 0, 1200, 53), damage=None
):
    return Record(seq=seq, phase=phase, widget=widget, name=name, event=event, geometry=geometry, damage=damage)


def _snapshot(phase: str, height: int, hint: int, widget: str = "BotHeader") -> SizeSnapshot:
    return SizeSnapshot(
        phase=phase,
        widget=widget,
        height=height,
        hint_height=hint,
        minimum_hint_height=hint,
        width=1200,
        hint_width=1200,
    )


# ---------------------------------------------------------------------------
# Settled or not — the stale-hint diagnosis
# ---------------------------------------------------------------------------


def test_a_widget_whose_height_matches_its_hint_is_settled():
    assert _snapshot("run", 53, 53).settled


def test_a_widget_taller_than_its_own_hint_is_not_settled():
    """The exact state #83 left Bar B in: 56px tall, asking for 38.

    Nothing further is coming to correct it -- the parent already laid out, from a hint
    that has since changed -- so it stays wrong on screen until an unrelated resize.
    """
    assert not _snapshot("run settled", 56, 38).settled


def test_unsettled_reports_only_the_disagreeing_steps():
    snapshots = [
        _snapshot("run (initial)", 38, 38),
        _snapshot("edit settled", 53, 53),
        _snapshot("run settled", 56, 38),
    ]

    assert [s.phase for s in unsettled(snapshots)] == ["run settled"]


def test_nothing_is_unsettled_in_a_clean_trace():
    snapshots = [_snapshot("run", 53, 53), _snapshot("edit", 53, 53)]

    assert unsettled(snapshots) == []


# ---------------------------------------------------------------------------
# Transient heights — the frames the user saw
# ---------------------------------------------------------------------------


def test_the_heights_no_mode_asked_for_are_reported_in_order():
    """Bar B's real timeline, leaving Edit mode: 68 and 80 on the way to 56.

    Every one of these was painted, which is what the flicker was.
    """
    visited = [44, 52, 53, 68, 80, 56]

    assert transient_values(visited, settled={53, 56}) == [44, 52, 68, 80]


def test_a_height_a_mode_genuinely_wants_is_not_transient():
    """A bar legitimately taller in Edit mode must not be reported as an overshoot.

    A tagged bot's byline really does take a second row in Edit mode -- 53 to 101 -- and
    a tool that called that a bug would be useless for the case that matters.
    """
    assert transient_values([53, 101, 53], settled={53, 101}) == []


def test_repeats_are_kept_because_each_one_is_a_separate_frame():
    assert transient_values([68, 68], settled={56}) == [68, 68]


# ---------------------------------------------------------------------------
# Reading the stream
# ---------------------------------------------------------------------------


def test_the_timeline_follows_one_widget_and_one_event():
    records = [
        _record(1, phase="to edit", geometry=(0, 0, 1200, 44)),
        _record(2, phase="to edit", widget="FavoritesBar", geometry=(0, 0, 1200, 34)),
        _record(3, phase="to edit", event="paint", geometry=(0, 0, 1200, 99)),
        _record(4, phase="to run", geometry=(0, 0, 1200, 56)),
    ]

    assert timeline(records, "BotHeader") == [("to edit", 44), ("to run", 56)]


def test_the_timeline_is_empty_for_a_widget_that_was_never_resized():
    """Which is the healthy outcome, not an error: a bar whose height does not depend on
    the mode is never laid out again when the mode changes."""
    assert timeline([_record(1, widget="FavoritesBar")], "BotHeader") == []


def test_the_summary_counts_per_widget_and_event_busiest_first():
    records = [
        _record(1, event="paint"),
        _record(2, event="paint"),
        _record(3, event="paint"),
        _record(4, event="resize"),
        _record(5, widget="FavoritesBar", event="resize"),
    ]

    assert summarize(records)[0] == ("BotHeader", "", "paint", 3)
    assert ("BotHeader", "", "resize", 1) in summarize(records)


# ---------------------------------------------------------------------------
# Paint damage — the #73 side
# ---------------------------------------------------------------------------


def test_a_paint_covering_the_whole_widget_is_not_reported():
    records = [_record(1, event="paint", geometry=(0, 0, 1200, 53), damage=(0, 0, 1200, 53))]

    assert incomplete_paints(records) == []


def test_a_paint_narrower_than_its_widget_is_reported():
    """Anything the damage rect leaves out keeps what was drawn there before."""
    records = [_record(1, event="paint", geometry=(0, 0, 1200, 53), damage=(8, 3, 1184, 47))]

    assert len(incomplete_paints(records)) == 1


def test_records_that_are_not_paints_are_ignored():
    """A resize has no damage rect, and comparing one against a geometry is meaningless."""
    records = [_record(1, event="resize", geometry=(0, 0, 1200, 53))]

    assert incomplete_paints(records) == []


# ---------------------------------------------------------------------------
# Formatting — it gets pasted into issues, so it has to hold up
# ---------------------------------------------------------------------------


def test_the_record_table_names_the_phase_and_the_geometry():
    out = format_records([_record(1, phase="to run", geometry=(0, 0, 1200, 56))])

    assert "to run" in out
    assert "1200, 56" in out


def test_a_long_trace_is_truncated_with_a_count_of_the_rest():
    out = format_records([_record(i) for i in range(10)], limit=3)

    assert "7 more" in out


def test_the_snapshot_table_flags_the_unsettled_row():
    out = format_snapshots([_snapshot("run settled", 56, 38), _snapshot("edit settled", 53, 53)])

    assert "stale hint" in out
    assert out.count("NO") == 1


# ---------------------------------------------------------------------------
# Wrapping widgets — the false positive the settled test has to avoid
# ---------------------------------------------------------------------------


def _wrapping(phase: str, height: int, hint: int, wanted: int) -> SizeSnapshot:
    return SizeSnapshot(
        phase=phase,
        widget="BotHeader",
        height=height,
        hint_height=hint,
        minimum_hint_height=53,
        width=1200,
        hint_width=1200,
        wanted_height=wanted,
    )


def test_a_wrapped_widget_shorter_than_its_hint_is_still_settled():
    """Bar B with a tagged bot: 53px tall, hint 74, and correct.

    A ``FlowLayout``'s hint is deliberately one row of everything — what it would like,
    not what it needs — so a wrapped widget is legitimately shorter than its hint.
    Measuring against the hint called this a bug on a clean trace.
    """
    assert _wrapping("run settled", height=53, hint=74, wanted=53).settled


def test_a_wrapped_widget_that_disagrees_with_its_wrapped_height_is_not_settled():
    assert not _wrapping("run settled", height=101, hint=74, wanted=53).settled


def test_a_widget_that_does_not_wrap_is_measured_against_its_hint():
    """``wanted_height`` is None for a layout with no height-for-width, and the hint is
    then the only answer there is."""
    snapshot = _snapshot("run settled", 56, 38)

    assert snapshot.wanted == 38
    assert not snapshot.settled
