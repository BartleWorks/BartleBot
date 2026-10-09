"""The crumb bar's own drawing: how wide it makes itself, and how it cuts a long name.

Real widget tests (QApplication required). Which segments it shows, and what a click
does, are the canvas's, and are tested with it (test_subgraph_stepping, test_template_drafts).
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QWidget

from bartlebot.gui.canvas.subgraph_widgets import CRUMB_NAME_LIMIT, Crumb, CrumbBar, CrumbMode, elided_name

LONG = "Spellement Farmer For The Whole Of Aquila"


@pytest.fixture
def bar(qapp):
    host = QWidget()
    host.resize(1600, 400)
    bar = CrumbBar(host)
    host.show()
    qapp.processEvents()
    yield bar
    host.close()


def _path_right(bar: CrumbBar) -> int:
    last = bar.segment_labels[-1]
    return last.mapTo(bar, last.rect().topRight()).x()


@pytest.mark.parametrize("name", ["Boss Farmer", LONG])
def test_the_bar_is_as_wide_as_what_it_shows_the_moment_it_shows_it(bar, name):
    """It measured itself before its labels had their fonts, and the path ran on under the buttons."""
    bar.show_state(CrumbMode.TEMPLATE, [Crumb(name, prefix="Template draft \u00b7 ")], hint="latest v1")

    assert bar.width() == bar.sizeHint().width()
    assert _path_right(bar) < bar.save_button.x() < bar.release_button.x() < bar.close_button.x()
    assert bar.close_button.geometry().right() <= bar.width()


def test_a_long_name_is_cut_short_and_its_tooltip_says_it_in_full(bar):
    bar.show_state(CrumbMode.TEMPLATE, [Crumb(LONG, prefix="Template draft \u00b7 ")])
    (segment,) = bar.segment_labels

    assert segment.text() == f"Template draft \u00b7 {LONG[: CRUMB_NAME_LIMIT - 1].rstrip()}\u2026"
    assert segment.toolTip() == f"Template draft \u00b7 {LONG}"
    assert bar.text == f"Template draft \u00b7 {LONG}", "what the bar means is still the whole name"
    assert bar.shown_text == segment.text()


def test_a_name_that_fits_is_left_alone_and_needs_no_tooltip(bar):
    bar.show_state(CrumbMode.TEMPLATE, [Crumb("Boss Farmer", prefix="Template draft \u00b7 ")])
    (segment,) = bar.segment_labels

    assert segment.text() == "Template draft \u00b7 Boss Farmer" and segment.toolTip() == ""


def test_the_limit_is_on_the_name_not_on_what_the_bar_says_before_it():
    assert elided_name("x" * CRUMB_NAME_LIMIT) == "x" * CRUMB_NAME_LIMIT
    assert elided_name("x" * (CRUMB_NAME_LIMIT + 1)) == "x" * (CRUMB_NAME_LIMIT - 1) + "\u2026"
    assert Crumb("Boss", prefix="Focus Mode \u2014 ").shown == "Focus Mode \u2014 Boss"


def test_an_earlier_segment_says_where_it_goes_back_to_in_full(bar):
    bar.show_state(CrumbMode.DEFINITION, [Crumb(LONG), Crumb("Sync players")])
    first, last = bar.segment_labels

    assert first.text().endswith("\u2026") and first.toolTip() == f"Back to {LONG}"
    assert last.text() == "Sync players"
