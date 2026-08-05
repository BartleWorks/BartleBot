"""Tests for DuelConfigEditor — the reusable min/max/order/assignments editor
extracted out of DuelStrategyEditor so a DuelConfig can be edited inline
without a name, an id, or any autosave of its own.

Unlike PlayerStrategyEditor/DuelStrategyEditor, this widget has no debounce and
no ``changed(str)`` identity signal — every field write lands on the loaded
DuelConfig immediately, and persistence is entirely the embedder's job.  These
tests exercise it standalone, without a DuelStrategyEditor or CombatTab wrapping
it, to prove that split actually holds.
"""

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QPlainTextEdit

from bartlebot.bot.strategy_models import DuelConfig, PlayerAssignment, PlayerCombatStrategy
from bartlebot.gui.strategy_library.duel_strategy_editor import DuelConfigEditor


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_load_populates_fields_from_the_config(qapp):
    # combat_order must be a full permutation of 1..max_players — CombatOrderWidget
    # always displays (and round-trips) a complete order, padding partial ones.
    config = DuelConfig(min_players=2, max_players=3, combat_order=[2, 1, 3])
    editor = DuelConfigEditor()
    editor.load(config, [])

    assert editor._min_spin.value() == 2
    assert editor._max_spin.value() == 3
    assert editor._order_widget.get_value() == [2, 1, 3]


def test_editing_min_players_mutates_the_config_live_with_no_flush_needed(qapp):
    config = DuelConfig()
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._min_spin.setValue(3)

    assert config.min_players == 3


def test_editing_max_players_mutates_the_config_live(qapp):
    config = DuelConfig()
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._max_spin.setValue(2)

    assert config.max_players == 2


def test_editing_combat_order_mutates_the_config_live(qapp):
    """CombatOrderWidget.set_value() is for programmatic loading and is silent by
    design (see load()'s own suppress pattern) — a real user reorders by dragging
    chips, which goes through _swap_players() and does emit ``changed``.
    """
    # Order must start non-empty — an empty order means "simultaneous" (checkbox
    # checked), and swapping chips while simultaneous is checked is a no-op.
    config = DuelConfig(max_players=4, combat_order=[1, 2, 3, 4])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._order_widget._swap_players(1, 2)

    assert config.combat_order == [2, 1, 3, 4]


def test_clearing_the_combat_order_stores_none_not_an_empty_list(qapp):
    """Checking the "simultaneous" checkbox is the real user path to an empty order."""
    config = DuelConfig(max_players=4, combat_order=[1, 2, 3, 4])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._order_widget._checkbox.setChecked(True)

    assert config.combat_order is None


def test_changed_fires_on_every_edit(qapp):
    config = DuelConfig()
    editor = DuelConfigEditor()
    editor.load(config, [])

    seen = []
    editor.changed.connect(lambda: seen.append(True))
    editor._min_spin.setValue(4)

    assert seen == [True]


def test_loading_does_not_itself_fire_changed(qapp):
    config = DuelConfig(min_players=2)
    editor = DuelConfigEditor()
    seen = []
    editor.changed.connect(lambda: seen.append(True))

    editor.load(config, [])

    assert seen == []


def test_add_assignment_appends_to_the_config_live(qapp):
    config = DuelConfig(max_players=4)
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._on_add_assignment()

    assert len(config.player_assignments) == 1
    assert len(editor._rows) == 1


