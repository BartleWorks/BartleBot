"""A style change that leaves the theme as it was writes no stylesheet anywhere.

Every ``setStyleSheet`` re-polishes the widget and everything under it, and sends each of
them a ``StyleChange`` of its own. On the strategy dialog a page restyling its card list
with the very rule it already had cost a pass over every card, three times per page per
open. ``themed.set_style_sheet`` compares first; these surfaces answer a ``StyleChange``
through it, and this checks they still do. Core's editors are checked in core.
"""

from __future__ import annotations

import pytest
from bartleby.shared.strategy_library import StrategyKind
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.combat.bot_strategy_owner import load_working_set
from bartlebot.gui.canvas.drop_keep_list import DropKeepListWidget
from bartlebot.gui.combat_strategies.dialog import BotKindPage
from bartlebot.settings_manager import SettingsManager


def _strategy_page() -> QWidget:
    return BotKindPage(StrategyKind.PLAYER, load_working_set(), SettingsManager.instance())


def _drop_keep_list() -> QWidget:
    return DropKeepListWidget(lambda: [])


@pytest.mark.parametrize(
    "build",
    [_strategy_page, _drop_keep_list],
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
