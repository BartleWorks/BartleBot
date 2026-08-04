"""Tests for the pure path-shortening helper in main_window.

Extracted out of MainWindow.update_recent_menu() specifically so it is
testable without a QApplication — see CLAUDE.md's testing convention for
QWidget subclasses.
"""

from bartlebot.gui.main_window import _shorten_recent_path


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
