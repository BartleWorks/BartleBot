"""Tests for the auto-save-stem -> original-bot-file mapping in SettingsManager.

Crash recovery needs to know which original file an auto-save was derived from
so it can label the recovery candidate correctly instead of always falling back
to "(unsaved new bot)". ``set_autosave_origin`` records that link
at auto-save time; ``get_autosave_origin`` and ``remove_autosave_origin`` read
and clear it.

Each test builds a private SettingsManager against ``tmp_path`` rather than
touching the ``%APPDATA%`` singleton.
"""

from __future__ import annotations

import json

import pytest

from bartlebot import settings_manager as sm_mod
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A SettingsManager writing to tmp_path, isolated from the real singleton."""
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


def test_set_and_get_round_trip(settings, tmp_path):
    original = tmp_path / "MyBot.json"
    original.write_text("{}", encoding="utf-8")
    settings.set_autosave_origin("MyBot", str(original))
    assert settings.get_autosave_origin("MyBot") == str(original.resolve())


def test_get_returns_none_for_an_unknown_stem(settings):
    assert settings.get_autosave_origin("nobody") is None


def test_remove_clears_the_entry(settings, tmp_path):
    original = tmp_path / "MyBot.json"
    settings.set_autosave_origin("MyBot", str(original))
    settings.remove_autosave_origin("MyBot")
    assert settings.get_autosave_origin("MyBot") is None


def test_remove_of_unknown_stem_is_a_noop(settings):
    settings.remove_autosave_origin("nobody")
    assert settings.get_autosave_origin("nobody") is None


def test_set_persists_to_disk(settings, tmp_path):
    original = tmp_path / "MyBot.json"
    settings.set_autosave_origin("MyBot", str(original))
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["autosave_origins"] == {"MyBot": str(original.resolve())}


def test_set_overwrites_existing_stem(settings, tmp_path):
    first = tmp_path / "First.json"
    second = tmp_path / "Second.json"
    settings.set_autosave_origin("MyBot", str(first))
    settings.set_autosave_origin("MyBot", str(second))
    assert settings.get_autosave_origin("MyBot") == str(second.resolve())


def test_multiple_stems_independent(settings, tmp_path):
    a = tmp_path / "A.json"
    b = tmp_path / "B.json"
    settings.set_autosave_origin("A", str(a))
    settings.set_autosave_origin("B", str(b))
    settings.remove_autosave_origin("A")
    assert settings.get_autosave_origin("A") is None
    assert settings.get_autosave_origin("B") == str(b.resolve())
