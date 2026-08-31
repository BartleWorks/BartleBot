"""Tests for the reusable searchable multi-select picker.

The matching and summary logic is pure and tested directly; the popup itself needs a
QApplication, and is tested because the parts that break (row rebuild, live
selection, the self-styling recursion trap) are not extractable.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.picker_popup import (
    PickerButton,
    PickerPopup,
    PickerRow,
    _RowWidget,
    filter_sections,
    selection_caption,
    should_open,
    summarize,
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _drain(app) -> None:
    """Run what a live event loop would, so deleteLater() actually deletes.

    Without this the rebuilt rows are merely hidden and still turn up in
    findChildren, which reads as a leak that isn't one.
    """
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


SECTIONS = [
    ("Fruit", [PickerRow("apple", "Apple", "A red pome"), PickerRow("pear", "Pear", "Green and soft")]),
    ("Veg", [PickerRow("kale", "Kale", "Bitter green leaves")]),
]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_an_empty_query_returns_every_section():
    assert filter_sections(SECTIONS, "") == SECTIONS


def test_query_matches_the_label():
    assert [t for t, _ in filter_sections(SECTIONS, "kale")] == ["Veg"]


def test_query_matches_the_description():
    """The description is what someone who doesn't know the vocabulary can search."""
    result = filter_sections(SECTIONS, "bitter")

    assert [r.key for _, rows in result for r in rows] == ["kale"]


def test_a_section_with_no_hits_is_dropped_entirely():
    assert [t for t, _ in filter_sections(SECTIONS, "pear")] == ["Fruit"]


def test_no_hits_returns_nothing():
    assert filter_sections(SECTIONS, "zzzz") == []


def test_query_is_case_and_whitespace_insensitive():
    assert filter_sections(SECTIONS, "  KALE ") == filter_sections(SECTIONS, "kale")


def test_summarize_falls_back_to_the_placeholder():
    assert summarize([], "—") == "—"


def test_summarize_lists_a_short_selection():
    assert summarize(["Gear", "Boss"], "—") == "Gear, Boss"


def test_summarize_collapses_a_long_selection_to_a_count():
    """A button that grows with its selection reflows the form around it."""
    assert summarize(["Gear", "Boss", "Jewels"], "—") == "3 selected"


# ---------------------------------------------------------------------------
# Popup
# ---------------------------------------------------------------------------


def test_popup_renders_a_row_per_entry(qapp):
    popup = PickerPopup("T", SECTIONS)

    assert len(popup.findChildren(_RowWidget)) == 3


def test_popup_reports_selection_in_section_order_not_click_order(qapp):
    popup = PickerPopup("T", SECTIONS)
    seen = []
    popup.selection_changed.connect(seen.append)

    popup._on_row_toggled("kale", True)
    popup._on_row_toggled("apple", True)

    assert seen[-1] == ["apple", "kale"]


def test_popup_preselects_without_emitting(qapp):
    popup = PickerPopup("T", SECTIONS, ["pear"])
    seen = []
    popup.selection_changed.connect(seen.append)

    assert popup.selection() == ["pear"]
    assert seen == []


def test_searching_rebuilds_the_rows(qapp):
    popup = PickerPopup("T", SECTIONS)

    popup._search.setText("bitter")
    _drain(qapp)

    assert [w._row.key for w in popup.findChildren(_RowWidget)] == ["kale"]


def test_clearing_the_search_restores_every_row(qapp):
    popup = PickerPopup("T", SECTIONS)
    popup._search.setText("bitter")
    _drain(qapp)

    popup._search.clear()
    _drain(qapp)

    assert len(popup.findChildren(_RowWidget)) == 3


def test_a_selection_survives_a_search_that_hides_its_row(qapp):
    """Filtering is a view over the rows, not an edit of the selection."""
    popup = PickerPopup("T", SECTIONS, ["apple"])

    popup._search.setText("kale")
    _drain(qapp)

    assert popup.selection() == ["apple"]


def test_clicking_anywhere_on_a_row_toggles_it(qapp):
    """A 13px checkbox is a poor hit target; the whole row is the button."""
    popup = PickerPopup("T", SECTIONS)
    row = popup.findChildren(_RowWidget)[0]

    QTest.mouseClick(row, Qt.MouseButton.LeftButton)

    assert popup.selection() == ["apple"]


def test_a_theme_change_does_not_recurse(qapp):
    """The popup styles itself, so setStyleSheet feeds StyleChange back to it."""
    popup = PickerPopup("T", SECTIONS)
    popup.setStyleSheet("")

    qapp.sendEvent(popup, QEvent(QEvent.Type.StyleChange))

    assert "PickerPopup" in popup.styleSheet()


