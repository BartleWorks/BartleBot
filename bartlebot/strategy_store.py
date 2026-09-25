"""File-based storage for the three combat strategy types plus the deck preset library.

Every strategy is read and written across an **ordered pair of roots**:

  shipped — the project root (``bartlebot.paths.project_root``), read-only,
            what the repo ships and version-controls.
  user    — ``%APPDATA%/Bartleby/BartleBot`` (``bartlebot.shared.vendor.product_dir``),
            where every app-initiated write lands.

Reads resolve user-first, falling back to default: a user file with the same id
shadows the shipped one. Writes and deletes only ever touch the user root, so
editing a shipped strategy copies it into the user root on save, keyed by the
same id — an override, not a fork. Deleting a user copy of a shipped id is how
"revert to default" works; the shipped file itself is never touched by the app.

Directory layout is identical under both roots:
  strategies/player/   — PlayerCombatStrategy files
  strategies/duel/     — DuelCombatStrategy   files
  strategies/decks/    — DeckPreset           files
  bot_strategies/      — BotCombatStrategy    files

Each file is named after the strategy's name (``default-support.json``) and found
by the id inside it; :mod:`bartlebot.store_roots` has the naming rules.

Every function accepts an optional ``root``, which sets the shipped root and,
unless ``user_root`` is also given, doubles as the user root too — so a single
sandbox directory in a test behaves like one flat store, same as before this
module had two roots. Pass ``user_root`` explicitly to exercise the shadowing
behavior. With both omitted, the real shipped/user roots are used.

The root resolution and :class:`Origin` live in :mod:`bartlebot.store_roots`,
shared with the subgraph library; ``Origin`` is re-exported here because every
combat surface imports it from this module.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckPreset,
    DuelCombatStrategy,
    PlayerCombatStrategy,
)
from bartlebot.store_roots import (
    Origin,
    delete_by_id,
    load_by_id,
    merged_by_id,
    origin_in,
    resolve_roots,
    save_by_id,
)

__all__ = [
    "Origin",
    "StrategyKind",
    "delete_bot_strategy",
    "delete_deck_preset",
    "delete_duel_strategy",
    "delete_player_strategy",
    "list_all_bot_strategies",
    "list_bot_strategies",
    "list_deck_presets",
    "list_duel_strategies",
    "list_player_strategies",
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
_merged = merged_by_id


# ---------------------------------------------------------------------------
# Player Combat Strategy
# ---------------------------------------------------------------------------


def _player_dir(root: Path) -> Path:
    return root / "strategies" / "player"


def list_player_strategies(
    root: str | Path | None = None, user_root: str | Path | None = None
) -> list[PlayerCombatStrategy]:
    shipped, user = _roots(root, user_root)
    by_id = _merged(_player_dir(shipped), _player_dir(user), PlayerCombatStrategy.load)
    return list(by_id.values())


def load_player_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> PlayerCombatStrategy:
    shipped, user = _roots(root, user_root)
    return load_by_id(_player_dir(shipped), _player_dir(user), uuid, PlayerCombatStrategy.load)  # type: ignore[return-value]


def save_player_strategy(
    s: PlayerCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    _, user = _roots(root, user_root)
    save_by_id(_player_dir(user), s.id, s.name, s.save)


def delete_player_strategy(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    delete_by_id(_player_dir(user), uuid)


# ---------------------------------------------------------------------------
# Duel Combat Strategy
# ---------------------------------------------------------------------------


def _duel_dir(root: Path) -> Path:
    return root / "strategies" / "duel"


def list_duel_strategies(
    root: str | Path | None = None, user_root: str | Path | None = None
) -> list[DuelCombatStrategy]:
    shipped, user = _roots(root, user_root)
    by_id = _merged(_duel_dir(shipped), _duel_dir(user), DuelCombatStrategy.load)
    return list(by_id.values())


def load_duel_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> DuelCombatStrategy:
    shipped, user = _roots(root, user_root)
    return load_by_id(_duel_dir(shipped), _duel_dir(user), uuid, DuelCombatStrategy.load)  # type: ignore[return-value]


def save_duel_strategy(
    s: DuelCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    _, user = _roots(root, user_root)
    save_by_id(_duel_dir(user), s.id, s.name, s.save)


def delete_duel_strategy(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    delete_by_id(_duel_dir(user), uuid)


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
    shipped, user = _roots(root, user_root)
    by_id = _merged(_bot_strategy_dir(shipped), _bot_strategy_dir(user), BotCombatStrategy.load)
    return list(by_id.values())


def load_bot_strategy(
    uuid: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> BotCombatStrategy:
    shipped, user = _roots(root, user_root)
    return load_by_id(_bot_strategy_dir(shipped), _bot_strategy_dir(user), uuid, BotCombatStrategy.load)  # type: ignore[return-value]


def save_bot_strategy(
    s: BotCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    _, user = _roots(root, user_root)
    save_by_id(_bot_strategy_dir(user), s.id, s.name, s.save)


def delete_bot_strategy(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    delete_by_id(_bot_strategy_dir(user), uuid)


def rekey_bot_strategy_states(
    bot_id: str, id_map: dict[str, str], root: str | Path | None = None, user_root: str | Path | None = None
) -> int:
    """Rewrite state ids in every strategy of ``bot_id`` and save the ones that changed.

    Converting a region into a subgraph instance renames its members
    (``docs/SUBGRAPHS.md``); the assignments that keyed on the old ids would
    otherwise fall silent, with nothing in the UI to say so. A shipped strategy
    that changes is written to the user root, as every save here is. Returns how
    many strategies were rewritten.
    """
    changed = 0
    for bcs in list_bot_strategies(bot_id, root, user_root):
        touched = False
        for assignment in bcs.state_assignments:
            renamed = [id_map.get(sid, sid) for sid in assignment.state_ids]
            if renamed != assignment.state_ids:
                assignment.state_ids = renamed
                touched = True
        if touched:
            save_bot_strategy(bcs, root, user_root)
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Deck Preset Library
# ---------------------------------------------------------------------------


def _deck_preset_dir(root: Path) -> Path:
    return root / "strategies" / "decks"


def list_deck_presets(root: str | Path | None = None, user_root: str | Path | None = None) -> list[DeckPreset]:
    shipped, user = _roots(root, user_root)
    by_id = _merged(_deck_preset_dir(shipped), _deck_preset_dir(user), DeckPreset.load)
    return list(by_id.values())


def load_deck_preset(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> DeckPreset:
    shipped, user = _roots(root, user_root)
    return load_by_id(_deck_preset_dir(shipped), _deck_preset_dir(user), uuid, DeckPreset.load)  # type: ignore[return-value]


def save_deck_preset(preset: DeckPreset, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    save_by_id(_deck_preset_dir(user), preset.id, preset.name, preset.save)


def delete_deck_preset(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    delete_by_id(_deck_preset_dir(user), uuid)


# ---------------------------------------------------------------------------
# Where a file actually lives
# ---------------------------------------------------------------------------

_KIND_DIRS = {
    StrategyKind.PLAYER: _player_dir,
    StrategyKind.DUEL: _duel_dir,
    StrategyKind.BOT: _bot_strategy_dir,
    StrategyKind.DECK: _deck_preset_dir,
}


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
    shipped, user = _roots(root, user_root)
    return origin_in(_KIND_DIRS[kind], uuid, shipped, user)
