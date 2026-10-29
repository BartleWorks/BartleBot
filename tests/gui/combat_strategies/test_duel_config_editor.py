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
from bartleby.shared.strategy_models import DuelConfig, PlayerAssignment, PlayerCombatStrategy

from bartlebot.gui.combat_strategies.assignment_card import (
    CHANGE_VERB,
    CUSTOM_DATA,
    DEFAULT_DATA,
    MISSING_TITLE,
    REMOVE_VERB,
)
from bartlebot.gui.combat_strategies.duel_editor import DuelConfigEditor, move_item
from tests.drag_helpers import drag_card, menu_move, row_height, show
from tests.picker_helpers import pick


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
    assert len(editor.cards) == 1


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

    editor._on_remove_card(editor.cards[0])

    assert config.player_assignments == []


def test_clear_detaches_and_removes_rows(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    editor = DuelConfigEditor()
    editor.load(config, [])
    assert editor.cards

    editor.clear()

    assert editor.cards == []
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

    assert editor.cards[0].title_text() == "Preset"


def test_a_row_asking_to_be_promoted_reaches_the_dialog(qapp):
    """The row emits itself; naming it and making the entry is the dialog's job."""
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="any<damage>")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    seen = []
    editor.promote_requested.connect(seen.append)
    editor.cards[0]._promote_btn.click()

    assert seen == [editor.cards[0]]


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
    assert editor.add_card.isHidden()
    card = editor.cards[0]
    assert card._dsl_edit.isReadOnly()
    assert not card.pills.pills[0].is_editable
    assert not card.action_button(REMOVE_VERB).isEnabled()
    assert not card.action_button(CHANGE_VERB).isEnabled()


def test_set_editable_true_reenables_an_inline_row(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor.set_editable(False)
    editor.set_editable(True)

    assert not editor.cards[0]._dsl_edit.isReadOnly()
    assert not editor.add_card.isHidden()


def test_a_linked_row_offers_no_dsl_box_after_being_reenabled(qapp):
    """A named answer builds no DSL box; unlocking the editor must not conjure one."""
    pcs = PlayerCombatStrategy(name="Preset", dsl="blade")
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)])
    editor = DuelConfigEditor()
    editor.load(config, [pcs])

    editor.set_editable(False)
    editor.set_editable(True)

    card = editor.cards[0]
    assert not card.is_custom()
    assert card._dsl_edit.isHidden()


def test_rows_added_while_locked_are_locked_too(qapp):
    config = DuelConfig()
    editor = DuelConfigEditor()
    editor.load(config, [])
    editor.set_editable(False)

    # Bypasses the disabled button the way a stray signal would.
    editor._on_add_assignment()

    assert editor.cards[0]._dsl_edit.isReadOnly()


# ---------------------------------------------------------------------------
# Disabled controls explain themselves
# ---------------------------------------------------------------------------


def test_the_add_card_goes_away_and_says_why_when_slots_are_full(qapp):
    config = DuelConfig(max_players=2)
    editor = DuelConfigEditor()
    editor.load(config, [])
    editor._on_add_assignment()
    editor._on_add_assignment()

    assert editor.add_card.isHidden()
    assert "already has a strategy" in editor.add_card.toolTip()


def test_the_add_card_goes_away_while_a_bot_is_running(qapp):
    editor = DuelConfigEditor()
    editor.load(DuelConfig(), [])

    editor.set_editable(False)

    assert editor.add_card.isHidden()


def test_the_add_card_describes_the_action_when_it_is_offered(qapp):
    editor = DuelConfigEditor()
    editor.load(DuelConfig(), [])

    assert not editor.add_card.isHidden()
    assert "own combat strategy" in editor.add_card.toolTip()


# ---------------------------------------------------------------------------
# Reordering assignment rows
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

    assert [row.assignment() for row in editor.cards] == config.player_assignments


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

    layout = editor._cards_layout
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order == editor.cards
    assert len(order) == len(config.player_assignments)


def _shown_editor(qapp):
    config, editor = _three_row_editor()
    show(qapp, editor)
    return config, editor


def test_dragging_a_row_down_past_one_reorders_the_config(qapp):
    config, editor = _shown_editor(qapp)
    seen: list[bool] = []
    editor.changed.connect(lambda: seen.append(True))

    drag_card(qapp, editor.cards[0], row_height(editor.cards, editor._cards_layout.spacing()))

    assert [a.dsl for a in config.player_assignments] == ["two", "one", "three"]
    assert seen == [True]


