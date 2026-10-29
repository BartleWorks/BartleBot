"""The authoring hotkeys (#81): binding syntax, arming, and dispatch of WM_HOTKEY."""

from __future__ import annotations

import pytest

from bartlebot.clients.authoring_tools import AuthoringAction
from bartlebot.gui.game_embed.game_view_panel import authoring_enabled, authoring_tooltip
from bartlebot.gui.settings.authoring_tab import binding_problems
from bartlebot.settings_manager import DEFAULT_AUTHORING_HOTKEYS, SettingsManager

# ---------------------------------------------------------------------------
# The settings behind them, and Bar A
# ---------------------------------------------------------------------------


def test_a_binding_problem_names_the_action():
    assert binding_problems({"xyz_sync": "F1", "tp_to_focused": "F2"}) == []
    assert binding_problems({"xyz_sync": "Space"}) == [
        "XYZ sync: Space can't be used. Choose F1–F24, a letter or a digit."
    ]
    assert binding_problems({"xyz_sync": "F1", "toggle_speed": "F1"}) == [
        "Speed and XYZ sync both use F1. Choose another key for one of them."
    ]


def test_the_hotkey_settings_fill_in_an_action_the_file_does_not_mention():
    sm = SettingsManager.instance()
    sm.set("authoring", "hotkeys", value={"xyz_sync": "F9"})
    assert sm.authoring_hotkeys == {
        "xyz_sync": "F9",
        "tp_to_focused": "F2",
        "toggle_speed": "F3",
        "toggle_combat": "F4",
        "toggle_dialogue": "F5",
        "toggle_speed_authoring": "F6",
    }
    sm.set_authoring_hotkey("toggle_combat", "")
    assert sm.authoring_hotkeys["toggle_combat"] == ""


@pytest.mark.parametrize(("stored", "read"), [(5.0, 5.0), (0.2, 1.0), (99, 10.0), ("fast", 5.0)])
def test_the_speed_multiplier_is_clamped_to_its_range(stored, read):
    sm = SettingsManager.instance()
    sm.set("authoring", "speed_multiplier", value=stored)
    assert sm.speed_multiplier == read


def test_the_buttons_are_off_during_a_run_and_the_moves_need_two_players():
    for action in AuthoringAction:
        assert not authoring_enabled(action, clients=4, run_active=True, busy=False)
    assert not authoring_enabled(AuthoringAction.XYZ_SYNC, clients=1, run_active=False, busy=False)
    assert not authoring_enabled(AuthoringAction.TP_TO_FOCUSED, clients=4, run_active=False, busy=True)
    assert authoring_enabled(AuthoringAction.TOGGLE_SPEED, clients=1, run_active=False, busy=True)
    assert not authoring_enabled(AuthoringAction.TOGGLE_COMBAT, clients=0, run_active=False, busy=False)


def test_a_tooltip_names_the_button_its_key_and_whether_it_is_on():
    assert authoring_tooltip(AuthoringAction.TOGGLE_SPEED, "F3", on=True).startswith("Speed (on) [F3] — ")
    assert authoring_tooltip(AuthoringAction.XYZ_SYNC, "").startswith("XYZ sync — ")


def test_only_a_key_that_differs_from_its_default_is_stored():
    """Applying the Authoring tab writes every row; a default must not be frozen by it, or a
    renumbered default never reaches the user -- speed-authoring stayed on F5 that way."""
    sm = SettingsManager.instance()
    sm.set("authoring", "hotkeys", value={})
    for action, binding in DEFAULT_AUTHORING_HOTKEYS.items():
        sm.set_authoring_hotkey(action, binding)
    sm.set_authoring_hotkey("xyz_sync", "F9")
    sm.set_authoring_hotkey("toggle_combat", "")

    assert sm.get("authoring", "hotkeys") == {"xyz_sync": "F9", "toggle_combat": ""}


def test_a_key_set_back_to_its_default_is_dropped_from_the_file():
    sm = SettingsManager.instance()
    sm.set("authoring", "hotkeys", value={"toggle_speed_authoring": "F5", "xyz_sync": "F9"})
    sm.set_authoring_hotkey("toggle_speed_authoring", DEFAULT_AUTHORING_HOTKEYS["toggle_speed_authoring"])

    assert sm.get("authoring", "hotkeys") == {"xyz_sync": "F9"}
    assert sm.authoring_hotkeys["toggle_speed_authoring"] == "F6"
