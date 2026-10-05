"""The Settings dialog's rows (#148): the changed bar and Reset, the ``?``, the folding
groups and where their folds are kept, and Show/Hide descriptions across the dialog."""

from __future__ import annotations

import json

import pytest
from PyQt6.QtWidgets import QCheckBox, QSpinBox

from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.gui.settings.rows import (
    FOLD_SURFACE,
    SETTINGS_FOLD_DEFAULTS,
    SettingGroup,
    SettingRow,
    SettingsForm,
)
from bartlebot.gui.settings.settings_dialog import SettingsDialog
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.settings_manager import SettingsManager
from bartlebot.shared.vendor import product_dir
from bartlebot.ui_state import UI_STATE_FILE, UiState


def _check_row(default: bool = True, *, details: str = "Longer prose.") -> SettingRow:
    box = QCheckBox()
    box.setChecked(default)
    return SettingRow("A setting", "What it does.", box, default=default, details=details)


def _spin_row(value: int, default: int) -> SettingRow:
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setSuffix(" sec")
    spin.setValue(value)
    return SettingRow("Interval", "How often.", spin, default=default)


# ---------------------------------------------------------------------------
# The changed bar and Reset
# ---------------------------------------------------------------------------


def test_a_row_at_its_default_is_unmarked_and_has_nothing_to_reset(qapp):
    row = _spin_row(30, 30)

    assert not row.is_changed
    assert not row.revert_button.isEnabled()
    assert not row.bar.active


def test_editing_a_row_marks_it_and_reset_puts_the_default_back(qapp):
    row = _spin_row(30, 30)

    row.control.setValue(45)
    assert row.is_changed
    assert row.bar.active
    assert row.revert_button.isEnabled()

    row.revert_button.click()
    assert row.control.value() == 30
    assert not row.is_changed
    assert not row.revert_button.isEnabled()


def test_the_reset_tooltip_names_the_default_as_the_field_prints_it(qapp):
    row = _spin_row(45, 30)

    assert row.revert_button.toolTip() == "Reset to the default (30 sec)"


def test_a_default_the_field_cannot_hold_is_not_a_change(qapp):
    """A sidebar floor measured past the shipped width must not mark a fresh install."""
    spin = QSpinBox()
    spin.setRange(416, 1200)
    spin.setValue(404)  # clamped to 416, as the General tab's is

    assert not SettingRow("Width", "Its width.", spin, default=404).is_changed


def test_a_reset_is_pending_until_ok(qapp):
    """Nothing reaches the settings until apply, a reset no more than a typed value."""
    sm = SettingsManager.instance()
    sm.default_safety_delay = 7.5
    tab = BotTab()
    row = tab.row("Default Safety Delay")

    row.reset()
    assert row.control.value() == 3.0
    assert sm.default_safety_delay == 7.5

    tab.apply()
    assert sm.default_safety_delay == 3.0


# ---------------------------------------------------------------------------
# The ?
# ---------------------------------------------------------------------------


def test_the_question_mark_folds_the_longer_prose_in_and_out(qapp):
    row = _check_row()

    assert row.details is not None and row.details.isHidden()
    row.help_button.click()
    assert not row.details.isHidden()
    row.help_button.click()
    assert row.details.isHidden()


def test_a_row_with_nothing_more_to_say_keeps_the_question_marks_slot_empty(qapp):
    row = _check_row(details="")

    assert row.details is None
    assert row.help_button.isHidden()
    assert row.help_button.sizePolicy().retainSizeWhenHidden()


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def _group(*, header_on: bool = True) -> SettingGroup:
    return SettingGroup("quicksell", _check_row(header_on), [_check_row(False), _spin_row(30, 30)])


def test_a_group_starts_collapsed_with_its_rows_hidden(qapp):
    group = _group()

    assert group.is_collapsed
    assert all(row.isHidden() for row in group.rows)
    assert group.fold_button.toolTip() == "Expand A setting"


def test_opening_a_group_shows_its_rows(qapp):
    group = _group()

    group.fold_button.click()

    assert not group.is_collapsed
    assert not any(row.isHidden() for row in group.rows)
    assert group.fold_button.toolTip() == "Collapse A setting"


