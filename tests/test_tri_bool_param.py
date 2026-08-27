"""Tests for the three-state param widget behind quicksell's sell_* fields.

Real widget tests (QApplication required): what matters here is the round trip
through the form — that "use the app setting" survives as None rather than
collapsing to a bool, since a bot file that stores False instead of null pins a
value its author never chose. The resolution itself is covered by
tests/test_quicksell_keep_list.py.
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot.gui.canvas.param_panel import _DynamicParamForm, _TriBoolComboBox
from bartlebot.nodes.utility.quicksell import PARAM_SCHEMA
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _form(qapp, params: dict) -> _DynamicParamForm:
    form = _DynamicParamForm()
    form.set_drops_getter(list)
    form.load_schema(PARAM_SCHEMA, params)
    return form


def _combo(form) -> _TriBoolComboBox:
    return next(w for w in form.findChildren(_TriBoolComboBox))


def test_a_bot_with_no_opinion_loads_as_use_app_setting(qapp):
    assert _form(qapp, {}).get_params()["sell_pets"] is None


def test_a_stored_null_loads_as_use_app_setting(qapp):
    assert _form(qapp, {"sell_pets": None}).get_params()["sell_pets"] is None


def test_a_pinned_value_round_trips(qapp):
    for pinned in (True, False):
        assert _form(qapp, {"sell_pets": pinned}).get_params()["sell_pets"] is pinned


def test_choosing_a_value_pins_it(qapp):
    form = _form(qapp, {})
    combo = _combo(form)
    combo.setCurrentIndex(combo.findData(False))
    assert form.get_params()["sell_pets"] is False


def test_choosing_use_app_setting_clears_the_pin(qapp):
    form = _form(qapp, {"sell_pets": True})
    combo = _combo(form)
    combo.setCurrentIndex(combo.findData(None))
    assert form.get_params()["sell_pets"] is None


def test_the_unset_choice_names_the_value_in_force(qapp):
    # "Use app setting" alone does not tell the reader what will happen.
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)
    combo = _TriBoolComboBox("default_quicksell_sell_pets")
    assert combo.itemText(0) == "Use app setting (No)"


def test_an_unreadable_setting_does_not_stop_the_panel_opening(qapp):
    combo = _TriBoolComboBox("no_such_setting")
    assert combo.itemData(0) is None
