"""A style change that leaves the theme as it was writes no stylesheet anywhere.

Every ``setStyleSheet`` re-polishes the widget and everything under it, and sends each of
them a ``StyleChange`` of its own. On the strategy dialog a page restyling its card list
with the very rule it already had cost a pass over every card, three times per page per
open. ``themed.set_style_sheet`` compares first; these surfaces answer a ``StyleChange``
through it, and this checks they still do.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.combat.strategy_store import StrategyKind
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.gui.canvas.drop_keep_list import DropKeepListWidget
from bartlebot.gui.combat_strategies.dialog import _KindPage
from bartlebot.gui.combat_strategies.duel_editor import DuelConfigEditor
from bartlebot.gui.combat_strategies.player_editor import PlayerStrategyEditor
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.shared.strategy_models import DuelConfig, PlayerAssignment, PlayerCombatStrategy


def _strategy_page() -> QWidget:
    return _KindPage(StrategyKind.PLAYER, StrategyWorkingSet.from_store())


def _player_editor() -> QWidget:
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl="pass"))
    return editor


def _assignment_card() -> QWidget:
    editor = DuelConfigEditor()
    editor.load(DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl="pass")]), [])
    return editor.cards[0]


def _combat_order() -> QWidget:
    widget = CombatOrderWidget()
    widget.set_value([1, 2, 3, 4])
    return widget


def _drop_keep_list() -> QWidget:
    return DropKeepListWidget(lambda: [])


@pytest.mark.parametrize(
    "build",
    [_strategy_page, _player_editor, _assignment_card, _combat_order, _drop_keep_list],
    ids=lambda b: b.__name__,
)
def test_a_style_change_with_the_theme_unchanged_writes_no_stylesheet(qapp, build):
    root = build()
    written: list[QWidget] = []
    for widget in [root, *root.findChildren(QWidget)]:
        real = widget.setStyleSheet
        widget.setStyleSheet = lambda sheet, widget=widget, real=real: (written.append(widget), real(sheet))

    QApplication.sendEvent(root, QEvent(QEvent.Type.StyleChange))

    assert written == []
