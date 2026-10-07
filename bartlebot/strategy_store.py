"""File-based storage for the three combat strategy types plus the deck preset library.

Every strategy is read and written across an **ordered pair of roots**, a shipped one
that is read-only and a user one where every app-initiated write lands. Reads resolve
user-first, falling back to default: a user file with the same id shadows the shipped
one. Writes and deletes only ever touch the user root, so editing a shipped strategy
copies it into the user root on save, keyed by the same id — an override, not a fork.
Deleting a user copy of a shipped id is how "revert to default" works; the shipped
file itself is never touched by the app.

The kinds sit under two different user roots:

  strategies/player/   — PlayerCombatStrategy files   } the shared library, at the vendor
  strategies/duel/     — DuelCombatStrategy   files   } root (%APPDATA%/Bartleby), read and
  strategies/decks/    — DeckPreset           files   } written by every product
  bot_strategies/      — BotCombatStrategy    files   — BartleBot's own (%APPDATA%/Bartleby/BartleBot)

The shared three are stored by :mod:`bartlebot.shared.strategy_library`, whose roots
come from :func:`bartlebot.store_roots.resolve_shared_roots`. Because another product
can have one of their files open, a save of one passes the fingerprint it was loaded
with (``expected``) and writes nothing if the file changed on disk since, raising
:class:`ChangedOnDisk`; a file in a newer format raises :class:`NewerFormat`. With no
fingerprint a save expects no user file, which is what creating an item means.

Each file is named after the strategy's name (``default-support.json``) and found
by the id inside it; :mod:`bartlebot.shared.store_files` has the naming rules.

Every function accepts an optional ``root``, which sets the shipped root and,
unless ``user_root`` is also given, doubles as the user root too — so a single
sandbox directory in a test behaves like one flat store, same as before this
module had two roots. Pass ``user_root`` explicitly to exercise the shadowing
behavior. With both omitted, the real shipped/user roots are used.

``Origin`` is re-exported here because every combat surface imports it from this module.
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckPreset,
    DuelCombatStrategy,
    PlayerCombatStrategy,
)
from bartlebot.shared import strategy_library as library
from bartlebot.shared.store_files import record_save_in
from bartlebot.shared.strategy_library import ChangedOnDisk, Entry, LibraryKind, NewerFormat
from bartlebot.store_roots import (
    Origin,
    delete_by_id,
    index_dir,
    load_by_id,
    merged_by_id,
    origin_in,
    resolve_bot_strategy_roots,
    resolve_roots,
    resolve_shared_roots,
    save_by_id,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ChangedOnDisk",
    "Entry",
    "NewerFormat",
    "Origin",
    "StrategyKind",
    "delete_bot_strategy",
    "delete_deck_preset",
    "delete_duel_strategy",
    "delete_player_strategy",
    "fingerprint",
    "library_entries",
    "load_library_entry",
    "list_all_bot_strategies",
    "list_bot_strategies",
    "list_deck_presets",
    "list_duel_strategies",
    "list_player_strategies",
    "list_shipped",
    "load_bot_strategy",
    "load_deck_preset",
    "load_duel_strategy",
    "load_player_strategy",
    "origin_of",
    "save_bot_strategy",
    "save_deck_preset",
    "save_duel_strategy",
    "save_player_strategy",
    "unique_name",
]


class StrategyKind(Enum):
    """Which of the four stores an id lives in.

    Only needed by the functions that answer a question *about* a file rather
    than reading or writing one — :func:`origin_of` and the callers that want
    one code path across kinds. The read/write API stays four explicit function
    families, which is what nearly every caller wants.
    """

    PLAYER = "player"
    DUEL = "duel"
    BOT = "bot"
    DECK = "deck"


#: ``base (2)``, ``base (3)`` — the suffix :func:`unique_name` adds and strips.
_COPY_SUFFIX = re.compile(r"^(?P<stem>.*?)\s*\((?P<n>\d+)\)$")


def unique_name(base: str, existing_names) -> str:
    """``base``, or the first free ``base (2)``, ``base (3)``, … .

    One naming rule for Duplicate and ``+ New`` across all four strategy kinds,
    so a copy of a copy reads as ``Storm AoE (3)`` rather than
    ``Storm AoE copy copy``. A ``base`` that already carries a suffix is counted
    from its stem for that reason — duplicating ``Storm AoE (2)`` gives
    ``Storm AoE (3)``, not ``Storm AoE (2) (2)``.

    ``existing_names`` is any iterable of names; comparison is exact, since these
    are user-visible labels and two names differing only in case are two names.
    """
    taken = set(existing_names)
    if base not in taken:
        return base
    match = _COPY_SUFFIX.match(base)
    stem = match.group("stem") if match else base
    n = int(match.group("n")) + 1 if match else 2
    while f"{stem} ({n})" in taken:
        n += 1
    return f"{stem} ({n})"


_roots = resolve_roots
_bot_roots = resolve_bot_strategy_roots
_merged = merged_by_id


# ---------------------------------------------------------------------------
# The shared library: player strategies, duel strategies, deck presets
# ---------------------------------------------------------------------------

_shared_roots = resolve_shared_roots


def library_entries(
    kind: StrategyKind, root: str | Path | None = None, user_root: str | Path | None = None
) -> dict[str, Entry]:
    """Every ``kind`` entry in the shared library, by id, each with the fingerprint a save needs."""
    shipped, user = _shared_roots(root, user_root)
    return library.entries(shipped, user, _LIBRARY_KINDS[kind])


def load_library_entry(
    kind: StrategyKind, uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> Entry:
    """``uuid`` as it is on disk now, with its fingerprint. Raises ``FileNotFoundError`` if no root has it."""
    shipped, user = _shared_roots(root, user_root)
    return library.load(shipped, user, _LIBRARY_KINDS[kind], uuid)


def fingerprint(
    kind: StrategyKind, uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> str | None:
    """The fingerprint ``uuid`` would load with now; see :mod:`bartlebot.shared.strategy_library`."""
    _, user = _shared_roots(root, user_root)
    return library.fingerprint(user, _LIBRARY_KINDS[kind], uuid)


def _list(kind: LibraryKind, root, user_root) -> list:
    shipped, user = _shared_roots(root, user_root)
    return [e.item for e in library.entries(shipped, user, kind).values()]


def _load(kind: LibraryKind, uuid: str, root, user_root):
    shipped, user = _shared_roots(root, user_root)
    return library.load(shipped, user, kind, uuid).item


def _save(kind: LibraryKind, item, root, user_root, expected: str | None) -> str:
    shipped, user = _shared_roots(root, user_root)
    return library.save(user, kind, item, expected=expected, shipped=shipped)


def _delete(kind: LibraryKind, uuid: str, root, user_root, expected: str | None) -> None:
    _, user = _shared_roots(root, user_root)
    library.delete(user, kind, uuid, expected=expected)


def list_player_strategies(
    root: str | Path | None = None, user_root: str | Path | None = None
) -> list[PlayerCombatStrategy]:
    return _list(LibraryKind.PLAYER, root, user_root)


def load_player_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> PlayerCombatStrategy:
    return _load(LibraryKind.PLAYER, uuid, root, user_root)


def save_player_strategy(
    s: PlayerCombatStrategy,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    *,
    expected: str | None = None,
) -> str:
    """Write ``s`` to the user root and return its new fingerprint, if nothing changed it since ``expected``."""
    return _save(LibraryKind.PLAYER, s, root, user_root, expected)


def delete_player_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None, *, expected: str | None = None
) -> None:
    _delete(LibraryKind.PLAYER, uuid, root, user_root, expected)


def list_duel_strategies(
    root: str | Path | None = None, user_root: str | Path | None = None
) -> list[DuelCombatStrategy]:
    return _list(LibraryKind.DUEL, root, user_root)


def load_duel_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> DuelCombatStrategy:
    return _load(LibraryKind.DUEL, uuid, root, user_root)


def save_duel_strategy(
    s: DuelCombatStrategy,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    *,
    expected: str | None = None,
) -> str:
    """Write ``s`` to the user root and return its new fingerprint, if nothing changed it since ``expected``."""
    return _save(LibraryKind.DUEL, s, root, user_root, expected)


def delete_duel_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None, *, expected: str | None = None
) -> None:
    _delete(LibraryKind.DUEL, uuid, root, user_root, expected)


def list_deck_presets(root: str | Path | None = None, user_root: str | Path | None = None) -> list[DeckPreset]:
    return _list(LibraryKind.DECK, root, user_root)


def load_deck_preset(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> DeckPreset:
    return _load(LibraryKind.DECK, uuid, root, user_root)


def save_deck_preset(
    preset: DeckPreset,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    *,
    expected: str | None = None,
) -> str:
    """Write ``preset`` to the user root and return its new fingerprint, if nothing changed it since ``expected``."""
    return _save(LibraryKind.DECK, preset, root, user_root, expected)


def delete_deck_preset(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None, *, expected: str | None = None
) -> None:
    _delete(LibraryKind.DECK, uuid, root, user_root, expected)


# ---------------------------------------------------------------------------
# Bot Combat Strategy
# ---------------------------------------------------------------------------


def _bot_strategy_dir(root: Path) -> Path:
    return root / "bot_strategies"


def list_bot_strategies(
    bot_id: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> list[BotCombatStrategy]:
    """Return all BotCombatStrategy files that reference the given bot UUID."""
    return [bcs for bcs in list_all_bot_strategies(root, user_root) if bcs.bot_id == bot_id]


def list_all_bot_strategies(
    root: str | Path | None = None, user_root: str | Path | None = None
) -> list[BotCombatStrategy]:
    """Return every BotCombatStrategy file regardless of bot_id."""
    shipped, user = _bot_roots(root, user_root)
    by_id = _merged(_bot_strategy_dir(shipped), _bot_strategy_dir(user), BotCombatStrategy.load)
    return list(by_id.values())


def load_bot_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> BotCombatStrategy:
    shipped, user = _bot_roots(root, user_root)
    return load_by_id(_bot_strategy_dir(shipped), _bot_strategy_dir(user), uuid, BotCombatStrategy.load)  # type: ignore[return-value]


def save_bot_strategy(
    s: BotCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None, *, record: bool = True
) -> None:
    """Write ``s`` to the user root, recording its history into it first (:mod:`bartlebot.shared.content_hash`).

    ``record=False`` writes the history ``s`` already has, as an import does (#171).
    """
    shipped, user = _bot_roots(root, user_root)
    if record:
        record_save_in(_bot_strategy_dir(shipped), _bot_strategy_dir(user), s, BotCombatStrategy.load)
    save_by_id(_bot_strategy_dir(user), s.id, s.name, s.save)


def delete_bot_strategy(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _bot_roots(root, user_root)
    delete_by_id(_bot_strategy_dir(user), uuid)


def rekey_bot_strategy_states(
    bot_id: str, id_map: dict[str, str], root: str | Path | None = None, user_root: str | Path | None = None
) -> int:
    """Rename each fight's state in every strategy of ``bot_id`` and save the ones that changed.

    Converting a region into a subgraph instance renames its members
    (``docs/SUBGRAPHS.md``); the assignments that keyed on the old ids would
    otherwise fall silent, with nothing in the UI to say so. A shipped strategy
    that changes is written to the user root, as every save here is. Returns how
    many strategies were rewritten. A fight's ``enemy`` is left alone: the node
    was renamed, not the enemy it names.
    """
    changed = 0
    for bcs in list_bot_strategies(bot_id, root, user_root):
        touched = False
        for assignment in bcs.state_assignments:
            for fight in assignment.fights:
                if id_map.get(fight.state, fight.state) != fight.state:
                    fight.state = id_map[fight.state]
                    touched = True
        if touched:
            save_bot_strategy(bcs, root, user_root)
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Where a file actually lives
# ---------------------------------------------------------------------------

_LIBRARY_KINDS = {
    StrategyKind.PLAYER: LibraryKind.PLAYER,
    StrategyKind.DUEL: LibraryKind.DUEL,
    StrategyKind.DECK: LibraryKind.DECK,
}

_KIND_DIRS = {
    StrategyKind.PLAYER: lambda root: library.kind_dir(root, LibraryKind.PLAYER),
    StrategyKind.DUEL: lambda root: library.kind_dir(root, LibraryKind.DUEL),
    StrategyKind.BOT: _bot_strategy_dir,
    StrategyKind.DECK: lambda root: library.kind_dir(root, LibraryKind.DECK),
}


def _roots_for(kind: StrategyKind, root, user_root) -> tuple[Path, Path]:
    """The roots ``kind`` is stored under: BartleBot's own for a Bot Combat Strategy, else the shared library's."""
    return _bot_roots(root, user_root) if kind is StrategyKind.BOT else _shared_roots(root, user_root)


def origin_of(
    kind: StrategyKind, uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> Origin:
    """Which root(s) hold ``uuid`` — see :class:`Origin`.

    The one place a surface can ask whether removing a strategy deletes it or
    merely reverts it, without reaching for the paths itself. Returns
    :attr:`Origin.USER` for both roots when they are the same directory (the
    single-root sandbox a test passes as ``root`` alone), since nothing is
    shipped in that arrangement and everything written is the user's.
    """
    shipped, user = _roots_for(kind, root, user_root)
    return origin_in(_KIND_DIRS[kind], uuid, shipped, user)


_LOADERS = {
    StrategyKind.PLAYER: PlayerCombatStrategy.load,
    StrategyKind.DUEL: DuelCombatStrategy.load,
    StrategyKind.BOT: BotCombatStrategy.load,
    StrategyKind.DECK: DeckPreset.load,
}


def list_shipped(kind: StrategyKind, root: str | Path | None = None, user_root: str | Path | None = None) -> list:
    """Every ``kind`` entry as it ships, ignoring any user copy that shadows it.

    What "revert to default" restores, and what a working set compares an entry
    against to say whether it is still the built-in one. Empty when the two roots
    are one directory — developer mode, or a single-root test sandbox — since
    nothing is shipped in that arrangement (see :func:`origin_of`).
    """
    shipped, user = _roots_for(kind, root, user_root)
    if shipped == user:
        return []
    items = []
    for path in index_dir(_KIND_DIRS[kind](shipped)).values():
        try:
            items.append(_LOADERS[kind](path))
        except Exception:
            logger.warning("Skipping unloadable %s", path, exc_info=True)
    return items
