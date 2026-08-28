"""Tests for the local teleport-target settings introduced with #102.

The friend name is machine configuration rather than bot content — bot files must not
carry a character name — so it lives here, and the history beside it exists only to make
typing it easier.

Each test builds a private SettingsManager against ``tmp_path`` rather than touching the
``%APPDATA%`` singleton.
"""

from __future__ import annotations

import pytest

from bartlebot import settings_manager as sm_mod
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


def test_default_friend_name_starts_empty(settings):
    """Unset is the only honest default: there is no name we could guess, and guessing
    would teleport somebody somewhere."""
    assert settings.default_friend_name == ""


def test_default_friend_name_round_trips_and_is_stripped(settings):
    settings.default_friend_name = "  Luke SkullHunter  "

    assert settings.default_friend_name == "Luke SkullHunter"


def test_recent_names_are_newest_first(settings):
    settings.add_recent_friend_name("Alice StormBlade")
    settings.add_recent_friend_name("Bob FrostGem")

    assert settings.recent_friend_names == ["Bob FrostGem", "Alice StormBlade"]


def test_recasing_a_name_does_not_add_a_second_entry(settings):
    """A retype with different casing is the same wizard. The newest spelling wins,
    because that is the one the user just looked at."""
    settings.add_recent_friend_name("Luke SkullHunter")
    settings.add_recent_friend_name("luke skullhunter")

    assert settings.recent_friend_names == ["luke skullhunter"]


def test_blank_names_are_not_remembered(settings):
    settings.add_recent_friend_name("   ")

    assert settings.recent_friend_names == []


def test_history_is_capped(settings):
    for i in range(30):
        settings.add_recent_friend_name(f"Wizard{i}")

    assert len(settings.recent_friend_names) == 20
    assert settings.recent_friend_names[0] == "Wizard29"


def test_history_does_not_decide_the_target(settings):
    """The history is autocomplete and nothing else. If it could stand in for an unset
    name, typing into an unrelated bot last week would silently redirect this run."""
    settings.add_recent_friend_name("Alice StormBlade")

    assert settings.default_friend_name == ""
