"""The Favorite Realm setting and the ``realm`` option type behind it.

A realm is the user's, never the bot's: a bot file naming one would send everyone who
runs it to the same realm. So it is a bot option with a null declaration, answered by
the Favorite Realm setting on the Bot tab, overridable per bot in Option Values, and picked
from the checked-in realm table rather than typed.

Real widget tests (QApplication required). The settings store is injected.
"""

from __future__ import annotations

import pytest

from bartlebot import settings_manager as sm_mod
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.game import realms
from bartlebot.gui.settings.bot_tab import BotTab
from bartlebot.gui.sidebar.sections.option_values_section import OptionValuesSection
from bartlebot.gui.widgets.inputs.option_widgets import NODE_ONLY_TYPES, OPTION_TYPES, make_option_widget, widget_value
from bartlebot.gui.widgets.inputs.realm_combo import NOT_SET_TEXT, RealmCombo
from bartlebot.nodes.utility import change_realm as cf
from bartlebot.options import bot_options
from bartlebot.settings_manager import SettingsManager

_OPTION = cf.OPTION_FAVORITE_REALM


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


def _config() -> BotConfig:
    return BotConfig(
        id="realm-bot",
        name="Realm Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="change_realm", params={"use_favorite_realm": True})],
        transitions=[],
    )


def _field(tab, label: str):
    return tab.row(label).control


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


def test_the_option_type_builds_the_picker(qapp):
    widget = make_option_widget("realm", "Balestrom")

    assert isinstance(widget, RealmCombo)
    assert widget_value(widget) == "Balestrom"


def test_the_picker_offers_every_realm_and_an_empty_choice_first(qapp):
    combo = RealmCombo()

    assert combo.itemText(0) == NOT_SET_TEXT
    assert combo.get_value() == ""
    assert [combo.itemData(i) for i in range(1, combo.count())] == realms.realm_keys()
    assert combo.itemText(combo.findData("Balestrom")) == "Balestrom"


def test_the_picker_keeps_a_value_it_does_not_recognize(qapp):
    """A retired realm in settings.json is shown as such, not silently reset."""
    combo = RealmCombo()
    combo.set_value("Wu")

    assert combo.get_value() == "Wu"
    assert "unknown" in combo.currentText()


def test_the_type_is_node_only():
    """Nothing to pick from behind a hand-added option, so the Add dialog does not offer it."""
    assert "realm" in NODE_ONLY_TYPES
    assert "realm" not in OPTION_TYPES


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_the_bot_tab_writes_the_chosen_realm(qapp):
    """Round trip through the settings singleton, which is the tab's only output."""
    sm = SettingsManager.instance()
    before = sm.favorite_realm
    try:
        tab = BotTab()
        _field(tab, "Favorite Realm").set_value("Wraith")

        tab.apply()

        assert sm.favorite_realm == "Wraith"
        assert _field(BotTab(), "Favorite Realm").get_value() == "Wraith"
    finally:
        sm.favorite_realm = before


# ---------------------------------------------------------------------------
# The resolution
# ---------------------------------------------------------------------------


def test_the_app_setting_answers_until_a_bot_needs_its_own(qapp, settings):
    settings.favorite_realm = "Balestrom"
    section = OptionValuesSection(settings=settings)
    section.set_config(_config())

    assert section.option_widgets[_OPTION].get_value() == "Balestrom"
    assert section.resolution_for(_OPTION).source == bot_options.SOURCE_APP_SETTING


def test_a_per_bot_override_wins_over_the_setting(qapp, settings):
    settings.favorite_realm = "Balestrom"
    config = _config()
    settings.set_option_override(config.id, _OPTION, "Wraith")
    section = OptionValuesSection(settings=settings)
    section.set_config(config)

    resolution = section.resolution_for(_OPTION)
    assert resolution.value == "Wraith"
    assert resolution.source == bot_options.SOURCE_OVERRIDE


def test_no_bot_may_declare_a_realm():
    """The Options surface omits user-owned rows, so nothing the app writes can produce one."""
    assert bot_options.is_user_owned(_OPTION)
