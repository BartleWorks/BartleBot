"""The Authoring tab's hotkeys, checked against the game's own keys (#81).

Core holds the rules (``bartleby.qt.global_hotkeys.binding_problems``) since Stage 11 of
#167; this is BartleBot's list of actions and their labels on them.
"""

from __future__ import annotations

import json

import pytest
from bartleby.qt.settings.rows import SettingRow
from bartleby.runtime.game.game_keybindings import game_bindings
from bartleby.runtime.game.key_bindings import parse_binding_file

from bartlebot.gui.settings.authoring_tab import AuthoringTab, binding_problems
from bartlebot.settings_manager import SettingsManager

_DEFAULTS = """<Objects>
    <Class Name="class InputBindingList">
        <m_inputs>
            <Class Name="class InputBinding">
                <m_action>Quit</m_action>
                <m_input>keyboard.down.alt.f4</m_input>
                <m_managerType>KBM</m_managerType>
                <m_rebindable>1</m_rebindable>
                <m_version>0</m_version>
                <m_slot>0</m_slot>
            </Class>
        </m_inputs>
        <m_inputs>
            <Class Name="class InputBinding">
                <m_action>FindNearestActiveQuest</m_action>
                <m_input>keyboard.down.ctrl.shift.q</m_input>
                <m_managerType>KBM</m_managerType>
                <m_rebindable>1</m_rebindable>
                <m_version>0</m_version>
                <m_slot>0</m_slot>
            </Class>
        </m_inputs>
        <m_inputs>
            <Class Name="class InputBinding">
                <m_action>PrintScreen</m_action>
                <m_input>keyboard.down.f12</m_input>
                <m_managerType>KBM</m_managerType>
                <m_rebindable>1</m_rebindable>
                <m_version>0</m_version>
                <m_slot>1</m_slot>
            </Class>
        </m_inputs>
    </Class>
</Objects>
"""


@pytest.mark.usefixtures("speed_enabled")
def test_a_hotkey_on_a_game_key_is_flagged_and_its_modifiers_matter():
    game = game_bindings(parse_binding_file(_DEFAULTS))
    # The defaults: F4 is free, since the game's F4 binding is Alt+F4.
    assert binding_problems({"tp_to_focused": "F2", "toggle_combat": "F4"}, game) == []
    assert binding_problems({"toggle_speed": "F12"}, game) == [
        "Speed: F12 is the game's key for Print Screen, which won't work in the game while this is set."
    ]
    assert binding_problems({"toggle_speed": "Shift+Ctrl+Q"}, game)[0].startswith(
        "Speed: Shift+Ctrl+Q is the game's key for Find Nearest Active Quest"
    )


# ---------------------------------------------------------------------------
# Speed switched off (Stage 0 of #322)
# ---------------------------------------------------------------------------


def _row_titles(tab) -> list[str]:
    return [row.title for row in tab.findChildren(SettingRow)]


def test_switched_off_speed_has_no_rows_on_the_authoring_tab(qapp):
    titles = _row_titles(AuthoringTab())
    assert "Speed multiplier" not in titles
    assert "Speed" not in titles
    assert "Auto-combat" in titles


@pytest.mark.usefixtures("speed_enabled")
def test_switched_on_speed_has_its_rows_back_on_the_authoring_tab(qapp):
    titles = _row_titles(AuthoringTab())
    assert "Speed multiplier" in titles
    assert "Speed" in titles


def test_applying_the_authoring_tab_leaves_switched_off_speed_s_saved_values_alone(qapp):
    """The file keeps them as the user left them, for the day Speed is switched back on."""
    sm = SettingsManager.instance()
    sm.speed_multiplier = 1.5
    sm.set_authoring_hotkey("toggle_speed", "F9")
    sm.save()

    tab = AuthoringTab()
    tab.apply()
    sm.save()

    saved = json.loads(sm.settings_path.read_text(encoding="utf-8"))["authoring"]
    assert saved["speed_multiplier"] == 1.5
    assert saved["hotkeys"]["toggle_speed"] == "F9"
    assert sm.authoring_hotkeys["toggle_speed"] == "F9"
