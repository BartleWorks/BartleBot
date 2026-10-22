"""Reading the game's own key bindings, and warning when an authoring hotkey lands on one."""

from __future__ import annotations

import wizwalker.utils

from bartlebot.game.game_keybindings import GameBinding, parse_input_mappings, read_game_keybindings
from bartlebot.gui.settings.authoring_tab import binding_problems

# Shaped like the game's config.xml: a section before, then InputMappings with a mix of
# keyboard and mouse records.
_CONFIG = """<GameSettings>
  <RECORD>
    <Locale TYPE="INT">0</Locale>
  </RECORD>
</GameSettings>
<InputMappings>
  <RECORD>
    <Alt TYPE="BYT">1</Alt>
    <Ctrl TYPE="BYT">0</Ctrl>
    <Event TYPE="STR">Quit</Event>
    <InputType TYPE="STR">keyboard</InputType>
    <Key TYPE="STR">F4</Key>
    <KeyState TYPE="STR">down</KeyState>
    <Shift TYPE="BYT">0</Shift>
  </RECORD>
  <RECORD>
    <Alt TYPE="BYT">0</Alt>
    <Ctrl TYPE="BYT">1</Ctrl>
    <Event TYPE="STR">FindNearestActiveQuest</Event>
    <InputType TYPE="STR">keyboard</InputType>
    <Key TYPE="STR">q</Key>
    <KeyState TYPE="STR">up</KeyState>
    <Shift TYPE="BYT">1</Shift>
  </RECORD>
  <RECORD>
    <Alt TYPE="BYT">0</Alt>
    <Ctrl TYPE="BYT">0</Ctrl>
    <Event TYPE="STR">PrintScreen</Event>
    <InputType TYPE="STR">keyboard</InputType>
    <Key TYPE="STR">F12</Key>
    <KeyState TYPE="STR">up</KeyState>
    <Shift TYPE="BYT">0</Shift>
  </RECORD>
  <RECORD>
    <Event TYPE="STR">CameraRotate</Event>
    <InputType TYPE="STR">mouse</InputType>
    <Key TYPE="STR">RightButton</Key>
  </RECORD>
</InputMappings>
"""


def test_the_keyboard_records_are_read_with_their_modifiers_and_the_rest_skipped():
    assert parse_input_mappings(_CONFIG) == [
        GameBinding(event="Quit", binding="Alt+F4"),
        GameBinding(event="FindNearestActiveQuest", binding="Ctrl+Shift+q"),
        GameBinding(event="PrintScreen", binding="F12"),
    ]


def test_a_file_with_no_input_mappings_has_no_bindings():
    assert parse_input_mappings("<GameSettings></GameSettings>") == []


def test_an_event_name_reads_as_words():
    assert GameBinding(event="DisplayNewBuddyList", binding="F").description == "Display New Buddy List"


def test_an_unreadable_install_gives_no_bindings_rather_than_an_error(monkeypatch):
    def missing():
        raise FileNotFoundError("no install")

    monkeypatch.setattr(wizwalker.utils, "get_wiz_install", missing)
    assert read_game_keybindings() == []


def test_a_hotkey_on_a_game_key_is_flagged_and_its_modifiers_matter():
    game = parse_input_mappings(_CONFIG)
    # The defaults: F4 is free, since the game's F4 binding is Alt+F4.
    assert binding_problems({"xyz_sync": "F1", "toggle_combat": "F4"}, game) == []
    assert binding_problems({"toggle_speed": "F12"}, game) == [
        "Speed: F12 is the game's key for Print Screen, which won't work in the game while this is set."
    ]
    assert binding_problems({"toggle_speed": "Shift+Ctrl+Q"}, game)[0].startswith(
        "Speed: Shift+Ctrl+Q is the game's key for Find Nearest Active Quest"
    )
