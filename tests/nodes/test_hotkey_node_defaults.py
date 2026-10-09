"""The node hotbar's default slots (#85), and the one-shot seed that reaches old installs."""

from __future__ import annotations

import json

import pytest

from bartlebot import settings_manager as sm_mod
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.settings_manager import DEFAULT_HOTKEY_NODES, SettingsManager


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return tmp_path


def _write(directory, data) -> None:
    (directory / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_every_default_slot_names_a_real_node():
    assert len(DEFAULT_HOTKEY_NODES) == 10
    assert all(n in NODE_SPECS for n in DEFAULT_HOTKEY_NODES if n)


def test_a_new_install_gets_the_defaults(settings_dir):
    assert SettingsManager().favorited_hotkey_nodes == list(DEFAULT_HOTKEY_NODES)


def test_a_file_saved_before_the_defaults_is_filled_once(settings_dir):
    """save() wrote every default, so an old file holds ten empty slots, not a missing key."""
    _write(settings_dir, {"favorited_hotkey_nodes": [""] * 10})

    assert SettingsManager().favorited_hotkey_nodes == list(DEFAULT_HOTKEY_NODES)


def test_slots_the_user_filled_are_kept(settings_dir):
    mine = ["sleep"] + [""] * 9
    _write(settings_dir, {"favorited_hotkey_nodes": mine})

    assert SettingsManager().favorited_hotkey_nodes == mine


def test_clearing_every_slot_after_the_seed_sticks(settings_dir):
    manager = SettingsManager()
    for slot in range(10):
        manager.clear_favorited_hotkey_node(slot)

    assert SettingsManager().favorited_hotkey_nodes == [""] * 10
