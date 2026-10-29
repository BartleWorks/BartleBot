"""Tests for the reusable searchable multi-select picker.

The matching and summary logic is pure and tested directly; the popup itself needs a
QApplication, and is tested because the parts that break (row rebuild, live
selection, the self-styling recursion trap) are not extractable.
"""

import bartleby.qt.theming.theme as theme_mod
import pytest
from bartleby.qt.theming.theme import THEME
from PyQt6.QtCore import QEvent, QRect, QSize, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget

from bartlebot.gui.widgets.pickers.picker_popup import (
    _BOX,
    _PAD_X,
    POPUP_MIN_HEIGHT,
    SECTION_ICON_PX,
    PickerButton,
    PickerPopup,
    PickerRow,
    _RowWidget,
    _SectionHeader,
    filter_sections,
    popup_height,
    popup_position,
    selection_caption,
    should_open,
    summarize,
)


@pytest.fixture
def theme():
    """Pin the active theme for a test, and put it back afterwards.

    Settings ship ``"theme": "system"``, so THEME follows the Windows light/dark
    setting at read time. A paint test that does not say which theme it means is
    really testing whichever one the machine is in, and changes result when the OS
    does — which is how the checkbox test came to pass in the morning and fail later
    the same day.
    """
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


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
    btn = PickerButton("T", placeholder="Filter by tag")
    btn.set_sections(SECTIONS)

    assert btn.text() == "Filter by tag"


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


def test_committed_holds_until_the_popup_closes(qapp):
    """Two signals for two questions. A caller that rebuilds the widget tree this
    button lives in cannot react per toggle -- it would destroy the button while its
    own popup is still open on top of it -- so it waits for this one."""
    btn = PickerButton("T")
    btn.set_sections(SECTIONS)
    live, settled = [], []
    btn.selection_changed.connect(live.append)
    btn.committed.connect(settled.append)

    btn._open()
    btn._popup._on_row_toggled("kale", True)
    btn._popup._on_row_toggled("apple", True)

    assert len(live) == 2
    assert settled == []

    btn._popup.close()

    assert settled == [["apple", "kale"]]


def test_committed_fires_even_when_nothing_was_toggled(qapp):
    """Closing an untouched popup is a commit of the selection it opened with. The
    caller compares against what it had rather than the button guessing."""
    btn = PickerButton("T")
    btn.set_sections(SECTIONS)
    btn.set_selection(["kale"])
    settled = []
    btn.committed.connect(settled.append)

    btn._open()
    btn._popup.close()

    assert settled == [["kale"]]


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
    """Every color the row actually paints, as hex."""
    img = row.grab().toImage()
    return {img.pixelColor(x, y).name() for y in range(img.height()) for x in range(img.width())}


def _luminance(c) -> float:
    """WCAG relative luminance, for asking whether two colors are told apart."""

    def channel(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())


def _contrast(a, b) -> float:
    """Contrast ratio between two colors, 1.0 (identical) to 21.0 (black on white)."""
    lo, hi = sorted((_luminance(a), _luminance(b)))
    return (hi + 0.05) / (lo + 0.05)


