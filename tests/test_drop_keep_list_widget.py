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
from bartlebot.gui.widgets.option_widgets import connect_widget, make_option_widget, widget_value

_DROPS = [
    DropModel(name="Amber", category="Jewels"),
    DropModel(name="Amber Ring", category="Rings"),
    DropModel(name="Boots of Dawn", category="Boots"),
    DropModel(name="Socket Wrench"),  # uncategorized — unknown is not unsellable
    DropModel(name="Mandrake", category="Reagents"),  # never quicksold
]
# The resolved sell_* answers the host pushes as context. Supplied in full, so
# these tests do not depend on the settings of the machine they run on.
_SELL_ALL = {"sell_pets": True, "sell_socket_wrenches": True, "sell_skeleton_keys": True}


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


def _keyword_chips(widget) -> list[str]:
    """The keep list as the user reads it: one removable chip per keyword."""
    layout = widget._keyword_layout
    return [layout.itemAt(i).widget().value for i in range(layout.count())]


def _keyword_chip(widget, word):
    layout = widget._keyword_layout
    return next(layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget().value == word)


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
    assert row_visible("Amber", "Jewels", False, "", None, False)


def test_query_matches_case_insensitive_substring():
    assert row_visible("Amber Ring", "Rings", False, "amber", None, False)
    assert not row_visible("Boots of Dawn", "Boots", False, "amber", None, False)


def test_category_filter_selects_one_category():
    assert row_visible("Amber", "Jewels", False, "", "Jewels", False)
    assert not row_visible("Amber", "Jewels", False, "", "Rings", False)


def test_empty_category_filter_selects_uncategorized():
    assert row_visible("Socket Wrench", "", False, "", "", False)
    assert not row_visible("Amber", "Jewels", False, "", "", False)


def test_kept_only_hides_unkept():
    assert row_visible("Amber", "Jewels", True, "", None, True)
    assert not row_visible("Amber", "Jewels", False, "", None, True)


def test_filters_compose_rather_than_override():
    # Matches the query and the category, but is not kept.
    assert not row_visible("Amber", "Jewels", False, "amb", "Jewels", True)
    assert row_visible("Amber", "Jewels", True, "amb", "Jewels", True)


def test_never_quicksold_categories_are_hidden_by_default():
    assert not row_visible("Mandrake", "Reagents", False, "", None, False)
    assert not row_visible("Ghulture Feather", "Spellements", False, "", None, False)


def test_selecting_a_never_sold_category_still_shows_it():
    """Hidden, not unreachable — the dropdown is the escape hatch."""
    assert row_visible("Mandrake", "Reagents", False, "", "Reagents", False)


def test_a_never_sold_row_stays_hidden_under_another_category_filter():
    assert not row_visible("Mandrake", "Reagents", False, "", "Jewels", False)


def test_an_unknown_category_is_never_hidden_as_unsellable():
    """Most of the library predates categories; unknown is not the same as unsellable."""
    assert row_visible("Socket Wrench", "", False, "", None, False)


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
    assert _row(w, "Amber").text(2) == "Jewels"
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
    w._category.setCurrentIndex(w._category.findData("Rings"))
    assert w.visible_rows() == ["Amber Ring"]


def test_kept_only_filters(qapp):
    w = _make(qapp, keep_list=["Boots of Dawn"])
    w._kept_only.setChecked(True)
    assert w.visible_rows() == ["Boots of Dawn"]


def test_summary_reports_kept_and_shown_counts(qapp):
    w = _make(qapp, keep_list=["Amber"])
    # "Amber" and "Amber Ring" — the partial match counts, because the bot keeps it.
    # The denominator is the 4 sellable drops; Mandrake is counted separately.
    text = _plain(w._summary.text())
    assert "keeping 2 out of 4 sellable drops" in text
    assert "1 never quicksold" in text
    w._search.setText("boots")
    assert "1 shown" in _plain(w._summary.text())


def test_summary_omits_the_never_sold_count_when_there_are_none(qapp):
    w = _make(qapp, drops=[DropModel(name="Amber", category="Jewels")])
    assert "never quicksold" not in _plain(w._summary.text())


# ---------------------------------------------------------------------------
# Never-quicksellable categories
# ---------------------------------------------------------------------------


def test_never_sold_drops_are_hidden_by_default(qapp):
    w = _make(qapp)
    assert "Mandrake" not in w.visible_rows()
    # The row still exists — hidden, not dropped.
    assert "Mandrake" in _rows(w)


