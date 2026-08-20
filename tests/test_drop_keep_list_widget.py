"""Tests for the quicksell keep-list picker.

Real widget tests (QApplication required): what is under test is the wiring —
that ticking a drop rewrites the keyword line, that rows kept by a keyword the
table cannot edit are locked, that filters compose and never lose check state,
and that bulk changes touch only what is shown. The matching itself is covered by
tests/test_quicksell_keep_list.py.
"""

import re
import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from bartlebot.bot.schema import DropModel
from bartlebot.gui.canvas.drop_keep_list import (
    _COL_ITEM,
    _COL_KEEP,
    DropKeepListWidget,
    bulk_needs_confirmation,
    row_visible,
)
from bartlebot.gui.canvas.param_panel import _DynamicParamForm
from bartlebot.nodes.utility.quicksell import PARAM_SCHEMA

_DROPS = [
    DropModel(name="Amber", category="Reagents"),
    DropModel(name="Amber Ring", category="Jewels"),
    DropModel(name="Boots of Dawn", category="Boots"),
    DropModel(name="Socket Wrench"),
]
_SELL_ALL = {"sell_socket_wrenches": True, "sell_skeleton_keys": True}


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _plain(text: str) -> str:
    """Strip the rich-text markup the summary lines use for emphasis."""
    return re.sub(r"<[^>]+>", "", text)


def _make(qapp, drops=None, keep_list=None, context=None):
    w = DropKeepListWidget(lambda: list(_DROPS if drops is None else drops))
    w.set_context(context if context is not None else _SELL_ALL)
    w.set_value(keep_list or [])
    return w


def _rows(w) -> list[str]:
    return [row.text(_COL_ITEM) for row in w._rows]


def _row(w, name: str):
    return next(row for row in w._rows if row.text(_COL_ITEM) == name)


def _checked(w, name: str) -> bool:
    return _row(w, name).checkState(_COL_KEEP) == Qt.CheckState.Checked


def _enabled(w, name: str) -> bool:
    return bool(_row(w, name).flags() & Qt.ItemFlag.ItemIsEnabled)


# ---------------------------------------------------------------------------
# row_visible — pure
# ---------------------------------------------------------------------------


def test_no_filters_shows_everything():
    assert row_visible("Amber", "Reagents", False, "", None, False)


def test_query_matches_case_insensitive_substring():
    assert row_visible("Amber Ring", "Jewels", False, "amber", None, False)
    assert not row_visible("Boots of Dawn", "Boots", False, "amber", None, False)


def test_category_filter_selects_one_category():
    assert row_visible("Amber", "Reagents", False, "", "Reagents", False)
    assert not row_visible("Amber", "Reagents", False, "", "Jewels", False)


def test_empty_category_filter_selects_uncategorized():
    assert row_visible("Socket Wrench", "", False, "", "", False)
    assert not row_visible("Amber", "Reagents", False, "", "", False)


def test_kept_only_hides_unkept():
    assert row_visible("Amber", "Reagents", True, "", None, True)
    assert not row_visible("Amber", "Reagents", False, "", None, True)


def test_filters_compose_rather_than_override():
    # Matches the query and the category, but is not kept.
    assert not row_visible("Amber", "Reagents", False, "amb", "Reagents", True)
    assert row_visible("Amber", "Reagents", True, "amb", "Reagents", True)


def test_query_agrees_with_the_keyword_matcher():
    """The rows a search shows must be exactly the drops that keyword would keep."""
    from bartlebot.nodes.utility.quicksell import matching_keyword

    names = ["Amber", "Amber Ring", "Boots of Dawn", "Socket Wrench", "AMBERGRIS"]
    for query in ("amber", "AMBER", "  boots ", "wrench", "zzz"):
        by_filter = {n for n in names if row_visible(n, "", False, query, None, False)}
        by_matcher = {n for n in names if matching_keyword(n, [query.strip().lower()])}
        assert by_filter == by_matcher, query


def test_bulk_confirmation_threshold():
    assert not bulk_needs_confirmation(25)
    assert bulk_needs_confirmation(26)


# ---------------------------------------------------------------------------
# Table contents
# ---------------------------------------------------------------------------


