"""The authoring hotkeys (#81): binding syntax, arming, and dispatch of WM_HOTKEY."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QByteArray

from bartlebot.authoring_tools import AuthoringAction
from bartlebot.gui.game_embed.game_view_panel import authoring_enabled, authoring_tooltip
from bartlebot.gui.global_hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    WM_HOTKEY,
    GlobalHotkeys,
    parse_binding,
)
from bartlebot.gui.settings.authoring_tab import binding_problems
from bartlebot.settings_manager import DEFAULT_AUTHORING_HOTKEYS, SettingsManager


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("F1", (0, 0x70)),
        ("F24", (0, 0x87)),
        ("Shift+F2", (MOD_SHIFT, 0x71)),
        ("Ctrl+Alt+K", (MOD_CONTROL | MOD_ALT, ord("K"))),
        ("8", (0, ord("8"))),
        ("", None),
        ("Space", None),
        ("F25", None),
        ("Meta+F1", None),
    ],
)
def test_a_binding_parses_to_its_modifiers_and_virtual_key(text, expected):
    assert parse_binding(text) == expected


def _hotkeys(on_hotkey=None) -> tuple[GlobalHotkeys, MagicMock]:
    hk = GlobalHotkeys(on_hotkey or (lambda _a: None))
    user32 = MagicMock()
    user32.RegisterHotKey.return_value = 1
    hk._user32 = user32
    return hk, user32


def test_keys_are_registered_only_while_armed(qapp):
    hk, user32 = _hotkeys()
    hk.set_bindings({"xyz_sync": "F1", "toggle_speed": ""})
    user32.RegisterHotKey.assert_not_called()

    hk.set_armed(True)
    assert hk.registered_actions == ["xyz_sync"]
    _hwnd, _id, modifiers, vk = user32.RegisterHotKey.call_args.args
    assert modifiers == MOD_NOREPEAT and vk == 0x70

    hk.set_armed(False)
    assert hk.registered_actions == []
    user32.UnregisterHotKey.assert_called_once()


def test_a_key_another_application_holds_is_skipped(qapp):
    hk, user32 = _hotkeys()
    user32.RegisterHotKey.return_value = 0
    hk.set_bindings({"xyz_sync": "F1"})
    hk.set_armed(True)
    assert hk.registered_actions == []


def test_a_refused_key_is_reported_as_held_elsewhere(qapp, caplog):
    hk, user32 = _hotkeys()
    user32.RegisterHotKey.return_value = 0
    hk.set_bindings({"xyz_sync": "F1"})
    hk.set_armed(True)
    assert "xyz_sync is already registered elsewhere" in caplog.text


def test_two_actions_on_one_key_are_reported_as_our_own_clash(qapp, caplog):
    """Windows refuses the second one exactly as it refuses another application's key, so
    the clash has to be caught before asking it. The alphabetically first action keeps it."""
    hk, user32 = _hotkeys()
    hk.set_bindings({"toggle_speed_authoring": "F5", "toggle_dialogue": "F5", "xyz_sync": "F1"})
    hk.set_armed(True)

    assert hk.registered_actions == ["toggle_dialogue", "xyz_sync"]
    assert user32.RegisterHotKey.call_count == 2
    assert (
        "The hotkeys for toggle_dialogue and toggle_speed_authoring are the same key; only toggle_dialogue gets it"
        in caplog.text
    )
    assert "elsewhere" not in caplog.text

    # Once a session, not on every focus change.
    hk.set_armed(False)
    hk.set_armed(True)
    assert caplog.text.count("are the same key") == 1


def test_an_unbindable_key_is_reported_and_left_out(qapp):
    hk, _user32 = _hotkeys()
    assert hk.set_bindings({"xyz_sync": "Space", "toggle_speed": "F3"}) == ["xyz_sync"]
    hk.set_armed(True)
    assert hk.registered_actions == ["toggle_speed"]


def test_rebinding_while_armed_registers_the_new_keys(qapp):
    hk, user32 = _hotkeys()
    hk.set_bindings({"xyz_sync": "F1"})
    hk.set_armed(True)
    hk.set_bindings({"xyz_sync": "F5"})
    assert user32.RegisterHotKey.call_args.args[3] == 0x74
    assert hk.registered_actions == ["xyz_sync"]


def _message(message: int, w_param: int) -> ctypes.wintypes.MSG:
    msg = ctypes.wintypes.MSG()
    msg.message = message
    msg.wParam = w_param
    return msg


def test_a_hotkey_message_fires_its_action_and_is_consumed(qapp):
    pressed = []
    hk, user32 = _hotkeys(pressed.append)
    hk.set_bindings({"toggle_combat": "F4"})
    hk.set_armed(True)
    hotkey_id = user32.RegisterHotKey.call_args.args[1]

    msg = _message(WM_HOTKEY, hotkey_id)
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(msg)) == (True, 0)
    assert pressed == ["toggle_combat"]

    other = _message(0x0100, hotkey_id)  # WM_KEYDOWN
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(other)) == (False, 0)
    assert pressed == ["toggle_combat"]


def test_a_handler_that_raises_does_not_escape_the_filter(qapp):
    def boom(_action):
        raise RuntimeError("handler")

    hk, user32 = _hotkeys(boom)
    hk.set_bindings({"toggle_combat": "F4"})
    hk.set_armed(True)
    msg = _message(WM_HOTKEY, user32.RegisterHotKey.call_args.args[1])
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(msg)) == (True, 0)


# ---------------------------------------------------------------------------
# The settings behind them, and Bar A
# ---------------------------------------------------------------------------


def test_a_binding_problem_names_the_action():
    assert binding_problems({"xyz_sync": "F1", "tp_to_focused": "F2"}) == []
    assert binding_problems({"xyz_sync": "Space"}) == [
        "XYZ Sync: Space cannot be bound — use F1–F24, a letter or a digit."
    ]
    assert binding_problems({"xyz_sync": "F1", "toggle_speed": "F1"}) == ["Speed and XYZ Sync are both on F1."]


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
    assert authoring_tooltip(AuthoringAction.XYZ_SYNC, "").startswith("XYZ Sync — ")


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