def test_never_sold_drops_appear_when_their_category_is_selected(qapp):
    w = _make(qapp)
    w._category.setCurrentIndex(w._category.findData("Reagents"))
    assert w.visible_rows() == ["Mandrake"]


def test_never_sold_rows_are_inert_with_a_reason(qapp):
    w = _make(qapp)
    w._category.setCurrentIndex(w._category.findData("Reagents"))
    assert _checked(w, "Mandrake")
    assert not _enabled(w, "Mandrake")
    assert "cannot be quicksold" in _row(w, "Mandrake").toolTip(_COL_ITEM).lower()


def test_never_sold_drops_stay_out_of_bulk_changes(qapp):
    w = _make(qapp)
    w._category.setCurrentIndex(w._category.findData("Reagents"))
    w._btn_check_all.click()
    assert w.get_value() == []


def test_never_sold_drops_are_excluded_from_the_kept_count(qapp):
    """A reagent survives quicksell, but not because the keep list said so."""
    w = _make(qapp, keep_list=["Mandrake"])
    assert "keeping 0 out of 4 sellable drops" in _plain(w._summary.text())


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


def test_the_option_layer_builds_the_picker_and_round_trips_its_value(qapp):
    """The keep list is a bot option, so the picker is reached through the one
    option-type -> widget mapping rather than through the properties form."""
    widget = make_option_widget("drop_keep_list", ["Amber"], get_drops=lambda: list(_DROPS))
    assert isinstance(widget, DropKeepListWidget)
    widget.set_context(_SELL_ALL)
    assert _checked(widget, "Amber")
    assert widget_value(widget) == ["Amber"]

    _row(widget, "Boots of Dawn").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert widget_value(widget) == ["Amber", "Boots of Dawn"]


def test_ticking_a_drop_reports_the_whole_new_list(qapp):
    """A tick is an edit the host stores, so it has to arrive as a value and not as
    a signal the host then has to go and read."""
    seen = []
    widget = make_option_widget("drop_keep_list", [], get_drops=lambda: list(_DROPS))
    widget.set_context(_SELL_ALL)
    connect_widget(widget, seen.append)

    _row(widget, "Amber").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert seen == [["Amber"]]


def test_a_typed_keyword_is_reported_once_the_field_is_left_and_not_per_keystroke(qapp):
    """Storing a value is a settings-file write, and one per keystroke is one write
    per keystroke -- the same reason connect_widget uses editingFinished elsewhere.
    A keyword is committed whole, and the table repaints when it is."""
    seen = []
    widget = make_option_widget("drop_keep_list", [], get_drops=lambda: list(_DROPS))
    widget.set_context(_SELL_ALL)
    connect_widget(widget, seen.append)

    widget._edit.setText("Amber")
    assert seen == []
    assert not _checked(widget, "Amber")

    widget._edit.editingFinished.emit()
    assert seen == [["Amber"]]
    assert _checked(widget, "Amber")


def test_a_committed_keyword_becomes_a_chip_and_leaves_the_field_empty(qapp):
    """The value is shown as chips because a 32-keyword CSV string at a 400px sidebar
    shows only its own middle."""
    widget = make_option_widget("drop_keep_list", [], get_drops=lambda: list(_DROPS))

    widget._edit.setText("Amber, Onyx")
    widget._edit.editingFinished.emit()

    assert _keyword_chips(widget) == ["Amber", "Onyx"]
    assert widget._edit.text() == ""
    assert widget.get_value() == ["Amber", "Onyx"]


def test_a_keyword_already_kept_is_not_added_twice(qapp):
    widget = make_option_widget("drop_keep_list", ["Amber"], get_drops=lambda: list(_DROPS))
    seen = []
    connect_widget(widget, seen.append)

    widget._edit.setText("amber")
    widget._edit.editingFinished.emit()

    assert widget.get_value() == ["Amber"]
    assert seen == []


def test_a_chip_removes_its_own_keyword(qapp):
    """Removing one entry from the CSV line meant selecting text between two commas."""
    widget = make_option_widget("drop_keep_list", ["Amber", "Onyx"], get_drops=lambda: list(_DROPS))
    seen = []
    connect_widget(widget, seen.append)

    _keyword_chip(widget, "Amber").click()

    assert _keyword_chips(widget) == ["Onyx"]
    assert seen == [["Onyx"]]


