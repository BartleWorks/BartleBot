"""ChooseFightsDialog: step 2 of adding a duel strategy, which has to be answered (#272)."""

from __future__ import annotations

from PyQt6.QtCore import Qt

from bartlebot.gui.dialogs.choose_fights_dialog import ChooseFightsDialog, FightsAnswer
from bartlebot.gui.widgets.labels.step_heading import Step
from bartlebot.gui.widgets.pickers.picker_popup import PickerRow

SECTIONS = [
    ("", [PickerRow(key="state_1", label="Lamia", meta="Default"), PickerRow(key="state_2", label="Krokopatra")])
]


def _dialog() -> ChooseFightsDialog:
    return ChooseFightsDialog(Step(2, 2, "Which fights use 'Boss Burn'?", "Each fight moves."), SECTIONS)


def _check(dlg: ChooseFightsDialog, key: str) -> None:
    for row in dlg.picker._rows:
        if row.key == key:
            row.toggle()


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, parentless_shows):
    _dialog()

    assert parentless_shows == []


def test_it_heads_itself_as_the_second_step(qapp):
    dlg = _dialog()

    assert dlg.heading.counter_text() == "Step 2 of 2"
    assert dlg.heading.heading_text() == "Which fights use 'Boss Burn'?"


def test_the_list_is_part_of_the_dialog_not_a_popup_that_a_click_dismisses(qapp):
    dlg = _dialog()

    assert dlg.picker.windowType() == Qt.WindowType.Widget
    assert dlg.picker.parent() is dlg


def test_add_waits_for_a_fight(qapp):
    dlg = _dialog()
    assert not dlg.add_button.isEnabled()

    _check(dlg, "state_2")

    assert dlg.add_button.isEnabled()


def test_add_answers_with_the_fights_checked(qapp):
    dlg = _dialog()
    _check(dlg, "state_2")
    _check(dlg, "state_1")

    dlg.add_button.click()

    assert dlg.answer is FightsAnswer.ADD
    assert dlg.selection() == ["state_1", "state_2"]
    assert dlg.result() == dlg.DialogCode.Accepted


def test_back_answers_back(qapp):
    dlg = _dialog()

    dlg.back_button.click()

    assert dlg.answer is FightsAnswer.BACK


def test_cancel_and_escape_add_nothing(qapp):
    dlg = _dialog()
    _check(dlg, "state_1")

    dlg.reject()

    assert dlg.answer is FightsAnswer.CANCEL
