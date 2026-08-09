"""Tests for the pure path-resolution helper in bot_canvas.

Extracted out of BotCanvas.refresh() specifically so it is testable without a
QApplication — see CLAUDE.md's testing convention for QWidget subclasses.
"""

from pathlib import Path

from bartlebot.gui.canvas.bot_canvas import _resolve_bots_dir


def test_relative_path_resolves_against_the_given_root():
    root = Path("/project")
    assert _resolve_bots_dir("bots", root) == Path("/project/bots")


def test_default_value_resolves_against_the_given_root():
    """The configured value is always "bots" unless the user overrides it."""
    root = Path("/project")
    assert _resolve_bots_dir("bots", root) == root / "bots"


def test_absolute_override_is_returned_unchanged(tmp_path):
    absolute = tmp_path / "elsewhere" / "my-bots"
    assert _resolve_bots_dir(str(absolute), Path("/project")) == absolute


def test_nested_relative_override_resolves_against_the_root():
    root = Path("/project")
    assert _resolve_bots_dir("data/bots", root) == root / "data" / "bots"
