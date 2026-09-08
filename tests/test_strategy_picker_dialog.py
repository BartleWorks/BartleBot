"""Tests for StrategyPickerDialog and the StrategyCard it renders.

Real widget tests (QApplication required) — the behavior under test is Qt
wiring: filtering, selection, and that a card renders from a StrategySummary
without crashing on every strategy kind.

Known expiry, #3: ``StrategyPickerDialog`` is being replaced by a run-mode
pick surface organized around player count and role rather than school, so the
four tests here that assert its wiring (ordering, selection, filtering, and
pass-through of the PCS list) describe a dialog that will not exist. Rewrite
them against the new surface rather than porting them — what they assert is
right, but they assert it about widgets that are going away.

The two ``StrategyCard`` tests are not affected: the card and the
``StrategySummary`` layer beneath it survive into #3 and #4, and are the
reason this half of the combat dialog rework was kept.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.gui.strategy_library.strategy_picker_dialog import StrategyPickerDialog
from bartlebot.gui.widgets.strategy_card import StrategyCard
from bartlebot.strategy_summary import HealthStatus, summarize_duel

_BROKEN_DSL = "@@@ not a dsl"


def test_picker_lists_built_in_default_first(qapp):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    dlg = StrategyPickerDialog("Pick", "duel", [dcs], current_id=None, allow_none=True)

    assert dlg._list.count() == 2
    assert dlg._list.item(0).data(Qt.ItemDataRole.UserRole) is None


def test_picker_selects_the_current_strategy(qapp):
    a = DuelCombatStrategy(name="A")
    b = DuelCombatStrategy(name="B")
    dlg = StrategyPickerDialog("Pick", "duel", [a, b], current_id=b.id, allow_none=False)

    assert dlg.selected_id() == b.id


def test_picker_filter_matches_description(qapp):
    a = DuelCombatStrategy(name="A", description="tank and spank")
    b = DuelCombatStrategy(name="B", description="unrelated")
    dlg = StrategyPickerDialog("Pick", "duel", [a, b], current_id=None, allow_none=False)

    dlg._filter.setText("tank")

    assert len(dlg._card_map) == 1
    assert dlg._card_map[0][0].data(Qt.ItemDataRole.UserRole) == a.id


def test_picker_passes_all_pcs_through_to_duel_summary(qapp):
    pcs = PlayerCombatStrategy(name="Storm DPS", dsl="any<blade>", school="Storm")
    dcs = DuelCombatStrategy(
        name="Boss", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    dlg = StrategyPickerDialog("Pick", "duel", [dcs], current_id=None, allow_none=False, all_pcs=[pcs])

    card = dlg._card_map[0][1]
    assert isinstance(card, StrategyCard)


def test_picker_renders_a_bot_and_player_strategy_without_crashing(qapp):
    bcs = BotCombatStrategy(name="Bot", bot_id="bot-1")
    dlg = StrategyPickerDialog("Pick", "bot", [bcs], current_id=None, allow_none=False)
    assert dlg._list.count() == 1

    pcs = PlayerCombatStrategy(name="Preset", dsl="any<blade>")
    dlg2 = StrategyPickerDialog("Pick", "player", [pcs], current_id=None, allow_none=False)
    assert dlg2._list.count() == 1


def test_strategy_card_flags_a_broken_dsl(qapp):
    summary = summarize_duel(
        DuelCombatStrategy(name="Broken", player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)]), []
    )
    assert summary.health.status is HealthStatus.INVALID_DSL

    card = StrategyCard(summary)
    assert card._invalid is True