def test_removing_a_chip_never_leaves_it_parentless(qapp):
    """A parentless widget is a top-level window, and deleteLater is a turn away.

    The removed chip spent that interval as a real window, which Windows drew and
    then took away — a small box flashing beside the sidebar on every ✕.
    """
    widget = make_option_widget("drop_keep_list", ["Amber", "Onyx"], get_drops=lambda: list(_DROPS))
    chip = _keyword_chip(widget, "Amber")

    chip.click()

    assert chip.parent() is not None
    assert not chip.isVisible()


def test_the_default_category_entry_counts_what_it_shows(qapp):
    w = _make(qapp)
    # 5 drops, 1 of them a reagent the default view hides.
    assert w._category.itemText(0) == "All sellable (4)"
    assert len(w.visible_rows()) == 4


def test_the_default_category_entry_says_all_categories_when_nothing_is_hidden(qapp):
    w = _make(qapp, drops=[DropModel(name="Amber", category="Jewels")])
    assert w._category.itemText(0) == "All categories (1)"


# ---------------------------------------------------------------------------
# Wiki names vs in-game names
#
# The wiki disambiguates variants the game shows identically, so ticking a row has
# to store the in-game name — the only text the node ever reads. Storing the wiki
# name instead produced a keep-list entry matching nothing, and the item sold.
# ---------------------------------------------------------------------------

_PINS = [
    DropModel(
        name=f"Balance Crushing Pin (140, {school})",
        category="Pins",
        match_name="Balance Crushing Pin (140)",
    )
    for school in ("Fire", "Ice", "Storm", "Myth", "Life", "Death")
] + [DropModel(name="Amber", category="Jewels")]


def test_ticking_stores_the_in_game_name_not_the_wiki_name(qapp):
    w = _make(qapp, drops=_PINS)
    _row(w, "Balance Crushing Pin (140, Fire)").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert w.get_value() == ["Balance Crushing Pin (140)"]


def test_a_ticked_row_stays_tickable(qapp):
    # The row must not lock itself: the stored keyword equals the item's in-game
    # name, so the checkbox alone is enough to undo it.
    w = _make(qapp, drops=_PINS)
    _row(w, "Balance Crushing Pin (140, Fire)").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert _enabled(w, "Balance Crushing Pin (140, Fire)")


def test_unticking_removes_the_entry_again(qapp):
    w = _make(qapp, drops=_PINS)
    row = _row(w, "Balance Crushing Pin (140, Fire)")
    row.setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    row.setCheckState(_COL_KEEP, Qt.CheckState.Unchecked)
    assert w.get_value() == []


def test_drops_sharing_an_in_game_name_tick_together(qapp):
    # The game shows all six as one string, so there is no keep list that separates
    # them. The picker has to show that rather than imply per-school control.
    w = _make(qapp, drops=_PINS)
    _row(w, "Balance Crushing Pin (140, Fire)").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert all(_checked(w, f"Balance Crushing Pin (140, {s})") for s in ("Fire", "Ice", "Storm", "Myth"))
    assert not _checked(w, "Amber")


def test_a_shared_name_says_so_in_its_tooltip(qapp):
    w = _make(qapp, drops=_PINS)
    tooltip = _row(w, "Balance Crushing Pin (140, Fire)").toolTip(_COL_ITEM)
    assert "Balance Crushing Pin (140)" in tooltip
    assert "6 drops share that name in-game, so this applies to all 6." in tooltip


def test_an_undecorated_drop_has_no_extra_tooltip_lines(qapp):
    w = _make(qapp, drops=_PINS)
    assert _row(w, "Amber").toolTip(_COL_ITEM) == "Keep this item when quickselling."


def test_bulk_counts_in_game_names_not_rows(qapp, monkeypatch):
    # Six rows, one keep-list entry: the confirmation must not claim six.
    seen = {}
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: seen.update(title=self.windowTitle()) or QMessageBox.StandardButton.Ok
    )
    w = _make(qapp, drops=_PINS)
    w._btn_check_all.click()
    assert w.get_value() == ["Balance Crushing Pin (140)", "Amber"]
    assert not seen  # 2 entries is under the confirmation threshold


def test_searching_the_in_game_name_finds_the_wiki_named_rows(qapp):
    w = _make(qapp, drops=_PINS)
    w._search.setText("Balance Crushing Pin (140)")
    assert len(w.visible_rows()) == 6


def test_searching_the_displayed_name_still_works(qapp):
    w = _make(qapp, drops=_PINS)
    w._search.setText("(140, Ice)")
    assert w.visible_rows() == ["Balance Crushing Pin (140, Ice)"]


# ---------------------------------------------------------------------------
# Item names containing a comma
# ---------------------------------------------------------------------------

