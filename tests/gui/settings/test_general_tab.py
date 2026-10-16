"""Settings › General's Updates row: the startup check's setting (#27)."""

from __future__ import annotations

from bartlebot.gui.settings.general_tab import GeneralTab
from bartlebot.settings_manager import SettingsManager, default_for


def test_check_for_updates_at_startup_is_on_by_default(qapp):
    assert default_for("updates", "check_at_startup") is True
    assert SettingsManager.instance().check_for_updates_at_startup
    assert SettingsManager.instance().last_offered_update is None


def test_the_row_is_under_its_own_heading_and_says_what_it_does(qapp, parentless_shows):
    tab = GeneralTab()

    row = tab.check_updates_row
    assert row.title == "Check for updates at startup"
    assert "Help › Check for Updates…" in row.details.text()
    assert parentless_shows == []


def test_ok_writes_it(qapp):
    tab = GeneralTab()
    tab.check_updates_row.control.setChecked(False)

    tab.apply()

    assert SettingsManager.instance().check_for_updates_at_startup is False


def test_last_offered_survives_a_reload(qapp, monkeypatch):
    settings = SettingsManager.instance()
    settings.last_offered_update = "1.2.0"
    settings.save()
    monkeypatch.setattr(SettingsManager, "_instance", None)

    assert SettingsManager.instance().last_offered_update == "1.2.0"
