"""Quicksell's settings card, built once for Settings › Bot and setup's Quicksell page (#230)."""

from __future__ import annotations

from bartlebot.gui.settings.quicksell_group import QuicksellSettings
from bartlebot.gui.settings.rows import SettingRow
from bartlebot.options.quicksell_setup import SellFlags, current_flags, current_keep_list, write_sell_flags
from bartlebot.settings_manager import SettingsManager


def test_the_rows_open_on_the_stored_values(qapp):
    sm = SettingsManager.instance()
    write_sell_flags(sm, SellFlags(False, False, True, True))
    sm.set_general("quicksell_keep_list", ["No Pain, No Gain Onyx"])

    settings = QuicksellSettings(sm)

    assert settings.flags() == SellFlags(False, False, True, True)
    assert settings.keep_list() == ["No Pain, No Gain Onyx"]


def test_write_sets_what_the_rows_show(qapp):
    sm = SettingsManager.instance()
    settings = QuicksellSettings(sm)
    settings.group.header.control.setChecked(False)
    settings.keep_list_edit.setText("Amber,  Pearl , ")

    settings.write(sm)

    assert current_flags(sm).enabled is False
    assert current_keep_list(sm) == ["Amber", "Pearl"]


def test_extra_rows_go_before_always_keep(qapp):
    from PyQt6.QtWidgets import QCheckBox  # noqa: PLC0415

    extra = SettingRow("Extra", "A row of the tab's own.", QCheckBox(), default=False)

    settings = QuicksellSettings(SettingsManager.instance(), extra_rows=[extra])

    assert [row.title for row in settings.group.rows][-2:] == ["Extra", "Always keep"]
