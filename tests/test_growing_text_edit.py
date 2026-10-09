"""Tests for GrowingTextEdit — the clamp, and the two ends of it.

Real widget tests (QApplication required), in the style of
test_player_list_widget.  Heights are asserted in *lines* rather than pixels
wherever possible: the line height comes from the platform font, so a pixel
assertion would be measuring the CI runner's fonts rather than the widget.
"""

import pytest
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from bartlebot.gui.widgets.inputs.growing_text_edit import GrowingTextEdit


@pytest.fixture
def edit(qapp):
    w = GrowingTextEdit(min_lines=2, max_lines=8)
    # A width the document can wrap against, so the wrapping tests mean something.
    w.resize(240, 100)
    return w


def _line_height(w: GrowingTextEdit) -> int:
    """One line's worth of growth, isolated from the widget's fixed chrome."""
    return w._line_height(2) - w._line_height(1)  # noqa: SLF001 - measuring the unit under test


def _lines(w: GrowingTextEdit) -> float:
    """``preferred_height`` expressed in lines, so assertions are font-independent."""
    return (w.preferred_height() - w._line_height(1)) / _line_height(w) + 1  # noqa: SLF001


class TestClamp:
    def test_an_empty_editor_is_the_floor(self, edit) -> None:
        """The placeholder prompt wraps to two lines at a 300px sidebar."""
        assert edit.preferred_height() == edit._line_height(2)  # noqa: SLF001

    def test_one_line_of_text_still_gets_the_floor(self, edit) -> None:
        edit.setPlainText("Endlessly farms Foulgaze in Olde Town.")
        assert edit.preferred_height() == edit._line_height(2)  # noqa: SLF001

    def test_it_grows_with_the_text(self, edit) -> None:
        edit.setPlainText("\n".join(f"line {i}" for i in range(5)))
        assert edit.preferred_height() == pytest.approx(edit._line_height(5), abs=2)  # noqa: SLF001

    def test_it_stops_at_the_ceiling(self, edit) -> None:
        edit.setPlainText("\n".join(f"line {i}" for i in range(40)))
        assert edit.preferred_height() == edit._line_height(8)  # noqa: SLF001

    def test_the_ceiling_leaves_a_scrollbar_to_reach_the_rest(self, edit) -> None:
        """Growth stops; the text does not, so it has to go somewhere."""
        edit.setPlainText("\n".join(f"line {i}" for i in range(40)))
        edit.resize(240, edit.preferred_height())
        assert edit.verticalScrollBar().maximum() > 0

    def test_a_ceiling_below_the_floor_is_raised_to_it(self, qapp) -> None:
        w = GrowingTextEdit(min_lines=4, max_lines=2)
        assert w.line_bounds == (4, 4)


class TestWrapping:
    def test_a_long_paragraph_counts_its_wrapped_lines(self, edit) -> None:
        """The reason this is a QTextEdit: height follows the *wrapped* layout."""
        edit.setPlainText("word " * 200)
        assert edit.preferred_height() == edit._line_height(8)  # noqa: SLF001

    def test_narrowing_the_widget_makes_it_taller(self, edit) -> None:
        """A height measured once at one width is wrong at the other."""
        edit.resize(400, 100)
        edit.setPlainText("Endlessly farms the Spiral Cup Gauntlet with a friend porting in.")
        wide = edit.preferred_height()
        edit.resize(160, 100)
        assert edit.preferred_height() > wide


class TestGeometryReporting:
    def test_size_hint_follows_the_text(self, edit) -> None:
        before = edit.sizeHint().height()
        edit.setPlainText("\n".join(f"line {i}" for i in range(6)))
        assert edit.sizeHint().height() > before

    def test_the_minimum_size_hint_is_the_floor(self, edit) -> None:
        assert edit.minimumSizeHint().height() == edit._line_height(2)  # noqa: SLF001

    def test_changing_the_font_rescales_the_bounds(self, edit) -> None:
        small = edit._line_height(2)  # noqa: SLF001
        font = QFont(edit.font())
        font.setPixelSize(30)
        edit.setFont(font)
        assert edit._line_height(2) > small  # noqa: SLF001

    def test_it_does_not_pin_its_own_height(self, edit) -> None:
        """The layout decides geometry; the widget only reports what it wants.

        setFixedHeight would leave the widget fighting the sidebar's layout, and
        is what the old three-line box did.
        """
        assert edit.maximumHeight() > edit._line_height(8)  # noqa: SLF001