def test_move_down_in_a_rows_menu_reorders_the_config(qapp):
    config, editor = _three_row_editor()

    assert menu_move(editor.cards[0], "Move down")

    assert [a.dsl for a in config.player_assignments] == ["two", "one", "three"]


def test_dragging_a_row_to_the_top_puts_it_first(qapp):
    config, editor = _shown_editor(qapp)

    drag_card(qapp, editor.cards[2], -2 * row_height(editor.cards, editor._cards_layout.spacing()) - 10)

    assert [a.dsl for a in config.player_assignments] == ["three", "one", "two"]
    layout = editor._cards_layout
    assert [layout.itemAt(i).widget() for i in range(layout.count())] == editor.cards


def test_rows_cannot_be_dragged_while_the_bot_is_running(qapp):
    """Reordering is an edit, and edits are locked mid-run like every other one."""
    config, editor = _shown_editor(qapp)
    editor.set_editable(False)

    drag_card(qapp, editor.cards[0], row_height(editor.cards, editor._cards_layout.spacing()))

    assert [a.dsl for a in config.player_assignments] == ["one", "two", "three"]


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


# ---------------------------------------------------------------------------
# Derived school-requirement chips (phase 1)
# ---------------------------------------------------------------------------


def test_requirements_label_hidden_with_no_school_assignments(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    assert editor._requirements_lbl.isHidden()


def test_requirements_label_shows_derived_counts(qapp):
    config = DuelConfig(
        player_assignments=[
            PlayerAssignment(players=[1, 2], dsl="x", schools=["Storm"]),
            PlayerAssignment(players=[3], dsl="y", schools=["Life"]),
        ],
    )
    editor = DuelConfigEditor()
    editor.load(config, [])

    assert not editor._requirements_lbl.isHidden()
    assert "Storm ×2" in editor._requirements_lbl.text()
    assert "Life ×1" in editor._requirements_lbl.text()


def test_requirements_label_updates_when_a_row_school_changes(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    pick(editor.cards[0].school_picker, "Fire")

    assert not editor._requirements_lbl.isHidden()


def test_requirements_label_updates_when_a_row_is_removed(qapp):
    config = DuelConfig(
        player_assignments=[
            PlayerAssignment(players=[1], dsl="x", schools=["Storm"]),
            PlayerAssignment(players=[2], dsl="y"),
        ],
    )
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor._on_remove_card(editor.cards[0])

    assert editor._requirements_lbl.isHidden()


# ---------------------------------------------------------------------------
# Assignment row: school and note (phase 1)
# ---------------------------------------------------------------------------


def test_row_loads_school_and_note_from_the_assignment(qapp):
    config = DuelConfig(
        player_assignments=[PlayerAssignment(players=[1], dsl="x", schools=["Storm"], note="Kites adds")],
    )
    editor = DuelConfigEditor()
    editor.load(config, [])

    assert editor.cards[0].school_picker.value() == ["Storm"]
    assert editor.cards[0]._note_edit.text() == "Kites adds"


def test_editing_the_row_school_mutates_the_assignment_live(qapp):
    assignment = PlayerAssignment(players=[1], dsl="x")
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [])

    pick(editor.cards[0].school_picker, "Fire")

    assert assignment.schools


def test_editing_the_row_note_mutates_the_assignment_live(qapp):
    assignment = PlayerAssignment(players=[1], dsl="x")
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor.cards[0]._note_edit.setText("Passes to bank pips")
    editor.cards[0]._note_edit.editingFinished.emit()

    assert assignment.note == "Passes to bank pips"


def test_set_editable_false_disables_school_and_note(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor.set_editable(False)

    assert not editor.cards[0].school_picker.isEnabled()
    assert not editor.cards[0]._note_edit.isEnabled()


def _choose(card, data: str) -> None:
    """Pick one of the three answers the way the change-strategy menu does."""
    card.choose_strategy(data)


def test_forking_to_custom_dsl_seeds_from_the_strategy_left_behind(qapp):
    pcs = PlayerCombatStrategy(name="Preset", dsl="blade", schools=["Storm"])
    assignment = PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [pcs])

    _choose(editor.cards[0], CUSTOM_DATA)

    assert assignment.dsl == "blade"
    assert assignment.schools == ["Storm"]
    assert assignment.player_combat_strategy_id is None


def test_forking_to_custom_dsl_leaves_an_authored_assignment_school_alone(qapp):
    pcs = PlayerCombatStrategy(name="Preset", dsl="blade", schools=["Storm"])
    assignment = PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id, schools=["Life"])
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [pcs])

    _choose(editor.cards[0], CUSTOM_DATA)

    assert assignment.schools == ["Life"]


