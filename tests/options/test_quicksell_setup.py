"""What bots may sell: what setup's Quicksell page records, and when it counts as answered (epic #215, Stage 5, #230).

Checked against ``settings.json`` on disk, since a first run that closed after the page
has to find the answer there next launch.
"""

from __future__ import annotations

import json

import pytest

from bartlebot.options.quicksell_setup import (
    ANSWERED_KEY,
    DEFAULT_FLAGS,
    SellFlags,
    current_flags,
    current_keep_list,
    quicksell_answered,
    record_quicksell,
    write_keep_list,
    write_sell_flags,
)
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def sm() -> SettingsManager:
    return SettingsManager.instance()


def _general_on_disk(sm: SettingsManager) -> dict:
    return json.loads(sm.settings_path.read_text(encoding="utf-8"))["general"]


def test_the_defaults_leave_quicksell_off_and_once_on_sell_drops_and_pets_and_keep_wrenches_and_keys():
    assert DEFAULT_FLAGS == SellFlags(
        enabled=False, sell_pets=True, sell_socket_wrenches=False, sell_skeleton_keys=False
    )


def test_a_fresh_install_reads_the_defaults(sm):
    assert current_flags(sm) == DEFAULT_FLAGS
    assert current_keep_list(sm) == []


@pytest.mark.parametrize("flags", [SellFlags(True, False, True, False), SellFlags(False, True, False, True)])
def test_recording_writes_the_switches_to_disk(sm, flags):
    assert record_quicksell(sm, flags, [])

    general = _general_on_disk(sm)
    assert general["default_quicksell_enabled"] is flags.enabled
    assert general["default_quicksell_sell_pets"] is flags.sell_pets
    assert general["default_quicksell_sell_socket_wrenches"] is flags.sell_socket_wrenches
    assert general["default_quicksell_sell_skeleton_keys"] is flags.sell_skeleton_keys


def test_recording_writes_the_keep_list_turns_the_explanation_off_and_marks_the_page(sm):
    assert sm.show_quicksell_explanation

    record_quicksell(sm, DEFAULT_FLAGS, ["Amber", "No Pain, No Gain Onyx"])

    general = _general_on_disk(sm)
    assert general["quicksell_keep_list"] == ["Amber", "No Pain, No Gain Onyx"]
    assert general["show_quicksell_explanation"] is False
    assert general[ANSWERED_KEY] is True


def test_a_failed_save_says_so(sm, monkeypatch):
    def _fail():
        raise OSError("disk full")

    monkeypatch.setattr(sm, "save", _fail)

    assert record_quicksell(sm, DEFAULT_FLAGS, []) is False


def test_a_fresh_install_has_not_answered(sm):
    assert not quicksell_answered(sm)


def test_the_key_answers_the_page(sm):
    sm.set_general(ANSWERED_KEY, True)

    assert quicksell_answered(sm)


@pytest.mark.parametrize(
    "change",
    [
        lambda sm: write_sell_flags(sm, SellFlags(True, True, False, False)),
        lambda sm: write_sell_flags(sm, SellFlags(False, False, False, False)),
        lambda sm: write_sell_flags(sm, SellFlags(False, True, True, False)),
        lambda sm: write_sell_flags(sm, SellFlags(False, True, False, True)),
        lambda sm: write_keep_list(sm, ["Amber"]),
        lambda sm: setattr(sm, "show_quicksell_explanation", False),
    ],
    ids=["on", "keep-pets", "sell-wrenches", "sell-keys", "keep-list", "explanation-off"],
)
def test_a_quicksell_setting_off_its_default_answers_the_page(sm, change):
    change(sm)

    assert quicksell_answered(sm)