def test_a_group_whose_header_is_off_grays_its_rows_but_keeps_their_gutters(qapp):
    group = _group(header_on=False)

    for row in group.rows:
        assert not row.body.isEnabled()
        assert row.revert_button.parentWidget().isEnabled()

    group.header.control.setChecked(True)
    assert all(row.body.isEnabled() for row in group.rows)


def test_a_collapsed_group_lights_its_header_for_a_change_inside(qapp):
    """A fold never hides a change -- but Reset on the header resets only the header."""
    group = _group()
    group.rows[1].control.setValue(60)

    assert group.header.bar.active
    assert not group.header.revert_button.isEnabled()

    group.set_collapsed(False)
    # Open, the changed row says so itself.
    assert not group.header.bar.active


def test_a_fold_is_remembered_in_ui_state_and_read_back(qapp):
    _group().set_collapsed(False)

    assert not _group().is_collapsed
    stored = json.loads((product_dir() / UI_STATE_FILE).read_text(encoding="utf-8"))
    assert stored == {"folds": {FOLD_SURFACE: {"quicksell": False}}}


def test_folding_back_to_the_default_stores_nothing(qapp):
    group = _group()
    group.set_collapsed(False)
    group.set_collapsed(True)

    assert UiState().folds(FOLD_SURFACE, SETTINGS_FOLD_DEFAULTS) == SETTINGS_FOLD_DEFAULTS
    assert json.loads((product_dir() / UI_STATE_FILE).read_text(encoding="utf-8")) == {}


def test_folds_never_reach_settings_json(qapp):
    _group().set_collapsed(False)

    assert "folds" not in json.dumps(SettingsManager.instance().get("general", default={}))


def test_a_group_the_dialog_does_not_declare_is_refused(qapp):
    with pytest.raises(KeyError):
        SettingGroup("retired", _check_row(), [_check_row()])


def test_every_group_the_dialog_draws_is_declared():
    """A group missing from the declaration has its remembered fold pruned by the others."""
    dlg = SettingsDialog()
    drawn = {group.group_id for form in dlg.forms for group in form.groups}

    assert drawn == set(SETTINGS_FOLD_DEFAULTS)


# ---------------------------------------------------------------------------
# Show and Hide descriptions
# ---------------------------------------------------------------------------


def _click(form: SettingsForm, text: str) -> None:
    """Press one of the form's Show / Hide descriptions links."""
    next(link for link in form.switches.findChildren(LinkButton) if link.text() == text).click()


def test_show_descriptions_opens_every_row_and_is_remembered(qapp):
    tab = GeneralTab()

    _click(tab, "Show descriptions")

    assert all(row.details_shown for row in tab.rows if row.details is not None)
    assert SettingsManager.instance().get("general", "show_setting_descriptions") is True
    # A tab built afterwards opens the same way.
    assert all(row.details_shown for row in BotTab().rows if row.details is not None)


def test_hide_descriptions_folds_every_row(qapp):
    tab = GeneralTab()
    _click(tab, "Show descriptions")

    _click(tab, "Hide descriptions")

    assert not any(row.details_shown for row in tab.rows)


def test_the_dialogs_descriptions_are_apart_from_the_sidebars(qapp):
    _click(GeneralTab(), "Show descriptions")

    assert SettingsManager.instance().get("general", "show_option_descriptions") is False


def test_one_tabs_links_reach_every_form_in_the_dialog(qapp):
    """Including the forms inside Theme and Game, which are not form tabs themselves."""
    dlg = SettingsDialog()
    assert len(dlg.forms) == 6

    dlg.forms[1].descriptions_toggled.emit(True)

    for form in dlg.forms:
        assert all(row.details_shown for row in form.rows if row.details is not None), form


def test_building_the_dialog_never_shows_a_parentless_setting_row(qapp, parentless_shows):
    dlg = SettingsDialog()
    # Opening a group and its prose shows widgets too, and must find them parented.
    for form in dlg.forms:
        form.set_descriptions_shown(True)
        for group in form.groups:
            group.set_collapsed(False)

    assert parentless_shows == []