def _shown_page(qapp) -> tuple[QScrollArea, GrowingTextEdit]:
    """A scrollable page holding an editor, shown and *active*.

    ``show()`` only asks Windows to activate the window; the answer arrives as a
    later event, and under ``-n auto`` it can arrive after the test has finished.
    Until it does, ``hasFocus`` is false however many times the editor is given
    focus — so the widget declines to schedule the scroll this class is about and
    the test fails having exercised nothing.  ``setActiveWindow`` hands the
    activation over synchronously instead, which is the whole reason it is called
    here: the widget's rule is focus, and the test has to be able to state it.

    Arranging activation is not the same as *keeping* it -- see ``_grown_page``.
    """
    area = QScrollArea()
    page = QWidget()
    layout = QVBoxLayout(page)
    edit = GrowingTextEdit(min_lines=2, max_lines=8)
    layout.addWidget(edit)
    layout.addStretch()
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.resize(240, 60)
    area.show()
    qapp.processEvents()
    QApplication.setActiveWindow(area)
    return area, edit


def _grown_page(qapp) -> tuple[QScrollArea, GrowingTextEdit]:
    """A page whose editor has grown eight lines while holding focus throughout.

    Activation is arranged, never owned.  Any other process putting a window up takes
    the foreground, and Windows answers ours with a deactivation that Qt turns into a
    cleared focus widget -- so if that lands between the growth and the scroll, which
    is a window the pump itself delivers, ``GrowingTextEdit`` correctly declines to
    scroll a field it believes unfocused and the test fails ``assert 0 > 0`` having
    exercised nothing.  Under ``-n auto`` the other process is one of the fifteen
    other workers, which is why this flaked only in parallel (#94).

    A test cannot promise the foreground, so it re-runs the arrangement instead: a
    round that lost focus proves nothing and is discarded rather than failed.  The
    ``hasFocus`` assertion is what stops a run of steals passing as a page that simply
    never grew -- it is the precondition the widget reads, asserted at both ends.
    """
    for _ in range(5):
        area, edit = _shown_page(qapp)
        edit.setFocus()
        assert edit.hasFocus()
        # Typed, not assigned: setPlainText leaves the caret at the *top*, where
        # there is nothing to scroll to.  The case this exists for is a user
        # adding lines at the bottom of a description.
        for i in range(8):
            edit.insertPlainText(f"line {i}\n")
        for _ in range(4):  # document relayout, the resize it is granted, the scroll
            qapp.processEvents()
        if edit.hasFocus():
            return area, edit
        area.close()

    raise AssertionError("the foreground was taken on every attempt")


class TestCaretVisibility:
    def test_growing_scrolls_an_enclosing_page_to_the_caret(self, qapp) -> None:
        """Growth is top-anchored, so the line being typed moves down and out."""
        area, _edit = _grown_page(qapp)

        assert area.verticalScrollBar().value() > 0
        area.close()

    def test_an_unfocused_editor_leaves_the_page_alone(self, qapp) -> None:
        """A bot being opened must not scroll the sidebar out from under the user."""
        area, edit = _shown_page(qapp)

        # Activating a window hands focus to the first child that will take it, and
        # the editor is the only one — so the case has to be arranged rather than
        # assumed.  Without the activation above it would arrange itself, and the
        # test would pass on a window that simply had no focus to give.
        edit.clearFocus()
        assert not edit.hasFocus()

        for i in range(8):
            edit.insertPlainText(f"line {i}\n")
        for _ in range(4):
            qapp.processEvents()

        assert area.verticalScrollBar().value() == 0
        area.close()
