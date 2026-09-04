"""The Friend Name option: a value no bot file may ever carry.

A character name in a shared bot file is two bugs at once -- a privacy leak, and a
bot only its author can run (#102).  The old answer was an ``app_setting_str``
param, a widget that wrote the app setting directly and a ``get_params`` that
skipped the key: a mechanism invented because a user's value had nowhere else to
live.  It has somewhere now, so the name is an ordinary bot option with a null
declaration, and the guarantee comes from the option layer rather than from a
widget that refuses to report itself.

What is worth pinning down is the shape of that guarantee: the only editing surface
is Setup, which writes overrides and never the file, and the app setting is still the
level underneath, so one idler serves every bot until some bot needs another.

Real widget tests (QApplication required).  The settings store is injected.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot import bot_options
from bartlebot import settings_manager as sm_mod
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.sidebar.sections.setup_section import SetupSection
from bartlebot.gui.widgets.friend_name_edit import FriendNameEdit
from bartlebot.gui.widgets.option_widgets import make_option_widget, widget_value
from bartlebot.nodes.teleportation import teleport_to_friend as ttf
from bartlebot.settings_manager import SettingsManager

_OPTION = ttf.OPTION_FRIEND_NAME
_NAME = "Luke SkullHunter"  # synthetic — see CLAUDE.md, "No real player data"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


def _config() -> BotConfig:
    return BotConfig(
        id="teleport-bot",
        name="Teleport Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="teleport_to_friend", params={"to_zone": "Z"})],
        transitions=[],
    )


def _section(settings, config) -> SetupSection:
    section = SetupSection(settings=settings)
    section.set_config(config)
    return section


def test_the_option_type_builds_the_picker_widget(qapp):
    widget = make_option_widget("friend_name", _NAME)

    assert isinstance(widget, FriendNameEdit)
    assert widget_value(widget) == _NAME


def test_the_node_declares_it_with_no_opinion_of_its_own(qapp):
    """Null, always: an author cannot have a view on whose wizard you port to, and a
    non-null declaration would outrank the app setting under it."""
    spec = next(o for o in ttf.NODE_SPEC.required_bot_options if o.name == _OPTION)

    assert spec.default is None


def test_the_app_setting_answers_until_a_bot_needs_its_own(qapp, settings):
    """One idler for every bot is the common case, and it stays one place to set."""
    settings.set_general("default_friend_name", _NAME)
    section = _section(settings, _config())

    assert section.option_widgets[_OPTION].get_value() == _NAME
    assert section.resolution_for(_OPTION).source == bot_options.SOURCE_APP_SETTING


def test_editing_it_writes_an_override_and_never_the_bot_file(qapp, settings):
    """The whole guarantee, in one assertion: the name reaches settings.json and the
    bot's own options stay empty."""
    settings.set_general("default_friend_name", _NAME)
    config = _config()
    section = _section(settings, config)

    widget = section.option_widgets[_OPTION]
    widget.set_value("Someone Elsewhere")
    widget.changed.emit()

    assert settings.get_option_override(config.id, _OPTION) == "Someone Elsewhere"
    assert config.options == []
    assert settings.get_general("default_friend_name") == _NAME


def test_the_name_is_remembered_for_autocomplete_only(qapp, settings, monkeypatch):
    """The history is a suggestion list and nothing else — no run-time code reads it,
    so a remembered name can never decide where a bot teleports."""
    monkeypatch.setattr(SettingsManager, "instance", classmethod(lambda cls: settings))
    widget = FriendNameEdit()
    widget.set_value(_NAME)

    widget._on_edited()

    assert _NAME in settings.recent_friend_names


def test_the_declaration_editor_does_not_offer_it_at_all(qapp, settings):
    """A row here would be a place to type a character name into a file people
    share. The author has nothing to say about whose wizard you port to, so the
    option is simply not on their surface -- it is a row in Setup instead."""
    from bartlebot.gui.sidebar.sections.options_section import OptionsSection

    config = _config()
    section = OptionsSection(settings=settings)
    section.set_config(config)

    assert _OPTION not in section.option_widgets
    assert config.options == []
