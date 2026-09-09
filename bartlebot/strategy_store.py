"""File-based storage for the three combat strategy types plus the deck preset library.

Every strategy is read and written across an **ordered pair of roots**:

  shipped — the project root (``bartlebot.paths.project_root``), read-only,
            what the repo ships and version-controls.
  user    — ``%APPDATA%/Bartleby/BartleBot`` (``bartlebot.shared.vendor.product_dir``),
            where every app-initiated write lands.

Reads resolve user-first, falling back to shipped: a user file with the same id
shadows the shipped one. Writes and deletes only ever touch the user root, so
editing a shipped strategy copies it into the user root on save, keyed by the
same id — an override, not a fork. Deleting a user copy of a shipped id is how
"revert to shipped" works; the shipped file itself is never touched by the app.

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
"""

from __future__ import annotations

from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckPreset,
    DuelCombatStrategy,
    PlayerCombatStrategy,
)
from bartlebot.paths import project_root
from bartlebot.shared.vendor import product_dir


def _roots(root: str | Path | None, user_root: str | Path | None) -> tuple[Path, Path]:
    shipped = Path(root) if root is not None else project_root()
    if user_root is not None:
        user = Path(user_root)
    elif root is not None:
        user = Path(root)
    else:
        user = product_dir()
    return shipped, user


def _merged(shipped_dir: Path, user_dir: Path, loader) -> dict[str, object]:
    """Load every file under both dirs, keyed by id, user entries winning on collision."""
    by_id: dict[str, object] = {}
    for d in (shipped_dir, user_dir):
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                item = loader(p)
            except Exception:
                continue
            by_id[item.id] = item
    return by_id


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
