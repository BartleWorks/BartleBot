"""The game's own key bindings, and warning when an authoring hotkey lands on one."""

from __future__ import annotations

import pytest
from bartleby.runtime.game.key_bindings import Binding, KeyMap, parse_binding_file
from bartleby.shared import game_install

from bartlebot.game import game_keybindings
from bartlebot.game.game_keybindings import GameBinding, game_bindings, read_binding_files, read_game_keybindings
from bartlebot.gui.settings.authoring_tab import binding_problems


def _entry(action: str, key: str, slot: int = 0) -> str:
    return f"""
        <m_inputs>
            <Class Name="class InputBinding">
                <m_action>{action}</m_action>
                <m_input>{key}</m_input>
                <m_managerType>KBM</m_managerType>
                <m_rebindable>1</m_rebindable>
                <m_version>0</m_version>
                <m_slot>{slot}</m_slot>
            </Class>
        </m_inputs>"""


def _file(*entries: str) -> str:
    return '<Objects>\n    <Class Name="class InputBindingList">' + "".join(entries) + "\n    </Class>\n</Objects>\n"


# Shaped like the game's InputBindings.xml: keyboard bindings with and without modifiers, a
# second key, and a mouse binding.
_DEFAULTS = _file(
    _entry("Quit", "keyboard.down.alt.f4"),
    _entry("FindNearestActiveQuest", "keyboard.down.ctrl.shift.q"),
    _entry("PrintScreen", "keyboard.down.printscreen"),
    _entry("PrintScreen", "keyboard.down.f12", slot=1),
    _entry("CameraZoom", "mouse.wheel"),
)


def test_the_keyboard_bindings_are_read_with_their_modifiers_and_the_rest_skipped():
    assert game_bindings(parse_binding_file(_DEFAULTS)) == [
        GameBinding(event="Quit", binding="Alt+F4"),
        GameBinding(event="FindNearestActiveQuest", binding="Ctrl+Shift+Q"),
        GameBinding(event="PrintScreen", binding="Print Screen"),
        GameBinding(event="PrintScreen", binding="F12"),
    ]


def test_an_event_name_reads_as_words():
    assert GameBinding(event="DisplayNewBuddyList", binding="F").description == "Display New Buddy List"


def test_the_clients_own_bindings_are_used_when_there_are_any(monkeypatch):
    monkeypatch.setattr(game_install, "find_install", pytest.fail)  # never consulted
    one = KeyMap([Binding("NPCInteract", "keyboard.down.k")], source="memory")
    two = KeyMap(
        [Binding("NPCInteract", "keyboard.down.x"), Binding("NPCInteract", "keyboard.down.k")], source="memory"
    )
    assert read_game_keybindings([one, two]) == [
        GameBinding(event="NPCInteract", binding="K"),
        GameBinding(event="NPCInteract", binding="X"),
    ]


def _install(tmp_path, **files: str):
    data = tmp_path.joinpath(*game_keybindings.GAME_DATA)
    data.mkdir(parents=True)
    for name, text in files.items():
        (data / f"{name}.xml").write_bytes(text.encode("latin-1"))
    return tmp_path


def test_with_no_client_read_the_last_saved_bindings_come_before_the_defaults(tmp_path):
    install = _install(
        tmp_path,
        LocalInputBindings=_file(_entry("NPCInteract", "keyboard.down.k")),
        InputBindings=_file(_entry("NPCInteract", "keyboard.down.x")),
    )
    assert read_binding_files(install) == [Binding("NPCInteract", "keyboard.down.k")]


def test_with_no_saved_bindings_the_defaults_are_read(tmp_path):
    install = _install(tmp_path, InputBindings=_file(_entry("NPCInteract", "keyboard.down.x")))
    assert read_binding_files(install) == [Binding("NPCInteract", "keyboard.down.x")]


def test_an_install_with_neither_file_or_none_found_gives_no_bindings(tmp_path, monkeypatch):
    assert read_binding_files(_install(tmp_path)) == []
    monkeypatch.setattr(game_install, "find_install", lambda _prefs: None)
    assert read_game_keybindings() == []


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
