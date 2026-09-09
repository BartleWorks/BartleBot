"""Tests for live DSL validation in the strategy editors (the combat dialog rework, phase 6).

``validate_dsl`` is pure and cheap (see ``combat_runner.validate_dsl``), so both
editors run it and reflect the result as an error line plus a colored border.
Validation piggybacks on a debounce so a half-typed DSL is never flagged mid-edit;
these tests call the private update methods directly rather than waiting on a
real QTimer, the same way the identity tests call ``_flush`` directly.
"""

from bartlebot.bot.strategy_models import DuelConfig, PlayerAssignment, PlayerCombatStrategy
from bartlebot.gui.combat_strategies.duel_editor import DuelConfigEditor
from bartlebot.gui.combat_strategies.player_editor import PlayerStrategyEditor
from bartlebot.strategy_summary import HealthStatus, summarize_duel

_INVALID_DSL = "not even close to valid (("
_VALID_DSL = "any<damage> @ enemy"

# ---------------------------------------------------------------------------
# PlayerStrategyEditor
# ---------------------------------------------------------------------------


def test_pcs_editor_load_with_broken_dsl_shows_the_error_immediately(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=_INVALID_DSL))
    assert not editor._dsl_error_lbl.isHidden()
    assert editor._dsl_error_lbl.text()


def test_pcs_editor_load_with_valid_dsl_hides_the_error(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=_VALID_DSL))
    assert editor._dsl_error_lbl.isHidden()


def test_pcs_editor_empty_dsl_is_not_flagged(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=""))
    assert editor._dsl_error_lbl.isHidden()


def test_pcs_editor_flush_updates_the_error_after_editing(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=_VALID_DSL))
    assert editor._dsl_error_lbl.isHidden()

    editor._dsl_edit.setPlainText(_INVALID_DSL)
    editor._update_dsl_validation()

    assert not editor._dsl_error_lbl.isHidden()


def test_pcs_editor_fixing_the_dsl_clears_the_error_on_flush(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=_INVALID_DSL))
    assert not editor._dsl_error_lbl.isHidden()

    editor._dsl_edit.setPlainText(_VALID_DSL)
    editor._update_dsl_validation()

    assert editor._dsl_error_lbl.isHidden()


def test_pcs_editor_clear_hides_the_error(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=_INVALID_DSL))
    assert not editor._dsl_error_lbl.isHidden()

    editor.clear()

    assert editor._dsl_error_lbl.isHidden()


# ---------------------------------------------------------------------------
# _AssignmentRow, via DuelConfigEditor
# ---------------------------------------------------------------------------


def test_assignment_row_load_with_broken_dsl_shows_the_error_immediately(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl=_INVALID_DSL)])
    editor = DuelConfigEditor()
    editor.load(config, [])

    assert not editor._rows[0]._dsl_error_lbl.isHidden()


def test_assignment_row_empty_dsl_is_not_flagged(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="")])
    editor = DuelConfigEditor()
    editor.load(config, [])

    assert editor._rows[0]._dsl_error_lbl.isHidden()


def test_assignment_row_editing_starts_the_validate_debounce_not_an_immediate_flag(qapp):
    """Typing must not flag the box mid-edit — only the debounced re-check does."""
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    editor = DuelConfigEditor()
    editor.load(config, [])
    row = editor._rows[0]

    row._dsl_edit.setPlainText(_INVALID_DSL)

    assert row._validate_timer.isActive()
    assert row._dsl_error_lbl.isHidden()


def test_assignment_row_debounced_recheck_flags_invalid_dsl(qapp):
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl=_VALID_DSL)])
    editor = DuelConfigEditor()
    editor.load(config, [])
    row = editor._rows[0]

    row._dsl_edit.setPlainText(_INVALID_DSL)
    row._validate_timer.stop()
    row._update_dsl_validation()

    assert not row._dsl_error_lbl.isHidden()
    assert row.assignment().dsl == _INVALID_DSL


def test_a_linked_row_shows_no_dsl_box_and_no_error_of_its_own(qapp):
    """A named answer builds no DSL box, so it has no error line to raise.

    A broken Player Strategy is still visible where it is used — the duel's own
    summary carries INVALID_DSL and its card draws the warning — but the row
    itself no longer mirrors DSL it does not own.
    """
    pcs = PlayerCombatStrategy(name="Broken", dsl=_INVALID_DSL)
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)])
    editor = DuelConfigEditor()
    editor.load(config, [pcs])

    row = editor._rows[0]
    assert not row.is_custom()
    assert row._dsl_error_lbl.isHidden()
    assert summarize_duel(config, [pcs]).health.status is HealthStatus.INVALID_DSL
