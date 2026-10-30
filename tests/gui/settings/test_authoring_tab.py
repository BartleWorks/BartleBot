"""The Authoring tab's hotkeys, checked against the game's own keys (#81).

Core holds the rules (``bartleby.qt.global_hotkeys.binding_problems``) since Stage 11 of
#167; this is BartleBot's list of actions and their labels on them.
"""

from __future__ import annotations

from bartleby.runtime.game.game_keybindings import game_bindings
from bartleby.runtime.game.key_bindings import parse_binding_file

from bartlebot.gui.settings.authoring_tab import binding_problems

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


def test_a_hotkey_on_a_game_key_is_flagged_and_its_modifiers_matter():
    game = game_bindings(parse_binding_file(_DEFAULTS))
    # The defaults: F4 is free, since the game's F4 binding is Alt+F4.
    assert binding_problems({"xyz_sync": "F1", "toggle_combat": "F4"}, game) == []
    assert binding_problems({"toggle_speed": "F12"}, game) == [
        "Speed: F12 is the game's key for Print Screen, which won't work in the game while this is set."
    ]
    assert binding_problems({"toggle_speed": "Shift+Ctrl+Q"}, game)[0].startswith(
        "Speed: Shift+Ctrl+Q is the game's key for Find Nearest Active Quest"
    )
