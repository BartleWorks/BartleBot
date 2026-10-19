"""Tests for the shared search field, and the convention that every search uses it."""

import re

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLineEdit, QPushButton

import bartlebot.gui.theming.theme as theme_mod
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.inputs.search_field import SearchLineEdit
from bartlebot.paths import project_root

GUI_DIR = project_root() / "bartlebot" / "gui"


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


# ---------------------------------------------------------------------------
# The scope pill (#269)
# ---------------------------------------------------------------------------


def _scoped(text: str = "MooShu", **kw) -> SearchLineEdit:
    field = SearchLineEdit("x")
    field.resize(400, 28)
    field.set_scope(text, **kw)
    return field


def _pill(field: SearchLineEdit) -> QPushButton:
    return field.findChild(QPushButton)


def test_a_field_has_no_scope_until_given_one(qapp):
    field = SearchLineEdit("x")

    assert field.scope_text is None
    assert not field.scope_removable
    assert field.findChild(QPushButton) is None
    assert field.textMargins().left() == 0


def test_a_scope_shows_its_pill_and_starts_the_text_after_it(qapp):
    field = _scoped("MooShu")
    pill = _pill(field)

    assert field.scope_text == "MooShu"
    assert pill.text() == "MooShu  ✕"
    assert not pill.isHidden()
    # Past the magnifier, and the text past the pill: a margin as wide as the pill at least.
    assert pill.x() > 0
    assert field.textMargins().left() >= pill.width()


def test_dropping_the_scope_hides_the_pill_and_gives_the_text_its_room_back(qapp):
    field = _scoped("MooShu")

    field.set_scope(None)

    assert field.scope_text is None
    assert _pill(field).isHidden()
    assert field.textMargins().left() == 0


def test_a_new_scope_reuses_the_pill_and_resizes_it(qapp):
    field = _scoped("MooShu")
    pill, width = _pill(field), _pill(field).width()

    field.set_scope("Wizard City and Beyond")

    assert _pill(field) is pill
    assert pill.width() > width
    assert field.textMargins().left() >= pill.width()


def test_clicking_a_removable_pill_asks_for_the_scope_to_go(qapp):
    """It asks and does no more: the owner knows what the wider search is."""
    field = _scoped("MooShu")
    removed = []
    field.scope_removed.connect(lambda: removed.append(True))

    _pill(field).click()

    assert removed == [True]
    assert field.scope_text == "MooShu"


def test_a_fixed_scope_has_no_cross_and_ignores_a_click(qapp):
    field = _scoped("Subgraphs", removable=False)
    removed = []
    field.scope_removed.connect(lambda: removed.append(True))

    _pill(field).click()

    assert _pill(field).text() == "Subgraphs"
    assert not field.scope_removable
    assert removed == []


def test_backspace_against_the_pill_takes_the_pill_not_the_text(qapp):
    """Discord's token behavior: the caret right after the pill, Backspace removes it."""
    field = _scoped("MooShu")
    field.setText("jade")
    field.setCursorPosition(0)
    removed = []
    field.scope_removed.connect(lambda: removed.append(True))

    QTest.keyClick(field, Qt.Key.Key_Backspace)

    assert removed == [True]
    assert field.text() == "jade"


def test_backspace_in_an_empty_field_takes_the_pill(qapp):
    field = _scoped("MooShu")
    removed = []
    field.scope_removed.connect(lambda: removed.append(True))

    QTest.keyClick(field, Qt.Key.Key_Backspace)

    assert removed == [True]


def test_backspace_after_the_text_deletes_text_as_ever(qapp):
    field = _scoped("MooShu")
    field.setText("jade")
    removed = []
    field.scope_removed.connect(lambda: removed.append(True))

    QTest.keyClick(field, Qt.Key.Key_Backspace)

    assert field.text() == "jad"
    assert removed == []


def test_backspace_over_a_selection_deletes_the_selection(qapp):
    field = _scoped("MooShu")
    field.setText("jade")
    field.selectAll()
    removed = []
    field.scope_removed.connect(lambda: removed.append(True))

    QTest.keyClick(field, Qt.Key.Key_Backspace)

    assert field.text() == ""
    assert removed == []


def test_backspace_never_takes_a_fixed_scope(qapp):
    field = _scoped("Subgraphs", removable=False)
    removed = []
    field.scope_removed.connect(lambda: removed.append(True))

    QTest.keyClick(field, Qt.Key.Key_Backspace)

    assert removed == []


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def test_the_pill_recolors_on_a_theme_switch(qapp, theme):
    theme("light")
    field = _scoped("MooShu")
    before = _pill(field).styleSheet()

    theme("dark")
    qapp.sendEvent(_pill(field), QEvent(QEvent.Type.StyleChange))

    assert _pill(field).styleSheet() != before
    assert THEME.text_muted in _pill(field).styleSheet()
