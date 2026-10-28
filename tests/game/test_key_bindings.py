"""The game's key names, and choosing the key that presses an action."""

from __future__ import annotations

from bartlebot.game.key_bindings import (
    PREFIX,
    Binding,
    GameAction,
    KeyMap,
    action_label,
    key_code,
    key_label,
    parse_binding_file,
    parse_input,
)


def _map(*bindings: tuple[str, str, int]) -> KeyMap:
    return KeyMap((Binding(action, PREFIX + key, slot) for action, key, slot in bindings), source="memory")


def test_an_input_is_its_modifiers_then_its_key():
    parsed = parse_input("keyboard.down.ctrl.shift.q")
    assert (parsed.modifiers, parsed.key) == (("ctrl", "shift"), "q")
    assert parsed.text == "Ctrl+Shift+Q"


def test_a_key_named_by_a_dot_is_not_split_on_it():
    # The Delete key is written as its code, 0x2E, which is ".".
    parsed = parse_input("keyboard.down..")
    assert (parsed.modifiers, parsed.key, parsed.code) == ((), ".", 0x2E)


def test_a_mouse_input_is_not_a_key_press():
    assert parse_input("mouse.wheel") is None
    assert parse_input("mouse.button1.down") is None


def test_a_letter_or_digit_is_its_own_code():
    assert key_code("x") == 0x58
    assert key_code("7") == 0x37


def test_an_unnamed_key_is_its_raw_code():
    assert key_code("\xba") == 0xBA  # ;
    assert key_code("\xde") == 0xDE  # '
    assert key_code("\xbd") == 0xBD  # main-keyboard -


def test_the_names_found_in_game_press_the_keys_they_were_recorded_from():
    assert key_code("divide") == 0x6F
    assert key_code("tilde") == 0xC0
    assert key_code("pipe") == 0xDC
    assert key_code("break") == 0x13
    assert key_code("equal") == 0xBB


def test_comma_and_period_are_swapped_on_r811339():
    # The "." key is recorded as "comma" and the "," key as "period", and each presses the key
    # it was recorded from. A recheck before the branch merges (bartleby-core/docs/GAME_UPDATES.md).
    assert key_code("comma") == 0xBE
    assert key_code("period") == 0xBC


def test_a_character_where_the_numpad_codes_are_is_no_code_the_game_writes():
    assert key_code("`") is None
    assert key_code("bogus") is None


def test_an_action_presses_its_first_key():
    choice = _map(("NPCInteract", "k", 0), ("NPCInteract", "x", 1)).choose(GameAction.INTERACT)
    assert choice.code == ord("K")


def test_a_key_with_a_modifier_is_passed_over_for_a_bare_second_key():
    choice = _map(("NPCInteract", "ctrl.x", 0), ("NPCInteract", "j", 1)).choose(GameAction.INTERACT)
    assert choice.code == ord("J")


def test_an_action_with_only_modified_keys_cannot_be_pressed_and_says_why():
    choice = _map(("NPCInteract", "ctrl.x", 0), ("NPCInteract", "alt.j", 1)).choose(GameAction.INTERACT)
    assert choice.code is None
    assert choice.problem == "is on Ctrl+X and Alt+J"


def test_an_action_on_the_mouse_alone_cannot_be_pressed():
    keys = KeyMap([Binding("NPCInteract", "mouse.button3.down")], source="memory")
    assert keys.choose(GameAction.INTERACT).code is None


def test_an_unbound_action_cannot_be_pressed():
    choice = _map(("ToggleInventory", "b", 0)).choose(GameAction.INTERACT)
    assert choice.code is None
    assert choice.problem == "has no key"


def test_a_key_the_chat_line_shares_is_never_pressed():
    # The game lets "/" sit on the Housing page and on the chat line at once, and a press then
    # opens the chat line, which takes every key after it as text.
    keys = _map(("ToggleChatBoxInputSlash", "question", 0), ("ToggleFurniture", "question", 0))
    choice = keys.choose(GameAction.HOUSING)
    assert choice.code is None
    assert choice.problem == "is on /, which also opens the chat"


def test_a_shared_key_that_is_not_the_chat_line_is_pressed_and_named():
    keys = _map(("NPCInteract", "x", 0), ("ToggleMapWindow", "x", 0))
    choice = keys.choose(GameAction.INTERACT)
    assert choice.code == ord("X")
    assert choice.shared_with == ("ToggleMapWindow",)


def test_the_defaults_press_every_action():
    assert KeyMap.defaults().problems() == []
    assert KeyMap.defaults().choose(GameAction.DECK).code == ord("P")


def test_a_problem_is_reported_for_each_action_that_has_no_key():
    keys = KeyMap(
        [b for b in KeyMap.defaults().bindings if b.action != GameAction.FRIENDS]
        + [Binding("DisplayNewBuddyList", "keyboard.down.ctrl.f")],
        source="memory",
    )
    assert [choice.action for choice in keys.problems()] == [GameAction.FRIENDS]


def test_an_action_is_named_as_the_game_names_it():
    assert action_label(GameAction.INTERACT) == "Interact"
    assert action_label(GameAction.BACKPACK) == "Backpack"
    assert action_label("ToggleQuestHelper") == "Toggle Quest Helper"


_TEST_REALM_FILE = """<Objects>
    <Class Name="class InputBindingList">
        <m_inputs>
            <Class Name="class InputBinding">
                <m_action>MoveForward</m_action>
                <m_input>keyboard.down.up</m_input>
                <m_managerType>KBM</m_managerType>
                <m_rebindable>1</m_rebindable>
                <m_version>0</m_version>
                <m_slot>1</m_slot>
            </Class>
        </m_inputs>
        <m_inputs>
            <Class Name="class InputBinding">
                <m_action>EquipSet1</m_action>
                <m_input>keyboard.down.\xba</m_input>
                <m_managerType>KBM</m_managerType>
                <m_rebindable>1</m_rebindable>
                <m_version>0</m_version>
                <m_slot>0</m_slot>
            </Class>
        </m_inputs>
    </Class>
</Objects>
"""

_LIVE_FILE = """<Objects>
   <Class Name="class InputBindingList">
      <m_inputs>
         <Class Name="class InputBinding">
            <m_action>ToggleJewelInventory</m_action>
            <m_input>keyboard.down.y</m_input>
            <m_managerType>KBM</m_managerType>
            <m_rebindable>true</m_rebindable>
            <m_version>0</m_version>
         </Class>
      </m_inputs>
   </Class>
</Objects>
"""


def test_a_bindings_file_is_read_with_its_slots_and_raw_bytes():
    assert parse_binding_file(_TEST_REALM_FILE) == [
        Binding("MoveForward", "keyboard.down.up", 1),
        Binding("EquipSet1", "keyboard.down.\xba", 0),
    ]


def test_live_s_older_bindings_file_has_no_slot_and_reads_as_slot_0():
    assert parse_binding_file(_LIVE_FILE) == [Binding("ToggleJewelInventory", "keyboard.down.y", 0)]


def test_a_key_is_named_as_the_keyboard_labels_it():
    assert key_label("question") == "/"
    assert key_label("\xba") == ";"
    assert key_label("add") == "Numpad +"
    assert key_label("pageup") == "Page Up"
    assert key_label("f12") == "F12"
    assert key_label("x") == "X"
    assert key_label("7") == "7"
    assert key_label(".") == "Delete"


def test_the_swapped_names_read_as_the_keys_they_press():
    # r811339 names the "." key "comma": the user is shown the key they pressed.
    assert key_label("comma") == "."
    assert key_label("period") == ","