# ---------------------------------------------------------------------------
# Button
# ---------------------------------------------------------------------------


def test_button_shows_the_placeholder_when_nothing_is_selected(qapp):
    btn = PickerButton("T", placeholder="Tags")
    btn.set_sections(SECTIONS)

    assert btn.text() == "Tags"


def test_button_shows_labels_not_keys(qapp):
    btn = PickerButton("T")
    btn.set_sections(SECTIONS)

    btn.set_selection(["apple"])

    assert btn.text() == "Apple"


def test_set_selection_does_not_emit(qapp):
    """Loading a bot must not look like the user edited it."""
    btn = PickerButton("T")
    btn.set_sections(SECTIONS)
    seen = []
    btn.selection_changed.connect(seen.append)

    btn.set_selection(["apple"])

    assert seen == []
    assert btn.selection() == ["apple"]


def test_toggling_in_the_popup_reaches_the_button(qapp):
    btn = PickerButton("T")
    btn.set_sections(SECTIONS)
    seen = []
    btn.selection_changed.connect(seen.append)

    btn._open()
    btn._popup._on_row_toggled("kale", True)

    assert seen[-1] == ["kale"]
    assert btn.text() == "Kale"
    btn._popup.close()


def test_button_survives_a_theme_change(qapp):
    btn = PickerButton("T")
    btn.setStyleSheet("")

    qapp.sendEvent(btn, QEvent(QEvent.Type.StyleChange))

    assert "QToolButton" in btn.styleSheet()


# ---------------------------------------------------------------------------
# Header: count and Clear
# ---------------------------------------------------------------------------


def test_selection_caption_is_empty_when_nothing_is_selected():
    assert selection_caption(0) == ""


def test_selection_caption_counts():
    assert selection_caption(3) == "3 selected"


def test_clear_is_not_offered_until_something_is_selected(qapp):
    popup = PickerPopup("T", SECTIONS)
    popup._header.resize(300, 28)

    assert THEME.accent not in _colors(popup._header)

    popup._on_row_toggled("apple", True)

    assert THEME.accent in _colors(popup._header)


def test_clicking_clear_in_the_header_empties_the_selection(qapp):
    """Clear is hit-tested against the rectangle the paint pass measured, since it
    is drawn rather than being a child widget."""
    popup = PickerPopup("T", SECTIONS, ["apple"])
    popup._header.resize(300, 28)
    popup._header.grab()  # lays out _clear_rect

    QTest.mouseClick(popup._header, Qt.MouseButton.LeftButton, pos=popup._header._clear_rect.center())

    assert popup.selection() == []


def test_the_header_shows_the_count(qapp):
    popup = PickerPopup("T", SECTIONS, ["apple", "kale"])

    assert popup._header._caption == "2 selected"


def test_clear_empties_the_selection_and_the_checkboxes(qapp):
    popup = PickerPopup("T", SECTIONS, ["apple", "kale"])
    seen = []
    popup.selection_changed.connect(seen.append)

    popup._clear()

    assert popup.selection() == []
    assert seen[-1] == []
    assert all(not w._selected for w in popup.findChildren(_RowWidget))


# ---------------------------------------------------------------------------
# Selected rows read as selected
# ---------------------------------------------------------------------------


def _colors(row) -> set[str]:
    """Every colour the row actually paints, as hex."""
    img = row.grab().toImage()
    return {img.pixelColor(x, y).name() for y in range(img.height()) for x in range(img.width())}


def test_a_selected_row_is_tinted(qapp):
    """Only the checkbox said so before, which made scanning slow."""
    popup = PickerPopup("T", SECTIONS)
    row = popup.findChildren(_RowWidget)[0]
    row.resize(300, 26)

    row.toggle()

    assert THEME.node_fill_selected in _colors(row)


def test_an_unselected_row_is_not_tinted(qapp):
    popup = PickerPopup("T", SECTIONS)
    row = popup.findChildren(_RowWidget)[0]
    row.resize(300, 26)

    assert THEME.node_fill_selected not in _colors(row)


def test_the_checkbox_is_visible_against_the_row(qapp):
    """It went the colour of the background once, when a stylesheet on the QCheckBox
    cost it the platform's own indicator and the palette drew the replacement."""
    popup = PickerPopup("T", SECTIONS)
    row = popup.findChildren(_RowWidget)[0]
    row.resize(300, 26)

    assert THEME.border in _colors(row), "unchecked indicator has no visible outline"


