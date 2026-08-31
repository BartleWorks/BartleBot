"""Tests for the shared search field, and the convention that every search uses it."""

import re
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QLineEdit

from bartlebot.gui.widgets.search_field import SearchLineEdit

GUI_DIR = Path(__file__).parent.parent / "bartlebot" / "gui"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_the_placeholder_is_set(qapp):
    assert SearchLineEdit("Search bots…").placeholderText() == "Search bots…"


def test_a_leading_icon_is_added(qapp):
    field = SearchLineEdit("x")

    assert len(field.actions()) == 1
    assert not field.actions()[0].icon().isNull()


def test_the_icon_is_re_tinted_on_a_theme_change(qapp):
    """svg_icon bakes the tint in at render time, so an icon added once at
    construction keeps a dark glyph on a dark field after a switch to dark."""
    field = SearchLineEdit("x")
    before = field.actions()[0].icon().cacheKey()

    qapp.sendEvent(field, QEvent(QEvent.Type.StyleChange))

    assert field.actions()[0].icon().cacheKey() != before


def test_other_events_do_not_rebuild_the_icon(qapp):
    field = SearchLineEdit("x")
    before = field.actions()[0].icon().cacheKey()

    qapp.sendEvent(field, QEvent(QEvent.Type.EnabledChange))

    assert field.actions()[0].icon().cacheKey() == before


def test_it_is_still_a_line_edit(qapp):
    """Callers keep wiring textChanged, clear buttons and widths as before."""
    assert isinstance(SearchLineEdit("x"), QLineEdit)


# ---------------------------------------------------------------------------
# The convention
# ---------------------------------------------------------------------------

_PLAIN_SEARCH = re.compile(
    r"QLineEdit\(\)\s*\n\s*\w[\w.]*\.setPlaceholderText\(\s*[\"'][^\"']*"
    r"(?:[Ss]earch|[Ff]ilter|[Ff]ind)",
)


def test_no_search_field_is_a_plain_line_edit():
    """A magnifier is how a free-text field says it narrows what is below, and it
    should not depend on whoever wrote the dialog remembering to add one."""
    offenders = []
    for path in sorted(GUI_DIR.rglob("*.py")):
        if _PLAIN_SEARCH.search(path.read_text(encoding="utf-8")):
            offenders.append(path.relative_to(GUI_DIR).as_posix())

    assert not offenders, f"search fields not using SearchLineEdit: {offenders}"
