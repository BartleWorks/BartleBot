"""The game's own keyboard bindings, read from ``Bin/config.xml``.

Wizard101 keeps its key map in ``<InputMappings>``: one ``<RECORD>`` per binding, naming
the ``<Event>`` it fires, the ``<Key>``, and ``<Alt>``/``<Ctrl>``/``<Shift>`` flags. Mouse
and gamepad bindings sit in the same list and are skipped.

Read so the authoring hotkeys (#81) can say when one lands on a key the game already
uses. The hotkeys are armed only while a game window has the keyboard, which is exactly
when the game would have acted on that key, so a clash is not a hypothetical: the game
never sees the key, and the user loses that binding for as long as the hotkey holds it.

Read-only, and read with a regex like every other ``config.xml`` read in the launcher,
because the file is the game's and is never parsed or rewritten whole here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SECTION = re.compile(r"<InputMappings>(.*?)</InputMappings>", re.DOTALL)
_RECORD = re.compile(r"<RECORD>(.*?)</RECORD>", re.DOTALL)


def _field(record: str, tag: str) -> str:
    match = re.search(rf"<{tag}\b[^>]*>([^<]*)</\s*{tag}\s*>", record)
    return match.group(1).strip() if match else ""


@dataclass(frozen=True)
class GameBinding:
    """One keyboard binding: the event it fires, and the key in ``QKeySequence`` form."""

    event: str
    binding: str

    @property
    def description(self) -> str:
        """The event as words: ``DisplayNewBuddyList`` -> ``Display New Buddy List``."""
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", self.event)


def parse_input_mappings(text: str) -> list[GameBinding]:
    """Every keyboard binding in *text*, a ``config.xml``'s contents. Empty if it has none."""
    section = _SECTION.search(text)
    if section is None:
        return []
    bindings: list[GameBinding] = []
    for record in _RECORD.findall(section.group(1)):
        if _field(record, "InputType").lower() != "keyboard":
            continue
        key = _field(record, "Key")
        if not key:
            continue
        modifiers = [name for name in ("Ctrl", "Alt", "Shift") if _field(record, name) == "1"]
        bindings.append(GameBinding(event=_field(record, "Event"), binding="+".join([*modifiers, key])))
    return bindings


def read_game_keybindings() -> list[GameBinding]:
    """The installed game's keyboard bindings, or an empty list if they cannot be read.

    Empty on any failure: no install found, no file yet, a file mid-write. A warning that
    cannot be given is not a reason to stop the settings dialog opening.
    """
    from wizwalker.utils import get_wiz_install  # noqa: PLC0415 - registry lookup, only when asked

    try:
        text = (get_wiz_install() / "Bin" / "config.xml").read_text(encoding="utf-8")
    except Exception:
        logger.debug("Could not read config.xml for the game's key bindings", exc_info=True)
        return []
    return parse_input_mappings(text)
