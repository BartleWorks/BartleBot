"""The game's key bindings: what an action is bound to, and the key code that presses it.

Since r811339 a player can rebind keys in game. A binding names its action (``NPCInteract``)
and its input as text (``keyboard.down.ctrl.shift.q``), and the bindings belong to the
account: each client holds its own account's in memory, which ``clients/game_keys.py`` reads.
This module is the part that needs no client: the names, the actions BartleBot presses, and
which of an action's bindings a press can use. ``docs/KEY_BINDINGS.md`` has the whole story.

**How the game names a key.** A key in its table of names (``KEY_CODES``) is written by that
name; any other is written as its own virtual-key code, one character, a letter lowercased:
``x`` is VK_X (0x58), ``;`` is the byte 0xBA, and the Delete key is ``.``, its code 0x2E, not
the table's ``delete``. The numpad keys all have names, since their codes (0x60-0x6F) would
otherwise read as a backquote and the letters a to o. Every name below marked *in game* was bound by hand
on r811339 and pressed with the code given (``tools/probe_key_bindings.py``).

**Only a bare keyboard key can be pressed.** BartleBot sends a key to a client's window as
messages, and a modifier sent that way never reaches the game: Ctrl+0 was tried three ways
and none worked. An action bound to Ctrl+X, or to a mouse button, has to have a bare key in
its other slot.

**A key on two actions fires both.** The game lets one key sit on two actions; pressing it
does both. Most pairs are harmless to a run, but not one with the chat line, which takes
every key after it as text (``CHAT_LINE_ACTIONS``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

PREFIX = "keyboard.down."
MODIFIERS = ("alt", "ctrl", "shift")
# The binding files are not UTF-8: an unnamed key is its raw code, one byte, such as 0xBA.
ENCODING = "latin-1"

# The game's own names for keys, from its table in Bin/WizardGraphicalClient.exe, and the
# virtual-key code each one means.
KEY_CODES: dict[str, int] = {
    "escape": 0x1B,
    "enter": 0x0D,
    "tab": 0x09,
    "backspace": 0x08,  # in game
    "delete": 0x2E,  # the Delete key itself is written "." (its code); which key gets this name is unknown
    "space": 0x20,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "insert": 0x2D,  # in game
    "printscreen": 0x2C,
    "break": 0x13,  # in game; 0x03 did nothing
    **{f"f{n}": 0x6F + n for n in range(1, 13)},  # f5 in game
    **{f"num{n}": 0x60 + n for n in range(10)},
    "tilde": 0xC0,  # in game: the ` key
    "add": 0x6B,  # in game: numpad +
    "numlock": 0x90,  # in game
    "multiply": 0x6A,  # in game: numpad *
    "divide": 0x6F,  # in game: numpad /
    "separator": 0x6C,
    "subtract": 0x6D,  # in game: numpad -
    "question": 0xBF,  # the / key: it fired both times, but alongside the chat line it shares
    # SWAPPED on r811339: the game calls the "." key "comma" and the "," key "period", and each
    # name presses the key it was recorded from. Recheck on the live build before
    # ahmarra-test-realm merges (docs/GAME_UPDATES.md); a fix there is these two lines.
    "comma": 0xBE,  # in game
    "period": 0xBC,  # in game
    "equal": 0xBB,  # in game
    "lsqbracket": 0xDB,  # in game
    "rsqbracket": 0xDD,  # in game
    "pipe": 0xDC,  # in game: the \ key; 0xE2 did nothing
}


class GameAction(StrEnum):
    """Every action BartleBot presses, by the game's name for it. A run needs every one."""

    INTERACT = "NPCInteract"
    BACKPACK = "ToggleInventory"
    JEWELS = "ToggleJewelInventory"
    CHARACTER_SHEET = "ToggleCharStats"
    DECK = "ToggleDeckConfig"
    HOUSING = "ToggleFurniture"
    FRIENDS = "DisplayNewBuddyList"
    MOVE_LEFT = "MoveLeft"


# What the game's key bindings page calls each action (Root.wad, Locale/en-US/InputActions.lang).
ACTION_LABELS: dict[str, str] = {
    GameAction.INTERACT: "Interact",
    GameAction.BACKPACK: "Backpack",
    GameAction.JEWELS: "Open Jewels",
    GameAction.CHARACTER_SHEET: "Character Sheet",
    GameAction.DECK: "Deck",
    GameAction.HOUSING: "Housing",
    GameAction.FRIENDS: "Friends",
    GameAction.MOVE_LEFT: "Move Left",
    "ToggleChatBoxInputEnter": "Open Chat",
    "ToggleChatBoxInputSlash": "Open Chat",
}

# The shipped defaults for the actions BartleBot presses: what a client is assumed to have
# when its own bindings cannot be read.
DEFAULT_KEYS: dict[GameAction, str] = {
    GameAction.INTERACT: "x",
    GameAction.BACKPACK: "b",
    GameAction.JEWELS: "y",
    GameAction.CHARACTER_SHEET: "c",
    GameAction.DECK: "p",
    GameAction.HOUSING: "u",
    GameAction.FRIENDS: "f",
    GameAction.MOVE_LEFT: "a",
}

# Actions that open the chat line, which then takes every key pressed after it as text.
CHAT_LINE_ACTIONS = frozenset({"ToggleChatBoxInputEnter", "ToggleChatBoxInputSlash"})


def action_label(action: str) -> str:
    """An action as the game's key bindings page names it, or its name split into words."""
    return ACTION_LABELS.get(action) or re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", action)