_COMMA_DROPS = [
    DropModel(name="No Pain, No Gain Onyx", category="Jewels"),
    DropModel(name="Amber", category="Jewels"),
]


def test_a_comma_in_an_item_name_survives_ticking(qapp):
    w = _make(qapp, drops=_COMMA_DROPS)
    _row(w, "No Pain, No Gain Onyx").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert w.get_value() == ["No Pain, No Gain Onyx"]


def test_a_comma_named_row_does_not_lock_itself(qapp):
    w = _make(qapp, drops=_COMMA_DROPS)
    _row(w, "No Pain, No Gain Onyx").setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    assert _enabled(w, "No Pain, No Gain Onyx")
    assert not _checked(w, "Amber")


def test_a_comma_named_entry_can_be_unticked(qapp):
    w = _make(qapp, drops=_COMMA_DROPS)
    row = _row(w, "No Pain, No Gain Onyx")
    row.setCheckState(_COL_KEEP, Qt.CheckState.Checked)
    row.setCheckState(_COL_KEEP, Qt.CheckState.Unchecked)
    assert w.get_value() == []


def test_a_comma_named_entry_round_trips_through_the_option_layer(qapp):
    widget = make_option_widget("drop_keep_list", ["No Pain, No Gain Onyx"], get_drops=lambda: list(_COMMA_DROPS))
    assert widget_value(widget) == ["No Pain, No Gain Onyx"]


# ---------------------------------------------------------------------------
# Categories a sell_* bool spares wholesale
#
# sell_pets deselects the Pet tab rather than matching a name, so no keep-list
# entry describes it. Without the picker knowing that, a pet renders as "will be
# sold" while the config plainly says otherwise.
# ---------------------------------------------------------------------------

_PETS = [
    DropModel(name="Fierce Hound", category="Pets"),
    DropModel(name="Enchanted Owl", category="Pets"),
    DropModel(name="Amber Ring", category="Rings"),
    DropModel(name="Mandrake", category="Reagents"),
]
_KEEP_PETS = {**_SELL_ALL, "sell_pets": False}


def test_pets_are_unchecked_while_the_node_sells_them(qapp):
    # And locked: the quicksell window shows a pet's nickname, so ticking the row
    # would write a keyword that never matches anything.
    w = _make(qapp, drops=_PETS, context={**_SELL_ALL, "sell_pets": True})
    assert not _checked(w, "Fierce Hound")
    assert not _enabled(w, "Fierce Hound")


def test_pets_are_checked_when_the_node_is_not_selling_them(qapp):
    w = _make(qapp, drops=_PETS, context=_KEEP_PETS)
    assert _checked(w, "Fierce Hound")
    assert _checked(w, "Enchanted Owl")


def test_a_spared_pet_is_locked_and_says_where_to_undo_it(qapp):
    w = _make(qapp, drops=_PETS, context=_KEEP_PETS)
    assert not _enabled(w, "Fierce Hound")
    assert _row(w, "Fierce Hound").toolTip(_COL_ITEM) == "Kept because Sell Pets is off, not by the keep list."


def test_sparing_pets_leaves_other_categories_alone(qapp):
    w = _make(qapp, drops=_PETS, context=_KEEP_PETS)
    assert not _checked(w, "Amber Ring")
    assert _enabled(w, "Amber Ring")


def test_spared_pets_stay_visible_and_counted(qapp):
    # Unlike a never-quicksold category, these are sellable — the config just is
    # not selling them — so hiding them or dropping them from the denominator
    # would misreport what the node does.
    w = _make(qapp, drops=_PETS, context=_KEEP_PETS)
    assert "Fierce Hound" in w.visible_rows()
    assert "Currently keeping 2 out of 3 sellable drops" in _plain(w._summary.text())


def test_spared_pets_stay_out_of_bulk_changes(qapp):
    w = _make(qapp, drops=_PETS, context=_KEEP_PETS)
    w._btn_uncheck_all.click()
    assert _checked(w, "Fierce Hound")
    assert w.get_value() == []


def test_a_spared_pet_shows_under_the_kept_only_filter(qapp):
    w = _make(qapp, drops=_PETS, context=_KEEP_PETS)
    w._kept_only.setChecked(True)
    assert w.visible_rows() == ["Fierce Hound", "Enchanted Owl"]


def test_flipping_the_pets_bool_updates_the_rows(qapp):
    w = _make(qapp, drops=_PETS, context={**_SELL_ALL, "sell_pets": True})
    assert not _checked(w, "Fierce Hound")
    w.set_context(_KEEP_PETS)
    assert _checked(w, "Fierce Hound")
    w.set_context({**_SELL_ALL, "sell_pets": True})
    assert not _checked(w, "Fierce Hound")
    assert not _enabled(w, "Fierce Hound")


