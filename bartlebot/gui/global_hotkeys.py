"""System-wide hotkeys for the authoring utilities, armed only while a game window has focus.

A game window embedded in BartleBot belongs to another process, so its keystrokes never
reach Qt and a ``QShortcut`` cannot see them. ``RegisterHotKey`` can: Windows delivers
``WM_HOTKEY`` to the registering thread whichever window has the keyboard.

Two decisions follow from that key being system-wide:

* **Registered only while a game window holds the keyboard.** Registered any longer, F1
  would stop reaching every other application on the machine, and a binding on a digit
  would take the canvas's favourites keys and every text field in BartleBot with it.
  ``GameView.game_focus_changed`` says when; ``set_armed`` follows it.
* **Read through a Qt native event filter, not WizWalker's ``HotkeyListener``.** That
  listener takes ``WM_HOTKEY`` off the thread queue with its own ``PeekMessage`` loop, and
  under qasync Qt's dispatcher pumps the same queue first -- the message would be gone
  before the listener looked. Qt hands every message it pumps to its native filters.

A binding is a ``QKeySequence`` portable string of one key and its modifiers ("F1",
"Shift+F2", "Ctrl+8"). Only keys with a fixed virtual-key code are accepted -- F1-F24,
A-Z and 0-9 -- because the rest depend on the keyboard layout.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from collections.abc import Callable

from PyQt6.QtCore import QAbstractNativeEventFilter

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

_MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT}

# Ids an application may register, per the RegisterHotKey docs, are 0x0000-0xBFFF. These
# sit near the top so they stay clear of anything else in the process that registers low.
_ID_BASE = 0xB800

_VK_F1 = 0x70


def parse_binding(text: str) -> tuple[int, int] | None:
    """``(modifiers, virtual_key)`` for a binding like ``"Shift+F2"``, or None if it has none.

    None for an empty string (an unbound action) and for anything not in the fixed-code set.
    """
    parts = [p.strip() for p in (text or "").split("+")]
    if not parts or not parts[-1]:
        return None
    modifiers = 0
    for name in parts[:-1]:
        flag = _MODIFIERS.get(name.lower())
        if flag is None:
            return None
        modifiers |= flag
    vk = _virtual_key(parts[-1])
    return None if vk is None else (modifiers, vk)


def _virtual_key(key: str) -> int | None:
    upper = key.upper()
    if len(upper) == 1 and ("A" <= upper <= "Z" or "0" <= upper <= "9"):
        return ord(upper)
    if upper.startswith("F") and upper[1:].isdigit() and 1 <= int(upper[1:]) <= 24:
        return _VK_F1 + int(upper[1:]) - 1
    return None


class GlobalHotkeys(QAbstractNativeEventFilter):
    """Register a set of bindings while armed, and call *on_hotkey* with the action pressed.

    *on_hotkey* runs inside Qt's message dispatch, so it must return quickly: start a task,
    never await or open a dialog. It is guarded, since an exception escaping a native event
    filter takes the event loop with it.
    """

    def __init__(self, on_hotkey: Callable[[str], None]) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey
        self._bindings: dict[str, tuple[int, int]] = {}
        # Registered id -> action, while armed.
        self._registered: dict[int, str] = {}
        self._armed = False
        self._user32 = ctypes.windll.user32
        # Warned once per binding a session, not on every focus change: another tool
        # holding F1 (Deimos, say) would otherwise log a line every time a tile is clicked.
        self._warned: set[tuple[str, tuple[int, int]]] = set()

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def registered_actions(self) -> list[str]:
        return sorted(self._registered.values())

    def set_bindings(self, bindings: dict[str, str]) -> list[str]:
        """Replace every binding; returns the actions whose binding could not be understood."""
        parsed: dict[str, tuple[int, int]] = {}
        invalid: list[str] = []
        for action, text in bindings.items():
            if not text:
                continue
            combo = parse_binding(text)
            if combo is None:
                invalid.append(action)
            else:
                parsed[action] = combo
        for action in invalid:
            logger.warning("Hotkey for %s is %r, which is not a key BartleBot can bind", action, bindings[action])
        self._bindings = parsed
        if self._armed:
            self._unregister_all()
            self._register_all()
        return invalid

    def set_armed(self, armed: bool) -> None:
        if armed == self._armed:
            return
        self._armed = armed
        if armed:
            self._register_all()
        else:
            self._unregister_all()

    def _register_all(self) -> None:
        for offset, (action, (modifiers, vk)) in enumerate(sorted(self._bindings.items())):
            hotkey_id = _ID_BASE + offset
            if self._user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
                self._registered[hotkey_id] = action
            elif (action, (modifiers, vk)) not in self._warned:
                self._warned.add((action, (modifiers, vk)))
                logger.warning("The hotkey for %s is already taken by another application", action)

    def _unregister_all(self) -> None:
        for hotkey_id in self._registered:
            self._user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def nativeEventFilter(self, event_type, message):
        if not self._registered or bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message != WM_HOTKEY:
            return False, 0
        action = self._registered.get(msg.wParam)
        if action is None:
            return False, 0
        try:
            self._on_hotkey(action)
        except Exception:
            logger.exception("Hotkey handler raised for %s", action)
        return True, 0

    def close(self) -> None:
        """Release every registration. Call before the filter is dropped."""
        self._armed = False
        self._unregister_all()
