"""The option card and its header switch (#138).

``OptionCard`` is one folding group of options on Option Values and Options; ``ToggleSwitch``
is the switch Enable Quicksell is drawn as in the Quicksell card's header. Neither
holds option state -- the surfaces drive them -- so these tests drive them directly.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from bartlebot.gui.sidebar.option_card import OptionCard
from bartlebot.gui.widgets.option_widgets import connect_widget, set_widget_value, widget_value
from bartlebot.gui.widgets.toggle_switch import SWITCH_HEIGHT_PX, SWITCH_WIDTH_PX, ToggleSwitch
from bartlebot.options.option_groups import SummaryPart

# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_the_switch_speaks_the_option_widget_protocol(qapp):
    """It is a check box underneath, so the option surfaces read and write it with no
    branch of their own."""
    switch = ToggleSwitch()
    seen = []
    connect_widget(switch, seen.append)

    set_widget_value(switch, True)

    assert widget_value(switch) is True
    assert seen == [True]


def test_a_click_anywhere_on_the_switch_toggles_it(qapp):
    """Not only on the style's idea of where a check box's indicator would be."""
    switch = ToggleSwitch()
    switch.show()
    try:
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(SWITCH_WIDTH_PX - 3, SWITCH_HEIGHT_PX // 2))
        assert switch.isChecked()
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(3, SWITCH_HEIGHT_PX // 2))
        assert not switch.isChecked()
    finally:
        switch.hide()


def test_the_switch_is_exactly_as_tall_as_a_row(qapp):
    """A taller switch would make the band taller than every row beneath it."""
    switch = ToggleSwitch()

    assert (switch.width(), switch.height()) == (SWITCH_WIDTH_PX, SWITCH_HEIGHT_PX)
    assert switch.sizeHint().height() == SWITCH_HEIGHT_PX


def test_the_switch_takes_focus_from_the_keyboard_only(qapp):
    """So the ring it draws is never left behind by a mouse click."""
    assert ToggleSwitch().focusPolicy() == Qt.FocusPolicy.TabFocus


def test_the_switch_paints_in_both_states(qapp):
    switch = ToggleSwitch()
    for checked in (False, True):
        switch.setChecked(checked)
        assert not switch.grab().isNull()


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def _card() -> OptionCard:
    card = OptionCard("quicksell", "Quicksell")
    card.body_layout.addWidget(QLabel("a row"))
    card.resize(400, 200)
    return card


def _shown(card: OptionCard) -> OptionCard:
    card.show()
    QApplication.processEvents()
    return card


def test_a_click_on_the_band_asks_to_fold(qapp):
    card = _shown(_card())
    seen = []
    card.fold_requested.connect(seen.append)
    try:
        QTest.mouseClick(card.header, Qt.MouseButton.LeftButton, pos=QPoint(card.header.width() - 20, 8))

        assert seen == [False]
    finally:
        card.hide()


def test_ctrl_click_asks_to_keep_this_card_alone_open(qapp):
    card = _shown(_card())
    seen = []
    card.fold_requested.connect(seen.append)
    try:
        QTest.mouseClick(
            card.header,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            QPoint(card.header.width() - 20, 8),
        )

        assert seen == [True]
    finally:
        card.hide()


def test_the_title_button_asks_too(qapp):
    """The fold mark and title are one button, which is what the keyboard reaches."""
    card = _card()
    seen = []
    card.fold_requested.connect(seen.append)

    card.fold_button.click()

    assert seen == [False]
    assert card.fold_button.focusPolicy() == Qt.FocusPolicy.TabFocus


def test_a_press_and_sweep_across_the_band_folds_nothing(qapp):
    """Activation is on release and only for a pointer that stayed put, as on every
    other card in the app."""
    card = _shown(_card())
    seen = []
    card.fold_requested.connect(seen.append)
    try:
        QTest.mousePress(card.header, Qt.MouseButton.LeftButton, pos=QPoint(card.header.width() - 120, 8))
        QTest.mouseRelease(card.header, Qt.MouseButton.LeftButton, pos=QPoint(card.header.width() - 20, 8))

        assert seen == []
    finally:
        card.hide()


def test_a_control_in_the_band_does_not_fold_the_card(qapp):
    """Quicksell can be switched off while its card is collapsed."""
    card = _card()
    switch = ToggleSwitch()
    card.add_header_widget(switch)
    _shown(card)
    seen = []
    card.fold_requested.connect(seen.append)
    try:
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))

        assert switch.isChecked()
        assert seen == []
    finally:
        card.hide()


def test_the_card_never_folds_itself(qapp):
    """The surface owns the fold state, remembered across bots and shared by both tabs."""
    card = _card()

    card.fold_button.click()

    assert not card.is_collapsed


def test_collapsing_hides_the_body_and_shows_the_summary(qapp):
    card = _shown(_card())
    card.set_summary([SummaryPart("pets kept"), SummaryPart("keeping 2 of 58")])
    try:
        assert card.body.isVisible()
        assert not card.summary_shown

        card.set_collapsed(True)
        QApplication.processEvents()

        assert not card.body.isVisible()
        assert card.summary_shown
        assert card._summary.toolTip() == "pets kept · keeping 2 of 58"
        assert card.fold_button.toolTip() == "Expand Quicksell"
    finally:
        card.hide()


def test_the_summary_is_hidden_while_expanded(qapp):
    """The rows say the same thing, and nothing should be on screen twice."""
    card = _shown(_card())
    card.set_summary([SummaryPart("pets kept")])
    try:
        card.set_collapsed(True)
        card.set_collapsed(False)
        QApplication.processEvents()

        assert not card.summary_shown
        assert card.fold_button.toolTip() == "Collapse Quicksell"
    finally:
        card.hide()


def test_the_warning_mark_shows_whether_folded_or_not(qapp):
    card = _shown(_card())
    try:
        card.set_warning("2 pets will be sold.")
        card.set_collapsed(True)
        QApplication.processEvents()

        assert card.warning == "2 pets will be sold."
        assert card._warning.isVisible()

        card.set_warning(None)
        assert card.warning is None
    finally:
        card.hide()


def test_a_right_click_on_the_band_asks_for_the_menu(qapp):
    card = _card()
    seen = []
    card.menu_requested.connect(seen.append)

    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(10, 5), QPoint(110, 205))
    QApplication.sendEvent(card.header, event)

    assert seen == [QPoint(110, 205)]


def test_the_menu_key_on_the_title_button_reaches_the_band(qapp):
    """Shift+F10 and the Menu key deliver a context-menu event to the focused button,
    which does not take it, so it has to arrive at the band."""
    card = _card()
    seen = []
    card.menu_requested.connect(seen.append)

    event = QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(2, 2), QPoint(50, 50))
    QApplication.sendEvent(card.fold_button, event)

    assert seen == [QPoint(50, 50)]


def test_header_widgets_land_at_the_right_end(qapp):
    card = _card()
    switch = ToggleSwitch()
    card.add_header_widget(switch)
    _shown(card)
    try:
        switch_x = switch.mapTo(card, QPoint(0, 0)).x()
        assert switch_x + switch.width() >= card.width() - 10
    finally:
        card.hide()


def test_the_card_paints_folded_marked_and_open(qapp):
    card = _card()
    for collapsed in (False, True):
        card.set_collapsed(collapsed)
        card.set_marked(collapsed)
        assert not card.grab().isNull()
