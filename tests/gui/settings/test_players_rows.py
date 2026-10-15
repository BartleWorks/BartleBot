"""The Automatically group players and Refill potions rows, built once for Settings › Bot and setup (#232)."""

from __future__ import annotations

from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.settings.players_rows import (
    AUTO_GROUP_TITLE,
    REFILL_POTIONS_TITLE,
    auto_group_row,
    refill_potions_row,
)
from bartlebot.settings_manager import SettingsManager


def test_the_rows_open_on_the_stored_values(qapp):
    sm = SettingsManager.instance()
    sm.auto_group_players = False
    sm.set_general("default_refill_potions", False)

    assert auto_group_row(sm).control.isChecked() is False
    assert refill_potions_row(sm).control.isChecked() is False


def test_the_bot_tab_writes_what_the_rows_show(qapp):
    tab = BotTab()
    tab.row(AUTO_GROUP_TITLE).control.setChecked(False)
    tab.row(REFILL_POTIONS_TITLE).control.setChecked(False)

    tab.apply()

    sm = SettingsManager.instance()
    assert (sm.auto_group_players, sm.get_general("default_refill_potions")) == (False, False)
    assert auto_group_row(sm).control.isChecked() is False
