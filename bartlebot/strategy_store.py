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
  strategies/player/   — PlayerCombatStrategy  files, named {uuid}.json
  strategies/duel/     — DuelCombatStrategy     files, named {uuid}.json
  strategies/decks/    — DeckPreset             files, named {uuid}.json
  bot_strategies/      — BotCombatStrategy      files, named {uuid}.json

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
from bartlebot.store_roots import Origin, merged_by_id, origin_in, resolve_roots

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
    user_path = _player_dir(user) / f"{uuid}.json"
    if user_path.exists():
        return PlayerCombatStrategy.load(user_path)
    return PlayerCombatStrategy.load(_player_dir(shipped) / f"{uuid}.json")


def save_player_strategy(
    s: PlayerCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    _, user = _roots(root, user_root)
    s.save(_player_dir(user) / f"{s.id}.json")


def delete_player_strategy(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    (_player_dir(user) / f"{uuid}.json").unlink(missing_ok=True)


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
    user_path = _duel_dir(user) / f"{uuid}.json"
    if user_path.exists():
        return DuelCombatStrategy.load(user_path)
    return DuelCombatStrategy.load(_duel_dir(shipped) / f"{uuid}.json")


def save_duel_strategy(
    s: DuelCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    _, user = _roots(root, user_root)
    s.save(_duel_dir(user) / f"{s.id}.json")


def delete_duel_strategy(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    (_duel_dir(user) / f"{uuid}.json").unlink(missing_ok=True)


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
    user_path = _bot_strategy_dir(user) / f"{uuid}.json"
    if user_path.exists():
        return BotCombatStrategy.load(user_path)
    return BotCombatStrategy.load(_bot_strategy_dir(shipped) / f"{uuid}.json")


def save_bot_strategy(
    s: BotCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    _, user = _roots(root, user_root)
    s.save(_bot_strategy_dir(user) / f"{s.id}.json")


def delete_bot_strategy(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    (_bot_strategy_dir(user) / f"{uuid}.json").unlink(missing_ok=True)


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
    user_path = _deck_preset_dir(user) / f"{uuid}.json"
    if user_path.exists():
        return DeckPreset.load(user_path)
    return DeckPreset.load(_deck_preset_dir(shipped) / f"{uuid}.json")


def save_deck_preset(preset: DeckPreset, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    preset.save(_deck_preset_dir(user) / f"{preset.id}.json")


def delete_deck_preset(uuid: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    _, user = _roots(root, user_root)
    (_deck_preset_dir(user) / f"{uuid}.json").unlink(missing_ok=True)


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