def test_add_assignment_claims_unclaimed_player_slots(qapp):
    config = DuelConfig(max_players=4, player_assignments=[PlayerAssignment(players=[1, 2], dsl="x")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._on_add_assignment()

    assert config.player_assignments[1].players == [3, 4]


def test_remove_row_removes_from_the_config_live(qapp):
    assignment = PlayerAssignment(players=[1], dsl="x")
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._on_remove_row(editor._rows[0])

    assert config.player_assignments == []


def test_clear_detaches_and_removes_rows(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    editor = DuelConfigEditor()
    editor.load(config, [])
    assert editor._rows

    editor.clear()

    assert editor._rows == []
    assert editor._config is None


def test_clear_then_edit_does_not_raise(qapp):
    """Nothing left loaded — spin changes after clear() must be safely ignored."""
    editor = DuelConfigEditor()
    editor.load(DuelConfig(), [])
    editor.clear()

    editor._min_spin.setValue(3)  # must not raise despite no config loaded


def test_set_pcs_list_propagates_to_existing_rows(qapp):
    pcs = PlayerCombatStrategy(name="Preset", dsl="any<damage>")
    assignment = PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [])  # loaded without the preset visible yet

    editor.set_pcs_list([pcs])

    assert "Preset" in editor._rows[0]._link_lbl.text()


def test_promoted_row_reports_via_presets_changed(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="any<damage>")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    seen = []
    editor.presets_changed.connect(lambda: seen.append(True))
    editor._rows[0].promoted.emit()

    assert seen == [True]


# ---------------------------------------------------------------------------
# _AutoHeightTextEdit / _wrapped_line_count
#
# Regression: the per-assignment DSL box was a fixed 56px QPlainTextEdit —
# small enough that a real DSL showed one line, maybe two, with the rest
# hidden behind an easy-to-miss inner scrollbar. It now grows to fit its
# content instead.
# ---------------------------------------------------------------------------


def test_wrapped_line_count_empty_text_is_one_line(qapp):
    from bartlebot.gui.strategy_library.duel_strategy_editor import _wrapped_line_count

    font = QPlainTextEdit().font()
    assert _wrapped_line_count("", 200, font) == 1


def test_wrapped_line_count_counts_explicit_newlines(qapp):
    from bartlebot.gui.strategy_library.duel_strategy_editor import _wrapped_line_count

    font = QPlainTextEdit().font()
    assert _wrapped_line_count("a\nb\nc", 400, font) == 3


def test_wrapped_line_count_wraps_long_text_at_a_narrow_width(qapp):
    from bartlebot.gui.strategy_library.duel_strategy_editor import _wrapped_line_count

    font = QPlainTextEdit().font()
    long_dsl = (
        'scald["Epic - Amulet"][epic] | feint mass | any<blade>[sharp]@ally(1) '
        "| any<blade & out_damage>[sharp] @ self | any<damage & aoe>"
    )
    narrow = _wrapped_line_count(long_dsl, 200, font)
    wide = _wrapped_line_count(long_dsl, 2000, font)

    assert narrow > 1
    assert wide == 1
    assert narrow > wide


def _show_and_resize(edit, width, height=20):
    """A widget's viewport is only laid out to its real size after show() runs
    at least one paint cycle — resize() alone leaves viewport().width() at an
    arbitrary default, which _sync_height() would wrap against instead of the
    requested width."""
    edit.resize(width, height)
    edit.show()
    QApplication.processEvents()


def test_auto_height_text_edit_grows_with_a_long_dsl(qapp):
    from bartlebot.gui.strategy_library.duel_strategy_editor import _AutoHeightTextEdit

    edit = _AutoHeightTextEdit()
    _show_and_resize(edit, 200)
    empty_height = edit.height()

    long_dsl = (
        'scald["Epic - Amulet"][epic] | feint mass | any<blade>[sharp]@ally(1) '
        "| any<blade & out_damage>[sharp] @ self | any<damage & aoe>"
    )
    edit.setPlainText(long_dsl)
    QApplication.processEvents()

    assert edit.height() > empty_height


def test_auto_height_text_edit_respects_the_minimum(qapp):
    from bartlebot.gui.strategy_library.duel_strategy_editor import _DSL_EDIT_MIN_HEIGHT, _AutoHeightTextEdit

    edit = _AutoHeightTextEdit()
    _show_and_resize(edit, 200)
    edit.setPlainText("x")
    QApplication.processEvents()

    assert edit.height() >= _DSL_EDIT_MIN_HEIGHT


def test_auto_height_text_edit_shrinks_back_down_when_widened(qapp):
    """A long single-line DSL needs fewer wrapped lines once the box is wider —
    covers the resizeEvent path (e.g. the user widens the sidebar).
    """
    from bartlebot.gui.strategy_library.duel_strategy_editor import _AutoHeightTextEdit

    edit = _AutoHeightTextEdit()
    _show_and_resize(edit, 200)
    long_dsl = (
        'scald["Epic - Amulet"][epic] | feint mass | any<blade>[sharp]@ally(1) '
        "| any<blade & out_damage>[sharp] @ self | any<damage & aoe>"
    )
    edit.setPlainText(long_dsl)
    QApplication.processEvents()
    narrow_height = edit.height()

    _show_and_resize(edit, 900)
    wide_height = edit.height()

    assert wide_height < narrow_height


# ---------------------------------------------------------------------------
# Edit lock — the Combat tab locks this editor while a bot is running
# ---------------------------------------------------------------------------


def test_set_editable_false_disables_every_control(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor.set_editable(False)

    assert not editor._min_spin.isEnabled()
    assert not editor._max_spin.isEnabled()
    assert not editor._add_btn.isEnabled()
    assert editor._rows[0]._dsl_edit.isReadOnly()
    assert not editor._rows[0]._player_widget.isEnabled()
    assert not editor._rows[0]._remove_btn.isEnabled()
    assert not editor._rows[0]._menu_btn.isEnabled()


def test_set_editable_true_reenables_an_inline_row(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor.set_editable(False)
    editor.set_editable(True)

    assert not editor._rows[0]._dsl_edit.isReadOnly()
    assert editor._add_btn.isEnabled()


def test_a_linked_row_stays_read_only_after_being_reenabled(qapp):
    """Linked rows are read-only for their own reason; unlocking must not override it."""
    pcs = PlayerCombatStrategy(name="Preset", dsl="blade")
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)])
    editor = DuelConfigEditor()
    editor.load(config, [pcs])

    editor.set_editable(False)
    editor.set_editable(True)

    assert editor._rows[0]._dsl_edit.isReadOnly()


def test_rows_added_while_locked_are_locked_too(qapp):
    config = DuelConfig()
    editor = DuelConfigEditor()
    editor.load(config, [])
    editor.set_editable(False)

    # Bypasses the disabled button the way a stray signal would.
    editor._on_add_assignment()

    assert editor._rows[0]._dsl_edit.isReadOnly()


# ---------------------------------------------------------------------------
# Disabled controls explain themselves
# ---------------------------------------------------------------------------


def test_add_button_tooltip_names_the_reason_when_slots_are_full(qapp):
    config = DuelConfig(max_players=2)
    editor = DuelConfigEditor()
    editor.load(config, [])
    editor._on_add_assignment()
    editor._on_add_assignment()

    assert not editor._add_btn.isEnabled()
    assert "already has an assignment" in editor._add_btn.toolTip()


def test_add_button_tooltip_names_the_lock_when_a_bot_is_running(qapp):
    editor = DuelConfigEditor()
    editor.load(DuelConfig(), [])

    editor.set_editable(False)

    assert "Locked while the bot is running" in editor._add_btn.toolTip()


def test_add_button_tooltip_describes_the_action_when_enabled(qapp):
    editor = DuelConfigEditor()
    editor.load(DuelConfig(), [])

    assert editor._add_btn.isEnabled()
    assert "Add a player assignment" in editor._add_btn.toolTip()