def test_every_drop_gets_a_row(qapp):
    assert _rows(_make(qapp)) == [d.name for d in _DROPS]


def test_keep_list_entries_start_checked(qapp):
    w = _make(qapp, keep_list=["Amber"])
    assert _checked(w, "Amber")
    assert not _checked(w, "Boots of Dawn")


def test_category_is_shown_and_missing_reads_as_a_dash(qapp):
    w = _make(qapp)
    assert _row(w, "Amber").text(2) == "Reagents"
    assert _row(w, "Socket Wrench").text(2) == "—"


def test_ticking_a_drop_adds_its_name_to_the_keep_list(qapp):
    w = _make(qapp)
    _row(w, "Boots of Dawn").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert w.get_value() == ["Boots of Dawn"]


def test_unticking_a_drop_removes_it_from_the_keep_list(qapp):
    w = _make(qapp, keep_list=["Amber", "Boots of Dawn"])
    _row(w, "Amber").setCheckState(_COL_KEEP, Qt.CheckState.Unchecked)
    assert w.get_value() == ["Boots of Dawn"]


def test_ticking_emits_changed(qapp):
    w = _make(qapp)
    seen = []
    w.changed.connect(lambda: seen.append(1))
    _row(w, "Amber").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert seen


def test_row_kept_by_a_partial_keyword_is_locked(qapp):
    # "Amber" spares "Amber Ring" too, but unticking the ring cannot express that.
    w = _make(qapp, keep_list=["Amber"])
    assert _checked(w, "Amber Ring") and not _enabled(w, "Amber Ring")
    assert "amber" in _row(w, "Amber Ring").toolTip(_COL_ITEM).lower()


def test_row_kept_by_a_sell_bool_is_locked(qapp):
    w = _make(qapp, keep_list=[], context={"sell_socket_wrenches": False, "sell_skeleton_keys": True})
    assert _checked(w, "Socket Wrench") and not _enabled(w, "Socket Wrench")


def test_changing_a_sell_bool_updates_the_rows(qapp):
    w = _make(qapp, keep_list=[])
    assert not _checked(w, "Socket Wrench")
    w.set_context({"sell_socket_wrenches": False, "sell_skeleton_keys": True})
    assert _checked(w, "Socket Wrench")


def test_typed_keywords_survive_a_round_trip(qapp):
    assert _make(qapp, keep_list=["hat", "Amber"]).get_value() == ["hat", "Amber"]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_search_hides_non_matching_rows(qapp):
    w = _make(qapp)
    w._search.setText("amber")
    assert w.visible_rows() == ["Amber", "Amber Ring"]


def test_filtering_does_not_rebuild_rows(qapp):
    """Filtering must be setHidden, not a teardown — 600-row tables cannot rebuild per keystroke."""
    w = _make(qapp)
    before = list(w._rows)
    w._search.setText("amber")
    w._search.setText("")
    assert [id(r) for r in w._rows] == [id(r) for r in before]


def test_filtering_preserves_check_state(qapp):
    w = _make(qapp, keep_list=["Boots of Dawn"])
    w._search.setText("amber")
    w._search.setText("")
    assert _checked(w, "Boots of Dawn")
    assert w.get_value() == ["Boots of Dawn"]


def test_category_dropdown_filters(qapp):
    w = _make(qapp)
    w._category.setCurrentIndex(w._category.findData("Jewels"))
    assert w.visible_rows() == ["Amber Ring"]


def test_kept_only_filters(qapp):
    w = _make(qapp, keep_list=["Boots of Dawn"])
    w._kept_only.setChecked(True)
    assert w.visible_rows() == ["Boots of Dawn"]


def test_summary_reports_kept_and_shown_counts(qapp):
    w = _make(qapp, keep_list=["Amber"])
    # "Amber" and "Amber Ring" — the partial match counts, because the bot keeps it.
    assert "keeping 2 out of 4 possible drops" in _plain(w._summary.text())
    w._search.setText("boots")
    assert "1 shown" in _plain(w._summary.text())


def test_no_matches_shows_a_message_instead_of_the_table(qapp):
    w = _make(qapp)
    w._search.setText("zzzz")
    assert w.visible_rows() == []
    assert w._empty.isVisibleTo(w)
    assert not w._tree.isVisibleTo(w)


