"""Tests for the pure path-shortening helper in main_window.

Extracted out of MainWindow.update_recent_menu() specifically so it is
testable without a QApplication — see CLAUDE.md's testing convention for
QWidget subclasses.
"""

from bartlebot.gui.main_window import _shorten_recent_path, _splitter_sizes


def test_path_under_root_is_shortened(tmp_path):
    bot_path = tmp_path / "bots" / "castle_darkmoor.json"
    assert _shorten_recent_path(str(bot_path), tmp_path) == str(bot_path.relative_to(tmp_path))


def test_path_outside_root_is_returned_unchanged(tmp_path):
    """A recent file saved elsewhere on disk has no relative form — show it as-is
    rather than raising, which is what relative_to() alone would do.
    """
    outside = tmp_path.parent / "somewhere-else" / "my_bot.json"
    assert _shorten_recent_path(str(outside), tmp_path) == str(outside)


def test_root_itself_shortens_to_a_dot(tmp_path):
    assert _shorten_recent_path(str(tmp_path), tmp_path) == "."


# ---------------------------------------------------------------------------
# Startup splitter sizes
# ---------------------------------------------------------------------------
# _apply_configured_splitter_sizes hands these to QSplitter.setSizes on the first
# showEvent. The widget half is not covered here — the arithmetic is what was wrong:
# the construction-time setSizes() landed on a splitter with no extent, so Qt read it
# as a ratio and the window's startup size was then shared out across every pane,
# leaving the game panel a fraction of the space the launch fit is judged against.


def test_sidebars_keep_their_configured_widths():
    assert _splitter_sizes(1400, [300, 300], flexible=1) == [300, 800, 300]


def test_bot_canvas_keeps_its_configured_height():
    assert _splitter_sizes(1000, [220], flexible=0) == [780, 220]


def test_a_window_too_small_for_the_fixed_panes_leaves_the_rest_at_zero():
    """Zero, not negative: Qt falls back on each pane's own minimum from there, where a
    negative middle is a size it has no sensible reading of.
    """
    assert _splitter_sizes(500, [300, 300], flexible=1) == [300, 0, 300]