def _indicator_contrast(row) -> float:
    """How far the checkbox's most distinct pixel sits from the row behind it.

    Asserting an exact ``THEME.border`` hex appears in the grab is the obvious test
    and the wrong one twice over. The outline is a 1px antialiased stroke, so in the
    light theme it blends and *no* pixel carries the token exactly — the test passed
    only because the machine happened to be in dark mode, and it is a screenshot
    assertion for a claim about visibility rather than about a color.
    """
    img = row.grab().toImage()
    bg = img.pixelColor(img.width() - 1, img.height() // 2)  # past the text, plain row
    box = QRect(_PAD_X, (row.height() - _BOX) // 2, _BOX, _BOX)
    return max(
        _contrast(img.pixelColor(x, y), bg)
        for y in range(box.top(), box.bottom() + 1)
        for x in range(box.left(), box.right() + 1)
    )


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


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_the_checkbox_is_visible_against_the_row(qapp, theme, mode):
    """It went the color of the background once, when a stylesheet on the QCheckBox
    cost it the platform's own indicator and the palette drew the replacement.

    Both themes, because the failure being guarded against is one theme's border
    disappearing into one theme's background.
    """
    theme(mode)
    popup = PickerPopup("T", SECTIONS)
    row = popup.findChildren(_RowWidget)[0]
    row.resize(300, 26)

    assert _indicator_contrast(row) > 1.5, "unchecked indicator has no visible outline"


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
# Section icons
# ---------------------------------------------------------------------------


def _icon(size: int):
    from PyQt6.QtGui import QPixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.red)
    return pixmap


def test_a_section_header_is_tall_enough_for_its_icon(qapp):
    popup = PickerPopup("T", SECTIONS, section_icon=lambda title: _icon(SECTION_ICON_PX))

    headers = popup.findChildren(_SectionHeader)

    assert len(headers) == 2
    assert all(h.icon is not None and h.height() >= SECTION_ICON_PX for h in headers)


def test_a_section_header_without_an_icon_stays_compact(qapp):
    """Most installs have no extracted art, and must not pay for the icon's height."""
    popup = PickerPopup("T", SECTIONS)

    assert all(h.icon is None and h.height() < SECTION_ICON_PX for h in popup.findChildren(_SectionHeader))


def test_a_resolver_that_raises_leaves_the_caption(qapp):
    def broken(title):
        raise RuntimeError("no art today")

    popup = PickerPopup("T", SECTIONS, section_icon=broken)

    assert [h.title for h in popup.findChildren(_SectionHeader)] == ["Fruit", "Veg"]
    assert len(popup.findChildren(_RowWidget)) == 3


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


# -- popup placement -------------------------------------------------------
#
# Pure geometry, so the 4K case can be checked from a 1080p machine and the other
# way round. A screen is 1920x1080 at the origin unless a test says otherwise.

_SCREEN = QRect(0, 0, 1920, 1080)
_SIZE = QSize(400, 560)


def test_the_popup_hangs_below_the_anchor_right_edges_flush():
    anchor = QRect(600, 200, 90, 24)

    pos = popup_position(anchor, _SIZE, _SCREEN)

    assert pos.x() + _SIZE.width() == anchor.right() + 1
    assert pos.y() == 224


def test_right_alignment_does_not_depend_on_running_out_of_room():
    # There is ample space to the right of this anchor; the popup right-aligns
    # anyway. Aligning left until it overflows makes the popup change sides as the
    # window is resized, which is the flip-flop this replaced.
    near = popup_position(QRect(600, 200, 90, 24), _SIZE, _SCREEN)
    far = popup_position(QRect(1800, 200, 90, 24), _SIZE, _SCREEN)

    assert near.x() == 600 + 90 - _SIZE.width()
    assert far.x() == 1800 + 90 - _SIZE.width()


def test_right_alignment_still_fits_on_screen():
    pos = popup_position(QRect(1800, 200, 90, 24), _SIZE, _SCREEN)

    assert pos.x() >= _SCREEN.left()
    assert pos.x() + _SIZE.width() <= _SCREEN.left() + _SCREEN.width()


def test_a_popup_that_cannot_right_align_flips_to_left_edges_flush():
    # A picker anchored in the 300px-minimum left sidebar: right-aligning a 400px
    # popup to a button at x=120 would put it at -190. Sliding it to the screen edge
    # instead would leave it with no edge in common with the button it belongs to.
    anchor = QRect(120, 200, 90, 24)

    pos = popup_position(anchor, _SIZE, _SCREEN)

    assert pos.x() == anchor.left()


def test_the_flip_is_not_taken_when_right_alignment_fits():
    # Guard against the flip becoming the flip-flop it replaced: it fires only where
    # right alignment leaves the screen, never merely because left would also fit.
    anchor = QRect(600, 200, 90, 24)

    assert popup_position(anchor, _SIZE, _SCREEN).x() != anchor.left()


def test_a_popup_wider_than_the_screen_is_clamped_rather_than_pushed_off_screen():
    # Neither alignment can be honored, so the screen edge wins.
    pos = popup_position(QRect(120, 200, 90, 24), QSize(2400, 560), _SCREEN)

    assert pos.x() == _SCREEN.left()


def test_a_popup_with_no_room_below_flips_above_the_anchor():
    anchor = QRect(300, 900, 90, 24)

    pos = popup_position(anchor, _SIZE, _SCREEN)

    assert pos.y() + _SIZE.height() == anchor.top()


def test_flipping_up_does_not_move_it_sideways():
    # The 1080p report: opens upward there and downward on 4K, and on both it had
    # drifted to the right edge. Vertical flip must not touch x.
    low = popup_position(QRect(1800, 900, 90, 24), _SIZE, _SCREEN)
    high = popup_position(QRect(1800, 200, 90, 24), _SIZE, _SCREEN)

    assert low.x() == high.x()


def test_a_4k_screen_leaves_room_to_open_downward():
    uhd = QRect(0, 0, 3840, 2160)
    anchor = QRect(300, 900, 90, 24)

    assert popup_position(anchor, _SIZE, uhd).y() == 924


def test_a_screen_that_does_not_start_at_the_origin_is_respected():
    second = QRect(1920, 0, 1920, 1080)

    pos = popup_position(QRect(1920, 200, 90, 24), _SIZE, second)

    assert pos.x() == second.left()  # clamped to this screen, not the primary's


def test_a_popup_taller_than_the_screen_is_clamped_rather_than_pushed_off_the_top():
    pos = popup_position(QRect(300, 900, 90, 24), QSize(400, 2000), _SCREEN)

    assert pos.y() == _SCREEN.top()


# -- popup height ----------------------------------------------------------
#
# The bot library's top bar is the top of main_window's *bottom* splitter pane, so
# on a 1080p screen a full-height popup fits in neither direction and the placement
# used to clamp — sliding the popup until it no longer met its button.


def test_a_popup_with_room_below_keeps_its_ideal_height():
    assert popup_height(QRect(600, 200, 90, 24), 420, _SCREEN) == 420


def test_a_popup_low_on_the_screen_shrinks_rather_than_clamping():
    # 700px above this anchor, 356 below: the popup opens upward at full height.
    assert popup_height(QRect(600, 700, 90, 24), 420, _SCREEN) == 420

    # A taller ideal that neither side can hold is cut to the roomier side (676
    # below) rather than clamped to the screen and slid off its button.
    assert popup_height(QRect(600, 380, 90, 24), 700, _SCREEN) == 676


def test_the_larger_side_decides_the_height():
    """Height is chosen for whichever direction popup_position will pick."""
    low = QRect(600, 900, 90, 24)  # 156 below, 900 above

    assert popup_height(low, 420, _SCREEN) == 420


def test_a_popup_is_never_shrunk_past_its_minimum():
    """Below this the header and search box stop fitting, which is worse than
    overhanging a screen edge — a screen edge at least still shows rows."""
    tiny = QRect(0, 0, 90, 24)
    cramped = QRect(0, 0, 1920, 120)

    assert popup_height(tiny, 420, cramped) == POPUP_MIN_HEIGHT


def test_shrinking_does_not_depend_on_the_screen_being_1080p():
    uhd = QRect(0, 0, 3840, 2160)

    assert popup_height(QRect(600, 700, 90, 24), 420, uhd) == 420


def test_an_embedded_picker_is_a_child_widget_not_a_popup_window(qapp):
    """For a question a dialog has to have answered (#272): no click-outside dismissal."""
    host = QWidget()
    popup = PickerPopup("Fights", [("", [PickerRow(key="a", label="A")])], parent=host, embedded=True)
    plain = PickerPopup("Fights", [("", [PickerRow(key="a", label="A")])], parent=host)

    assert popup.windowType() == Qt.WindowType.Widget
    assert plain.windowType() == Qt.WindowType.Popup
