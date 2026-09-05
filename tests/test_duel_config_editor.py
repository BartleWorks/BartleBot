"""Tests for DuelConfigEditor — the reusable min/max/order/assignments editor
extracted out of DuelStrategyEditor so a DuelConfig can be edited inline
without a name, an id, or any autosave of its own.

Unlike PlayerStrategyEditor/DuelStrategyEditor, this widget has no debounce and
no ``changed(str)`` identity signal — every field write lands on the loaded
DuelConfig immediately, and persistence is entirely the embedder's job.  These
tests exercise it standalone, without a DuelStrategyEditor or CombatTab wrapping
it, to prove that split actually holds.
"""

import pytest
from PyQt6.QtWidgets import QApplication, QPlainTextEdit

from bartlebot.bot.strategy_models import DuelConfig, PlayerAssignment, PlayerCombatStrategy
from bartlebot.gui.strategy_library.duel_strategy_editor import DuelConfigEditor, move_item


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


# ---------------------------------------------------------------------------
# Reordering assignment rows (#95)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "dst", "expected"),
    [
        (2, 0, ["c", "a", "b"]),  # drag the last row to the top
        (0, 3, ["b", "c", "a"]),  # drag the first row past the end
        (0, 2, ["b", "a", "c"]),  # downward: dst counts positions before the move
        (2, 1, ["a", "c", "b"]),  # upward: no compensation needed
        (0, 0, ["a", "b", "c"]),  # dropped on its own top edge
        (0, 1, ["a", "b", "c"]),  # dropped on its own bottom edge
        (9, 0, ["a", "b", "c"]),  # out of range is a no-op, not a raise
    ],
)
def test_move_item(src, dst, expected):
    items = ["a", "b", "c"]
    assert move_item(items, src, dst) == expected


def test_move_item_does_not_mutate_its_input():
    items = ["a", "b", "c"]
    move_item(items, 0, 3)
    assert items == ["a", "b", "c"]


def _three_row_editor():
    config = DuelConfig(
        max_players=3,
        player_assignments=[
            PlayerAssignment(players=[1], dsl="one"),
            PlayerAssignment(players=[2], dsl="two"),
            PlayerAssignment(players=[3], dsl="three"),
        ],
    )
    editor = DuelConfigEditor()
    editor.load(config, [])
    return config, editor


def test_move_assignment_reorders_the_config_live_with_no_flush(qapp):
    config, editor = _three_row_editor()

    editor.move_assignment(2, 0)

    assert [a.dsl for a in config.player_assignments] == ["three", "one", "two"]


def test_move_assignment_keeps_the_rows_in_step_with_the_config(qapp):
    """The widget list and the model list must not be able to drift apart."""
    config, editor = _three_row_editor()

    editor.move_assignment(0, 3)

    assert [row.assignment() for row in editor._rows] == config.player_assignments


def test_move_assignment_emits_changed(qapp):
    """``changed`` is what both embedders debounce a save off."""
    _config, editor = _three_row_editor()
    fired = []
    editor.changed.connect(lambda: fired.append(1))

    editor.move_assignment(2, 0)

    assert fired


def test_move_assignment_that_changes_nothing_stays_quiet(qapp):
    """Dropping a row back on its own edge is not an edit worth saving."""
    _config, editor = _three_row_editor()
    fired = []
    editor.changed.connect(lambda: fired.append(1))

    editor.move_assignment(1, 1)

    assert not fired


def test_move_assignment_puts_the_widget_where_the_model_says(qapp):
    config, editor = _three_row_editor()

    editor.move_assignment(2, 0)

    layout = editor._assignments_layout
    order = [layout.itemAt(i).widget() for i in range(len(config.player_assignments))]
    assert order == editor._rows
    # The stretch that _add_row inserts before must still be last.
    assert layout.itemAt(layout.count() - 1).widget() is None


def test_dropping_below_a_row_lands_after_it(qapp):
    """A drop on the lower half of row 0 puts the dragged row second, not first."""
    config, editor = _three_row_editor()
    rows = list(editor._rows)

    editor._on_reorder_row(rows[2], rows[0], False)

    assert [a.dsl for a in config.player_assignments] == ["one", "three", "two"]


def test_dropping_above_a_row_lands_before_it(qapp):
    config, editor = _three_row_editor()
    rows = list(editor._rows)

    editor._on_reorder_row(rows[2], rows[0], True)

    assert [a.dsl for a in config.player_assignments] == ["three", "one", "two"]


def test_a_row_refuses_a_drop_from_itself(qapp):
    _config, editor = _three_row_editor()
    row = editor._rows[0]

    assert not row.accepts_drop_from(row)


def test_a_row_refuses_drops_while_the_bot_is_running(qapp):
    """Reordering is an edit, and edits are locked mid-run like every other one."""
    _config, editor = _three_row_editor()
    editor.set_editable(False)

    assert not editor._rows[1].accepts_drop_from(editor._rows[0])


def test_a_row_accepts_a_drop_from_a_sibling(qapp):
    _config, editor = _three_row_editor()

    assert editor._rows[1].accepts_drop_from(editor._rows[0])


def test_the_dsl_box_does_not_swallow_row_drops(qapp):
    """The DSL editor accepts drops by default, which would shrink the target."""
    _config, editor = _three_row_editor()

    assert not editor._rows[0]._dsl_edit.acceptDrops()


class TestPlayerRangeCannotInvert:
    """Min players used to be settable above Max players, and vice versa.

    An inverted range describes a duel nobody can run: combat_warnings insists on
    ``min_players`` connected clients, while the editor only offers assignments and
    combat-order slots up to ``max_players``.  The summary line rendered it as
    "4-1 players", which is the visible half of the problem.
    """

    def test_raising_min_past_max_drags_max_up(self, qapp):
        config = DuelConfig(min_players=1, max_players=2)
        editor = DuelConfigEditor()
        editor.load(config, [])

        editor._min_spin.setValue(4)

        assert editor._max_spin.value() == 4
        assert (config.min_players, config.max_players) == (4, 4)

    def test_lowering_max_below_min_drags_min_down(self, qapp):
        config = DuelConfig(min_players=3, max_players=4)
        editor = DuelConfigEditor()
        editor.load(config, [])

        editor._max_spin.setValue(1)

        assert editor._min_spin.value() == 1
        assert (config.min_players, config.max_players) == (1, 1)

    def test_a_valid_range_is_left_alone(self, qapp):
        config = DuelConfig(min_players=1, max_players=4)
        editor = DuelConfigEditor()
        editor.load(config, [])

        editor._min_spin.setValue(2)

        assert (config.min_players, config.max_players) == (2, 4)

    def test_loading_a_narrow_config_after_a_wide_one_keeps_its_values(self, qapp):
        """The reason the coupling pushes values instead of narrowing ranges.

        Narrowing the other spin box's range would leave it narrow, and the next
        load() would silently clamp whatever config was opened into it.
        """
        editor = DuelConfigEditor()
        editor.load(DuelConfig(min_players=1, max_players=1), [])
        editor.load(DuelConfig(min_players=3, max_players=4), [])

        assert (editor._min_spin.value(), editor._max_spin.value()) == (3, 4)
