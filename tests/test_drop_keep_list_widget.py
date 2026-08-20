"""Tests for the quicksell keep-list widget in the properties panel.

Real widget tests (QApplication required): what is under test is the wiring —
that ticking a drop rewrites the keyword line, that rows kept by a keyword the
widget cannot edit are locked, and that the sell_* bools reach the display.
The matching itself is covered by tests/test_quicksell_keep_list.py.
"""

import re
import sys

import pytest
from PyQt6.QtWidgets import QApplication, QCheckBox

from bartlebot.gui.canvas.param_panel import _DropKeepListWidget, _DynamicParamForm
from bartlebot.nodes.utility.quicksell import PARAM_SCHEMA

_DROPS = ["Amber", "Amber Ring", "Boots of Dawn", "Socket Wrench"]
_SELL_ALL = {"sell_socket_wrenches": True, "sell_skeleton_keys": True}


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _make(qapp, drops=None, keep_list=None, context=None):
    w = _DropKeepListWidget(lambda: list(_DROPS if drops is None else drops))
    w.set_context(context if context is not None else _SELL_ALL)
    w.set_value(keep_list or [])
    return w


def _plain(text: str) -> str:
    """Strip the rich-text markup the summary lines use for emphasis."""
    return re.sub(r"<[^>]+>", "", text)


def _rows(w) -> list[QCheckBox]:
    return w.findChildren(QCheckBox)


def _row(w, name: str) -> QCheckBox:
    return next(r for r in _rows(w) if r.text() == name)


def test_every_drop_gets_a_row(qapp):
    assert [r.text() for r in _rows(_make(qapp))] == _DROPS


def test_keep_list_entries_start_checked(qapp):
    w = _make(qapp, keep_list=["Amber"])
    assert _row(w, "Amber").isChecked()
    assert not _row(w, "Boots of Dawn").isChecked()


def test_ticking_a_drop_adds_its_name_to_the_keep_list(qapp):
    w = _make(qapp)
    _row(w, "Boots of Dawn").setChecked(True)
    assert w.get_value() == ["Boots of Dawn"]


def test_unticking_a_drop_removes_it_from_the_keep_list(qapp):
    w = _make(qapp, keep_list=["Amber", "Boots of Dawn"])
    _row(w, "Amber").setChecked(False)
    assert w.get_value() == ["Boots of Dawn"]


def test_ticking_emits_changed(qapp):
    w = _make(qapp)
    seen = []
    w.changed.connect(lambda: seen.append(1))
    _row(w, "Amber").setChecked(True)
    assert seen


def test_row_kept_by_a_partial_keyword_is_locked(qapp):
    # "Amber" spares "Amber Ring" too, but unticking the ring cannot express that.
    w = _make(qapp, keep_list=["Amber"])
    ring = _row(w, "Amber Ring")
    assert ring.isChecked() and not ring.isEnabled()
    assert "Amber" in ring.toolTip() or "amber" in ring.toolTip()


def test_row_kept_by_a_sell_bool_is_locked(qapp):
    context = {"sell_socket_wrenches": False, "sell_skeleton_keys": True}
    w = _make(qapp, keep_list=[], context=context)
    wrench = _row(w, "Socket Wrench")
    assert wrench.isChecked() and not wrench.isEnabled()


def test_changing_a_sell_bool_updates_the_rows(qapp):
    w = _make(qapp, keep_list=[])
    assert not _row(w, "Socket Wrench").isChecked()
    w.set_context({"sell_socket_wrenches": False, "sell_skeleton_keys": True})
    assert _row(w, "Socket Wrench").isChecked()


def test_typed_keywords_survive_a_round_trip(qapp):
    w = _make(qapp, keep_list=["hat", "Amber"])
    assert w.get_value() == ["hat", "Amber"]


def test_summary_counts_kept_drops(qapp):
    w = _make(qapp, keep_list=["Amber"])
    # "Amber" and "Amber Ring" — the partial match counts, because the bot will keep it.
    assert "keeping 2 out of 4 possible drops" in _plain(w._summary.text())


def test_the_rule_line_states_what_a_check_means(qapp):
    rule = _plain(_make(qapp)._rule.text()).lower()
    assert "checked" in rule and "kept" in rule
    assert "unchecked" in rule and "sold" in rule


def test_no_drops_hides_the_list_and_the_rule(qapp):
    w = _make(qapp, drops=[])
    assert not _rows(w)
    # isVisibleTo, not isVisible: nothing here is ever shown on screen.
    assert not w._rule.isVisibleTo(w)
    assert "no drops" in w._summary.text().lower()


def test_the_rule_is_shown_once_there_are_drops(qapp):
    w = _make(qapp)
    assert w._rule.isVisibleTo(w)


def test_the_quicksell_form_builds_the_widget_and_round_trips_its_value(qapp):
    form = _DynamicParamForm()
    form.set_drops_getter(lambda: list(_DROPS))
    form.load_schema(PARAM_SCHEMA, {"keep_list": ["Amber"], **_SELL_ALL, "sell_pets": True})

    widget = next(w for w in form.findChildren(_DropKeepListWidget))
    assert _row(widget, "Amber").isChecked()
    assert form.get_params()["keep_list"] == ["Amber"]

    _row(widget, "Boots of Dawn").setChecked(True)
    assert form.get_params()["keep_list"] == ["Amber", "Boots of Dawn"]


def test_the_quicksell_form_feeds_its_sell_bools_to_the_widget(qapp):
    form = _DynamicParamForm()
    form.set_drops_getter(lambda: list(_DROPS))
    # sell_socket_wrenches False means wrenches are kept without a keep_list entry.
    form.load_schema(PARAM_SCHEMA, {"keep_list": [], "sell_socket_wrenches": False, "sell_skeleton_keys": True})

    widget = next(w for w in form.findChildren(_DropKeepListWidget))
    wrench = _row(widget, "Socket Wrench")
    assert wrench.isChecked() and not wrench.isEnabled()


def test_refresh_picks_up_drops_added_after_construction(qapp):
    drops = ["Amber"]
    w = _DropKeepListWidget(lambda: list(drops))
    w.set_context(_SELL_ALL)
    w.set_value([])
    drops.append("Boots of Dawn")
    w.refresh()
    assert [r.text() for r in _rows(w)] == ["Amber", "Boots of Dawn"]
