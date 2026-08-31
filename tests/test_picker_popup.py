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

from bartlebot.gui.widgets.picker_popup import (
    PickerButton,
    PickerPopup,
    PickerRow,
    _RowWidget,
    filter_sections,
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