# ---------------------------------------------------------------------------
# Refresh economy
#
# The form pushes context on every edit, including edits this widget caused, so
# the cheap paths have to stay cheap on a 600-row table.
# ---------------------------------------------------------------------------


def _count_refreshes(w, monkeypatch):
    calls = []
    real = w.refresh
    monkeypatch.setattr(w, "refresh", lambda: calls.append(1) or real())
    return calls


def test_an_unchanged_context_does_not_refresh(qapp, monkeypatch):
    w = _make(qapp)
    calls = _count_refreshes(w, monkeypatch)
    w.set_context(dict(_SELL_ALL))
    assert calls == []


def test_the_widgets_own_param_does_not_count_as_a_context_change(qapp, monkeypatch):
    # get_params() includes keep_list, so comparing it would defeat the guard on
    # every keystroke — which is exactly when the guard has to hold.
    w = _make(qapp, keep_list=["Amber"])
    calls = _count_refreshes(w, monkeypatch)
    w.set_context({**_SELL_ALL, "keep_list": ["something", "different"]})
    assert calls == []


def test_a_real_context_change_still_refreshes(qapp, monkeypatch):
    w = _make(qapp)
    calls = _count_refreshes(w, monkeypatch)
    w.set_context({**_SELL_ALL, "sell_socket_wrenches": False})
    assert len(calls) == 1
    assert _checked(w, "Socket Wrench")


def test_a_theme_change_repaints_without_rebuilding_state(qapp, monkeypatch):
    from PyQt6.QtCore import QEvent

    w = _make(qapp, keep_list=["Amber"])
    before = _plain(w._summary.text())
    calls = _count_refreshes(w, monkeypatch)
    w.changeEvent(QEvent(QEvent.Type.StyleChange))
    assert calls == []
    assert _plain(w._summary.text()) == before


def test_a_theme_change_leaves_the_no_drops_message_alone(qapp, monkeypatch):
    from PyQt6.QtCore import QEvent

    w = _make(qapp, drops=[])
    w.changeEvent(QEvent(QEvent.Type.StyleChange))
    assert "no drops listed" in w._summary.text()


# ---------------------------------------------------------------------------
# Pets the keep list cannot reach
#
# The drop table names a pet by its body type; the quicksell row shows the
# nickname its owner gave it. Nothing in the keep list can bridge that, so the
# picker has to say so rather than let a ticked pet read as safe.
# ---------------------------------------------------------------------------


def test_a_sold_pet_says_why_ticking_it_would_not_help(qapp):
    w = _make(qapp, drops=_PETS, context={**_SELL_ALL, "sell_pets": True})
    tip = _row(w, "Fierce Hound").toolTip(_COL_ITEM)
    assert "nickname" in tip and "Sell Pets" in tip


def test_a_pet_named_in_the_keep_list_is_still_shown_as_sold(qapp):
    # The trap this exists for: the keyword matches the name on screen here, and
    # nothing at all in the game's window.
    w = _make(qapp, drops=_PETS, keep_list=["Fierce Hound"], context={**_SELL_ALL, "sell_pets": True})
    assert not _checked(w, "Fierce Hound")
    assert "'Fierce Hound' is in the keep list" in _row(w, "Fierce Hound").toolTip(_COL_ITEM)


def test_the_warning_line_counts_the_pets_it_cannot_keep(qapp):
    w = _make(qapp, drops=_PETS, context={**_SELL_ALL, "sell_pets": True})
    assert w._warning.isVisibleTo(w)
    text = _plain(w._warning.text())
    assert text.startswith("2 Pets:")
    assert "nickname" in text


def test_no_warning_when_the_pets_bool_already_keeps_them(qapp):
    w = _make(qapp, drops=_PETS, context=_KEEP_PETS)
    assert not w._warning.isVisibleTo(w)


def test_no_warning_when_the_bot_drops_no_pets(qapp):
    w = _make(qapp, context={**_SELL_ALL, "sell_pets": True})
    assert not w._warning.isVisibleTo(w)


def test_pets_the_keep_list_cannot_keep_stay_out_of_bulk_changes(qapp):
    w = _make(qapp, drops=_PETS, context={**_SELL_ALL, "sell_pets": True})
    w._btn_check_all.click()
    assert w.get_value() == ["Amber Ring"]
