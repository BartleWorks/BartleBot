"""Tests for SchoolCombo — the shared "author a school requirement" control."""

from bartlebot.deck_io import PIP_SCHOOLS
from bartlebot.gui.widgets.school_combo import SchoolCombo


def test_defaults_to_any_school(qapp):
    combo = SchoolCombo()
    assert combo.value() is None


def test_lists_every_pip_school(qapp):
    combo = SchoolCombo()
    values = [combo.itemData(i) for i in range(combo.count())]
    assert values == [None, *PIP_SCHOOLS]


def test_set_value_selects_the_matching_school(qapp):
    combo = SchoolCombo()
    combo.set_value("Storm")
    assert combo.value() == "Storm"
    assert combo.currentText() == "Storm"


def test_set_value_none_selects_any_school(qapp):
    combo = SchoolCombo()
    combo.set_value("Storm")
    combo.set_value(None)
    assert combo.value() is None


def test_set_value_unknown_school_falls_back_to_any(qapp):
    """A stale/foreign value in a hand-edited file must not crash the combo."""
    combo = SchoolCombo()
    combo.set_value("NotASchool")
    assert combo.value() is None