# ---------------------------------------------------------------------------
# Bulk check / uncheck
# ---------------------------------------------------------------------------


def test_check_all_shown_only_affects_visible_rows(qapp):
    w = _make(qapp)
    w._search.setText("amber")
    w._btn_check_all.click()
    # Amber Ring becomes locked once "Amber" keeps it, so only Amber is written.
    assert w.get_value() == ["Amber", "Amber Ring"]
    assert not _checked(w, "Boots of Dawn")


def test_check_all_shown_skips_locked_rows(qapp):
    w = _make(qapp, keep_list=[], context={"sell_socket_wrenches": False, "sell_skeleton_keys": True})
    w._btn_check_all.click()
    # Socket Wrench is already kept by the sell bool and cannot be written as a name.
    assert "Socket Wrench" not in w.get_value()


def test_uncheck_all_shown_removes_only_visible_names(qapp):
    w = _make(qapp, keep_list=["Amber", "Boots of Dawn"])
    w._search.setText("boots")
    w._btn_uncheck_all.click()
    assert w.get_value() == ["Amber"]


def test_bulk_below_the_threshold_does_not_confirm(qapp, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: asked.append(1))
    w = _make(qapp)
    w._btn_check_all.click()
    assert not asked


def test_bulk_above_the_threshold_confirms_and_cancel_changes_nothing(qapp, monkeypatch):
    drops = [DropModel(name=f"Item {i}") for i in range(40)]
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
    w = _make(qapp, drops=drops)
    w._btn_check_all.click()
    assert w.get_value() == []


def test_bulk_above_the_threshold_applies_when_confirmed(qapp, monkeypatch):
    drops = [DropModel(name=f"Item {i}") for i in range(40)]
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)
    w = _make(qapp, drops=drops)
    w._btn_check_all.click()
    assert len(w.get_value()) == 40


# ---------------------------------------------------------------------------
# Empty state and form integration
# ---------------------------------------------------------------------------


def test_no_drops_hides_the_table_and_the_rule(qapp):
    w = _make(qapp, drops=[])
    assert not w._rows
    # isVisibleTo, not isVisible: nothing here is ever shown on screen.
    assert not w._rule.isVisibleTo(w)
    assert "no drops" in w._summary.text().lower()


def test_the_rule_is_shown_once_there_are_drops(qapp):
    w = _make(qapp)
    assert w._rule.isVisibleTo(w)
    rule = _plain(w._rule.text()).lower()
    assert "checked" in rule and "kept" in rule and "unchecked" in rule and "sold" in rule


def test_refresh_picks_up_drops_added_after_construction(qapp):
    drops = [DropModel(name="Amber")]
    w = DropKeepListWidget(lambda: list(drops))
    w.set_context(_SELL_ALL)
    w.set_value([])
    drops.append(DropModel(name="Boots of Dawn", category="Boots"))
    w.refresh()
    assert _rows(w) == ["Amber", "Boots of Dawn"]


def test_the_quicksell_form_builds_the_widget_and_round_trips_its_value(qapp):
    form = _DynamicParamForm()
    form.set_drops_getter(lambda: list(_DROPS))
    form.load_schema(PARAM_SCHEMA, {"keep_list": ["Amber"], **_SELL_ALL, "sell_pets": True})

    widget = next(w for w in form.findChildren(DropKeepListWidget))
    assert _checked(widget, "Amber")
    assert form.get_params()["keep_list"] == ["Amber"]

    _row(widget, "Boots of Dawn").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert form.get_params()["keep_list"] == ["Amber", "Boots of Dawn"]


def test_the_quicksell_form_feeds_its_sell_bools_to_the_widget(qapp):
    form = _DynamicParamForm()
    form.set_drops_getter(lambda: list(_DROPS))
    form.load_schema(PARAM_SCHEMA, {"keep_list": [], "sell_socket_wrenches": False, "sell_skeleton_keys": True})

    widget = next(w for w in form.findChildren(DropKeepListWidget))
    assert _checked(widget, "Socket Wrench") and not _enabled(widget, "Socket Wrench")
