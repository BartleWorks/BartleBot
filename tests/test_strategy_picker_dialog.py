"""Tests for BotStrategyPicker and the StrategyCard it renders.

Real widget tests (QApplication required) — the behavior under test is Qt
wiring: filtering, selection, and that a card renders from a StrategySummary
without crashing.

This file used to cover the general ``StrategyPickerDialog``, which picked all
four strategy kinds through a ``mode`` switch. #4 replaced that with
``CombatStrategyDialog``, which can also *edit* what it is showing, and left the
picker narrowed to the one pick that is not a library pick: a BotCombatStrategy,
which is bot-scoped and authored on the Combat tab itself. The duel/player
equivalents of these assertions live in ``test_combat_strategy_dialog.py``.

The ``StrategyCard`` test is unaffected: the card and the ``StrategySummary``
layer beneath it are shared by both surfaces.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    PlayerAssignment,
)
from bartlebot.gui.combat_strategies.bot_strategy_picker import BotStrategyPicker
from bartlebot.gui.widgets.strategy_card import StrategyCard
from bartlebot.strategy_summary import HealthStatus, summarize_duel

_BROKEN_DSL = "@@@ not a dsl"


def test_picker_lists_built_in_default_first(qapp):
    bcs = BotCombatStrategy(name="Solo", bot_id="bot-1")
    dlg = BotStrategyPicker([bcs], current_id=None, allow_none=True)

    assert dlg._list.count() == 2
    assert dlg._list.item(0).data(Qt.ItemDataRole.UserRole) is None


def test_picker_selects_the_current_strategy(qapp):
    a = BotCombatStrategy(name="A", bot_id="bot-1")
    b = BotCombatStrategy(name="B", bot_id="bot-1")
    dlg = BotStrategyPicker([a, b], current_id=b.id, allow_none=False)

    assert dlg.selected_id() == b.id


def test_picker_filter_matches_description(qapp):
    a = BotCombatStrategy(name="A", bot_id="bot-1", description="tank and spank")
    b = BotCombatStrategy(name="B", bot_id="bot-1", description="unrelated")
    dlg = BotStrategyPicker([a, b], current_id=None, allow_none=False)

    dlg._filter.setText("tank")

    assert len(dlg._card_map) == 1
    assert dlg._card_map[0][0].data(Qt.ItemDataRole.UserRole) == a.id


def test_picker_renders_a_bot_strategy_without_crashing(qapp):
    bcs = BotCombatStrategy(name="Bot", bot_id="bot-1")
    dlg = BotStrategyPicker([bcs], current_id=None, allow_none=False)

    assert dlg._list.count() == 1
    assert isinstance(dlg._card_map[0][1], StrategyCard)


def test_strategy_card_flags_a_broken_dsl(qapp):
    summary = summarize_duel(
        DuelCombatStrategy(name="Broken", player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)]), []
    )
    assert summary.health.status is HealthStatus.INVALID_DSL

    card = StrategyCard(summary)
    assert card._invalid is True