@dataclass(frozen=True)
class KeyInput:
    """A keyboard input: its modifiers, in the game's order, and the key's name."""

    modifiers: tuple[str, ...]
    key: str

    @property
    def code(self) -> int | None:
        """The virtual-key code of the key alone, or None for a name we cannot turn into one."""
        return key_code(self.key)

    @property
    def text(self) -> str:
        """For people: ``Ctrl+Shift+Q``, ``.`` ``0xBA``. The game's own name where it has one."""
        if len(self.key) == 1:
            key = self.key.upper() if self.key.isascii() and self.key.isprintable() else f"0x{ord(self.key):02X}"
        else:
            key = self.key.capitalize() if not re.fullmatch(r"f\d+", self.key) else self.key.upper()
        return "+".join([*(m.capitalize() for m in self.modifiers), key])


def parse_input(text: str) -> KeyInput | None:
    """A binding's ``m_input`` as a keyboard press, or None for a mouse or other input.

    The key comes after the modifiers and may itself be ".", as the Delete key's is
    (``keyboard.down..``), so the text is not split on dots.
    """
    if not text.startswith(PREFIX):
        return None
    rest = text[len(PREFIX) :]
    modifiers: list[str] = []
    while True:
        for modifier in MODIFIERS:
            if rest.startswith(modifier + ".") and len(rest) > len(modifier) + 1:
                modifiers.append(modifier)
                rest = rest[len(modifier) + 1 :]
                break
        else:
            break
    return KeyInput(tuple(modifiers), rest) if rest else None


def key_code(name: str) -> int | None:
    """The virtual-key code the game means by a key's name, or None when it is not one we know.

    A one-character name is the key's own code, a letter lowercased. Characters from 0x60 to
    0x7F other than the letters would be numpad and function keys, which the game names, so
    one of those is not a code it writes.
    """
    if name in KEY_CODES:
        return KEY_CODES[name]
    if len(name) != 1:
        return None
    if "a" <= name <= "z":
        return ord(name.upper())
    if 0x60 <= ord(name) <= 0x7F or ord(name) < 0x08:
        return None
    return ord(name)


@dataclass(frozen=True)
class Binding:
    """One of an action's bindings: slot 0 is its first key, slot 1 its second."""

    action: str
    input: str
    slot: int = 0


@dataclass(frozen=True)
class Choice:
    """The key a press of an action uses, or why it has none."""

    action: str
    code: int | None
    input: KeyInput | None = None
    problem: str = ""
    shared_with: tuple[str, ...] = ()
    """Other actions on the same key, which a press of it also does. Never a chat-line action."""


class KeyMap:
    """A client's bindings, and the key to press for each action."""

    def __init__(self, bindings: Iterable[Binding], *, source: str) -> None:
        self.bindings = tuple(bindings)
        self.source = source
        """Where these came from: "memory", or "defaults" when the client's could not be read."""

    @classmethod
    def defaults(cls) -> KeyMap:
        return cls((Binding(action, PREFIX + key) for action, key in DEFAULT_KEYS.items()), source="defaults")

    def inputs(self, action: str) -> list[str]:
        """The action's inputs, first slot first."""
        return [b.input for b in sorted(self.bindings, key=lambda b: b.slot) if b.action == action]

    def choose(self, action: str) -> Choice:
        """The key to press for *action*: its first bare keyboard key that no chat-line action shares.

        A slot it cannot use is passed over for the other, so an action on Ctrl+X with a bare
        second key still has a key. ``problem`` says why when neither slot will do.
        """
        inputs = self.inputs(action)
        if not inputs:
            return Choice(action, None, problem="is not bound to any key")
        reasons: list[str] = []
        for text in inputs:
            parsed = parse_input(text)
            if parsed is None:
                reasons.append(f"{text.split('.', 1)[0] or text} input")
                continue
            if parsed.modifiers:
                reasons.append(f"{parsed.text}, which BartleBot cannot press: a key with Ctrl, Shift or Alt")
                continue
            if parsed.code is None:
                reasons.append(f"{parsed.text}, a key BartleBot does not know")
                continue
            others = sorted({b.action for b in self.bindings if b.input == text and b.action != action})
            chat = [o for o in others if o in CHAT_LINE_ACTIONS]
            if chat:
                reasons.append(f"{parsed.text}, which also opens the chat line ({action_label(chat[0])})")
                continue
            return Choice(action, parsed.code, parsed, shared_with=tuple(others))
        return Choice(action, None, problem="is bound only to " + " and ".join(reasons))

    def problems(self, actions: Iterable[str] = tuple(GameAction)) -> list[Choice]:
        """The actions among *actions* that have no key a press can use."""
        return [choice for choice in map(self.choose, actions) if choice.code is None]


def parse_binding_file(text: str) -> list[Binding]:
    """The bindings in an ``InputBindingList`` file, ``InputBindings.xml`` or ``LocalInputBindings.xml``.

    Read with a regex, like every other read of the game's own files, since the file is the
    game's and is never rewritten here. Live's older form has no ``m_slot``: slot 0.
    """
    entries = re.findall(
        r"<m_inputs>\s*<Class Name=\"class InputBinding\">(.*?)</Class>\s*</m_inputs>", text, re.DOTALL
    )
    bindings = []
    for body in entries:
        fields = dict(re.findall(r"<(m_\w+)>([^<]*)</\1>", body))
        if fields.get("m_action") and fields.get("m_input"):
            slot = fields.get("m_slot", "0")
            bindings.append(Binding(fields["m_action"], fields["m_input"], int(slot) if slot.isdigit() else 0))
    return bindings