def test_a_checked_box_shows_a_checkmark(qapp):
    popup = PickerPopup("T", SECTIONS, ["apple"])
    row = popup.findChildren(_RowWidget)[0]
    row.resize(300, 26)
    painted = _colors(row)

    assert THEME.accent in painted, "checked indicator is not filled"
    assert THEME.pill_text in painted, "checked indicator has no checkmark"


def test_the_keyboard_cursor_paints_its_edge_rule(qapp):
    popup = PickerPopup("T", SECTIONS)
    row = popup.findChildren(_RowWidget)[0]
    row.resize(300, 26)

    row.set_cursor_row(True)

    img = row.grab().toImage()
    assert img.pixelColor(0, 13).name() == THEME.accent


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------


def _key(popup, key):
    QTest.keyClick(popup._search, key)


def test_down_moves_the_cursor_to_the_first_row(qapp):
    popup = PickerPopup("T", SECTIONS)

    _key(popup, Qt.Key.Key_Down)

    assert popup._cursor_key == "apple"


def test_down_then_up_returns(qapp):
    popup = PickerPopup("T", SECTIONS)

    _key(popup, Qt.Key.Key_Down)
    _key(popup, Qt.Key.Key_Down)
    _key(popup, Qt.Key.Key_Up)

    assert popup._cursor_key == "apple"


def test_the_cursor_stops_at_the_ends(qapp):
    popup = PickerPopup("T", SECTIONS)

    for _ in range(10):
        _key(popup, Qt.Key.Key_Down)

    assert popup._cursor_key == "kale"


def test_enter_toggles_the_row_under_the_cursor(qapp):
    popup = PickerPopup("T", SECTIONS)

    _key(popup, Qt.Key.Key_Down)
    _key(popup, Qt.Key.Key_Return)

    assert popup.selection() == ["apple"]


def test_enter_with_no_cursor_does_nothing(qapp):
    popup = PickerPopup("T", SECTIONS)

    _key(popup, Qt.Key.Key_Return)

    assert popup.selection() == []


def test_space_still_types_into_the_filter(qapp):
    """Space cannot toggle: the filter box has focus and needs it for text."""
    popup = PickerPopup("T", SECTIONS)
    _key(popup, Qt.Key.Key_Down)

    QTest.keyClicks(popup._search, "a b")

    assert popup._search.text() == "a b"
    assert popup.selection() == []


def test_the_cursor_drops_when_filtering_hides_its_row(qapp):
    """Down after typing should start at the first surviving row, not resume
    from something no longer on screen."""
    popup = PickerPopup("T", SECTIONS)
    _key(popup, Qt.Key.Key_Down)

    popup._search.setText("kale")
    _drain(qapp)

    assert popup._cursor_key is None
    _key(popup, Qt.Key.Key_Down)
    assert popup._cursor_key == "kale"


# ---------------------------------------------------------------------------
# Dimming
# ---------------------------------------------------------------------------


def test_a_dim_row_still_renders_and_still_toggles(qapp):
    """Dim means "no results behind this", not "disabled"."""
    sections = [("S", [PickerRow("x", "X", meta="0 bots", dim=True)])]
    popup = PickerPopup("T", sections)
    row = popup.findChildren(_RowWidget)[0]

    row.toggle()

    assert popup.selection() == ["x"]


# ---------------------------------------------------------------------------
# Clicking the button while the popup is open
# ---------------------------------------------------------------------------


def test_a_click_right_after_a_close_does_not_reopen():
    """Qt dismisses the popup on the press, then delivers that press to the button
    underneath — which would open a second popup on top of the first."""
    assert not should_open(now=100.0, closed_at=99.95)


def test_a_later_click_opens_again():
    assert should_open(now=100.0, closed_at=99.0)


def test_the_first_ever_click_opens():
    assert should_open(now=100.0, closed_at=None)


def test_clicking_the_button_twice_leaves_no_popup_open(qapp):
    btn = PickerButton("T")
    btn.set_sections(SECTIONS)

    btn._open()
    first = btn._popup
    first.close()  # what Qt does on the press
    btn._open()  # ...and then the press reaches the button

    assert btn._popup is first
    assert not first.isVisible()


def test_a_programmatic_close_does_not_swallow_the_next_click(qapp):
    """set_selection closes the popup; that is not the click-dismiss case."""
    btn = PickerButton("T")
    btn.set_sections(SECTIONS)
    btn._open()

    btn.set_selection(["apple"])
    btn._open()

    assert btn._popup is not None
    assert btn._popup.isVisible()
    btn._popup.close()


def test_closing_the_popup_emits_closed(qapp):
    popup = PickerPopup("T", SECTIONS)
    seen = []
    popup.closed.connect(lambda: seen.append(1))
    popup.show()

    popup.close()

    assert seen
