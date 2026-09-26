"""Tests for ``reserve_scrollbar_gutter`` — the Qt stand-in for ``scrollbar-gutter: stable``.

What it promises is two things: the viewport is the same width whether or not the content
overflows, and the bar is only *drawn* while there is something to scroll.
"""

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.sidebar.combat_tab import CombatTab
from bartlebot.gui.widgets.picker_popup import PickerPopup
from bartlebot.gui.widgets.scroll_gutter import GUTTER_GAP, is_gutter_reserved, reserve_scrollbar_gutter


def _area(reserved: bool) -> tuple[QScrollArea, QVBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    if reserved:
        reserve_scrollbar_gutter(area)
    content = QWidget()
    rows = QVBoxLayout(content)
    rows.addWidget(QLabel("one row"))
    area.setWidget(content)
    area.resize(200, 150)
    area.show()
    QApplication.processEvents()
    return area, rows


def _overflow(rows: QVBoxLayout) -> None:
    for i in range(30):
        rows.addWidget(QLabel(f"row {i}"))
    # Two passes: the content's layout request, then the scroll area's own relayout.
    QApplication.processEvents()
    QApplication.processEvents()


def _painted(area: QScrollArea) -> bool:
    """Whether the bar's own paint would run — False when the filter swallows it."""
    bar = area.verticalScrollBar()
    event = QPaintEvent(bar.rect())
    return not any(f.eventFilter(bar, event) for f in bar.children() if hasattr(f, "eventFilter"))


def test_the_viewport_keeps_its_width_when_the_content_overflows(qapp):
    area, rows = _area(reserved=True)
    before = area.viewport().width()

    _overflow(rows)

    assert area.verticalScrollBar().maximum() > 0
    assert area.viewport().width() == before
    area.close()


def test_without_it_the_viewport_narrows_which_is_the_jolt_being_fixed(qapp):
    area, rows = _area(reserved=False)
    before = area.viewport().width()

    _overflow(rows)

    assert area.viewport().width() < before
    area.close()


def test_the_bar_is_not_drawn_while_there_is_nothing_to_scroll(qapp):
    area, _rows = _area(reserved=True)

    assert area.verticalScrollBar().maximum() == 0
    assert not _painted(area)
    area.close()


def test_the_bar_is_drawn_once_there_is(qapp):
    area, rows = _area(reserved=True)

    _overflow(rows)

    assert _painted(area)
    area.close()


def test_the_content_stops_short_of_the_bar(qapp):
    """A bar flush against a card's border reads as part of the card."""
    area, rows = _area(reserved=True)
    _overflow(rows)
    viewport, bar = area.viewport(), area.verticalScrollBar()
    # The bar lives in Qt's own container widget, so both are measured in the area's frame.
    viewport_right = viewport.mapTo(area, viewport.rect().topRight()).x()
    bar_left = bar.mapTo(area, bar.rect().topLeft()).x()

    assert bar_left - viewport_right - 1 == GUTTER_GAP
    area.close()


def test_applying_it_twice_installs_one_filter(qapp):
    area = QScrollArea()
    reserve_scrollbar_gutter(area)
    reserve_scrollbar_gutter(area)

    assert is_gutter_reserved(area)
    assert sum(1 for c in area.verticalScrollBar().children() if type(c).__name__ == "_BlankWhenIdle") == 1


def test_a_plain_area_is_not_reserved(qapp):
    assert not is_gutter_reserved(QScrollArea())


def test_other_events_pass_through(qapp):
    area, _rows = _area(reserved=True)
    bar = area.verticalScrollBar()
    event = QEvent(QEvent.Type.Resize)

    assert not any(f.eventFilter(bar, event) for f in bar.children() if hasattr(f, "eventFilter"))
    area.close()


# ---------------------------------------------------------------------------
# Where it is applied — a pane whose content grows under the user. The Combat
# Strategies dialog's two are checked in test_combat_strategy_dialog.py.
# ---------------------------------------------------------------------------


def _scroll_areas(widget: QWidget) -> list[QScrollArea]:
    return widget.findChildren(QScrollArea)


def test_the_picker_popup_reserves_its_gutter(qapp):
    """Every keystroke in its search changes how many rows there are."""
    popup = PickerPopup("T", [])

    assert [is_gutter_reserved(a) for a in _scroll_areas(popup)] == [True]


def test_every_inspector_page_reserves_its_gutter(qapp):
    """All of them, so the inspector keeps one content width whichever page is up."""
    areas = _scroll_areas(_ParamPanel())

    assert len(areas) == 4
    assert all(is_gutter_reserved(a) for a in areas)


def test_the_combat_tab_reserves_its_gutter(qapp):
    """Its sections fold, and decks come and go."""
    areas = _scroll_areas(CombatTab())

    assert areas and all(is_gutter_reserved(a) for a in areas)
