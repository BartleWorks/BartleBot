"""The game's own keyboard bindings, for the authoring hotkeys' clash warning (#81).

Read so the authoring hotkeys can say when one lands on a key the game already uses. The
hotkeys are armed only while a game window has the keyboard, which is exactly when the game
would have acted on that key, so a clash is not a hypothetical: the game never sees the key,
and the user loses that binding for as long as the hotkey holds it.

**Whose bindings.** Since r811339 they belong to the account (``docs/KEY_BINDINGS.md``), so
the best answer is each attached client's own, as last read from its memory, and all of them
together, since a hotkey is armed in whichever game window has the keyboard. The settings
dialog cannot wait on a read, so its caller hands those in (``clients/game_keys.known_maps``).
With none, the install's ``LocalInputBindings.xml``, the last account to save, and failing that
the shipped ``InputBindings.xml``. ``Bin/config.xml``'s ``<InputMappings>``, read here before
r811339, is no longer kept current by the game: a rebound key still read as its old one there.

Read-only, and read with a regex like every other read of the game's own files.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from bartlebot.game.key_bindings import ENCODING, Binding, KeyMap, parse_binding_file, parse_input

logger = logging.getLogger(__name__)

GAME_DATA = ("Data", "GameData")
BINDING_FILES = ("LocalInputBindings.xml", "InputBindings.xml")


@dataclass(frozen=True)
class GameBinding:
    """One keyboard binding: the action it fires, and its key as ``Ctrl+Shift+Q`` or ``F12``."""

    event: str
    binding: str

    @property
    def description(self) -> str:
        """The action as words: ``DisplayNewBuddyList`` -> ``Display New Buddy List``."""
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", self.event)


def game_bindings(bindings: Iterable[Binding]) -> list[GameBinding]:
    """Every keyboard binding among *bindings*, once each; mouse and other inputs skipped."""
    found: dict[tuple[str, str], GameBinding] = {}
    for binding in bindings:
        parsed = parse_input(binding.input)
        if parsed is not None:
            found.setdefault((binding.action, parsed.text), GameBinding(binding.action, parsed.text))
    return list(found.values())


def read_binding_files(install: Path | None) -> list[Binding]:
    """The bindings in *install*'s ``LocalInputBindings.xml``, else its ``InputBindings.xml``; [] for neither."""
    if install is None:
        return []
    for name in BINDING_FILES:
        path = Path(install).joinpath(*GAME_DATA, name)
        try:
            return parse_binding_file(path.read_bytes().decode(ENCODING))
        except OSError:
            continue
    return []


def read_game_keybindings(maps: Sequence[KeyMap] = ()) -> list[GameBinding]:
    """The game's keyboard bindings: those of *maps*, the clients' own, or else the install's files.

    Empty on any failure: no install found, no file yet. A warning that cannot be given is not
    a reason to stop the settings dialog opening.
    """
    if maps:
        return game_bindings(b for key_map in maps for b in key_map.bindings)
    from bartlebot.shared.game_install import find_install  # noqa: PLC0415 - a registry lookup, only when asked
    from bartlebot.shared.preferences import PreferenceStore  # noqa: PLC0415

    try:
        found = find_install(PreferenceStore())
    except Exception:
        logger.debug("Could not find the game for its key bindings", exc_info=True)
        return []
    return game_bindings(read_binding_files(found.path if found is not None else None))