def test_choosing_the_built_in_default_clears_both_answers(qapp):
    assignment = PlayerAssignment(players=[1], dsl="blade")
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [])

    _choose(editor.cards[0], DEFAULT_DATA)

    assert assignment.dsl == ""
    assert assignment.player_combat_strategy_id is None
    assert editor.cards[0]._dsl_edit.isHidden()


def test_choosing_a_named_strategy_links_it(qapp):
    pcs = PlayerCombatStrategy(name="Preset", dsl="blade")
    assignment = PlayerAssignment(players=[1], dsl="pass")
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [pcs])

    _choose(editor.cards[0], pcs.id)

    assert assignment.player_combat_strategy_id == pcs.id
    assert editor.cards[0]._dsl_edit.isHidden()


def test_a_link_to_a_deleted_strategy_is_named_rather_than_silently_reset(qapp):
    """Resetting it would look like it was always configured that way, and Save
    would then make that true."""
    assignment = PlayerAssignment(players=[1], player_combat_strategy_id="gone")
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [])

    assert editor.cards[0].title_text() == MISSING_TITLE
    assert assignment.player_combat_strategy_id == "gone"


# ---------------------------------------------------------------------------
# The note field was wired to ``editingFinished``, which only fires when the
# widget loses focus.  Typing a note and then hitting the window's Save while the
# caret was still in the field discarded what had just been typed, with no
# indication anything had been lost.  Every other field here writes through on
# change, including the DSL box beside it.
#
# ``setText`` is the sharp end of the distinction: it emits ``textChanged`` and
# never emits ``editingFinished``, so this fails on the old wiring.
# ---------------------------------------------------------------------------


def test_typing_a_note_reaches_the_assignment_without_losing_focus(qapp):
    assignment = PlayerAssignment(players=[1], dsl="any<damage>")
    config = DuelConfig(player_assignments=[assignment])
    editor = DuelConfigEditor()
    editor.load(config, [])

    editor.cards[0]._note_edit.setText("Passes to bank pips for round 3")

    assert assignment.note == "Passes to bank pips for round 3"


@pytest.mark.parametrize("empty", ["no assignments", "cleared"])
def test_loading_after_an_empty_rebuild_survives_the_deferred_delete(qapp, empty):
    """New, then any other duel, crashed the app: a rebuild to no cards left the drag
    holding the discarded cards' grips, and the next load touched them once deleted.

    The suite runs no event loop, so the deferred delete that made it fatal is flushed
    by hand — without that, the stale grips are still alive and nothing fails.
    """
    from PyQt6.QtCore import QCoreApplication, QEvent  # noqa: PLC0415

    def with_cards() -> DuelConfig:
        return DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="pass")])

    editor = DuelConfigEditor()
    editor.load(with_cards(), [])
    if empty == "cleared":
        editor.clear()
    else:
        editor.load(DuelConfig(), [])
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)

    editor.load(with_cards(), [])

    assert len(editor.cards) == 1


# The slot-order line (#212)

_SUPPORT = PlayerCombatStrategy(name="Support", dsl="any<blade>[sharp] @ ally(last) | pass")


def _support_duel(**kwargs) -> DuelConfig:
    return DuelConfig(
        max_players=4,
        player_assignments=[PlayerAssignment(players=[2, 3, 4], player_combat_strategy_id=_SUPPORT.id)],
        **kwargs,
    )


def test_a_slot_left_to_chance_is_said_under_the_combat_order(qapp):
    editor = DuelConfigEditor()
    editor.load(_support_duel(), [_SUPPORT])

    assert editor.slot_warning.startswith("P2–P4 target ally(last)")


def test_an_ordered_duel_says_nothing_about_slots(qapp):
    editor = DuelConfigEditor()
    editor.load(_support_duel(combat_order=[4, 3, 2, 1]), [_SUPPORT])

    assert editor.slot_warning == ""


def test_choosing_simultaneous_entry_brings_the_slot_warning(qapp):
    editor = DuelConfigEditor()
    editor.load(_support_duel(combat_order=[4, 3, 2, 1]), [_SUPPORT])

    editor._order_widget._checkbox.setChecked(True)

    assert editor.slot_warning


def test_removing_the_support_takes_the_slot_warning_with_it(qapp):
    editor = DuelConfigEditor()
    editor.load(_support_duel(), [_SUPPORT])

    editor._on_remove_card(editor.cards[0])

    assert editor.slot_warning == ""
