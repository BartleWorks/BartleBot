"""File-based storage for the three combat strategy types plus the deck preset library.

Directory layout (version-controlled, relative to project root):
  strategies/player/   — PlayerCombatStrategy  files, named {uuid}.json
  strategies/duel/     — DuelCombatStrategy     files, named {uuid}.json
  strategies/decks/    — DeckPreset             files, named {uuid}.json
  bot_strategies/      — BotCombatStrategy      files, named {uuid}.json

Each function accepts an optional `root` parameter (path to the project root).
When omitted it defaults to :func:`bartlebot.paths.project_root`, so callers get
the correct directory regardless of the process's current working directory.
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


def _project_root(root: str | Path | None) -> Path:
    return Path(root) if root is not None else project_root()


# ---------------------------------------------------------------------------
# Player Combat Strategy
# ---------------------------------------------------------------------------


def _player_dir(root: str | Path | None) -> Path:
    return _project_root(root) / "strategies" / "player"


def list_player_strategies(root: str | Path | None = None) -> list[PlayerCombatStrategy]:
    d = _player_dir(root)
    if not d.exists():
        return []
    strategies = []
    for p in sorted(d.glob("*.json")):
        try:
            strategies.append(PlayerCombatStrategy.load(p))
        except Exception:
            pass
    return strategies


def load_player_strategy(uuid: str, root: str | Path | None = None) -> PlayerCombatStrategy:
    return PlayerCombatStrategy.load(_player_dir(root) / f"{uuid}.json")


def save_player_strategy(s: PlayerCombatStrategy, root: str | Path | None = None) -> None:
    s.save(_player_dir(root) / f"{s.id}.json")


def delete_player_strategy(uuid: str, root: str | Path | None = None) -> None:
    path = _player_dir(root) / f"{uuid}.json"
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Duel Combat Strategy
# ---------------------------------------------------------------------------


def _duel_dir(root: str | Path | None) -> Path:
    return _project_root(root) / "strategies" / "duel"


def list_duel_strategies(root: str | Path | None = None) -> list[DuelCombatStrategy]:
    d = _duel_dir(root)
    if not d.exists():
        return []
    strategies = []
    for p in sorted(d.glob("*.json")):
        try:
            strategies.append(DuelCombatStrategy.load(p))
        except Exception:
            pass
    return strategies


def load_duel_strategy(uuid: str, root: str | Path | None = None) -> DuelCombatStrategy:
    return DuelCombatStrategy.load(_duel_dir(root) / f"{uuid}.json")


def save_duel_strategy(s: DuelCombatStrategy, root: str | Path | None = None) -> None:
    s.save(_duel_dir(root) / f"{s.id}.json")


def delete_duel_strategy(uuid: str, root: str | Path | None = None) -> None:
    path = _duel_dir(root) / f"{uuid}.json"
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Bot Combat Strategy
# ---------------------------------------------------------------------------


def _bot_strategy_dir(root: str | Path | None) -> Path:
    return _project_root(root) / "bot_strategies"


def list_bot_strategies(bot_id: str, root: str | Path | None = None) -> list[BotCombatStrategy]:
    """Return all BotCombatStrategy files that reference the given bot UUID."""
    d = _bot_strategy_dir(root)
    if not d.exists():
        return []
    strategies = []
    for p in sorted(d.glob("*.json")):
        try:
            bcs = BotCombatStrategy.load(p)
            if bcs.bot_id == bot_id:
                strategies.append(bcs)
        except Exception:
            pass
    return strategies


def list_all_bot_strategies(root: str | Path | None = None) -> list[BotCombatStrategy]:
    """Return every BotCombatStrategy file regardless of bot_id."""
    d = _bot_strategy_dir(root)
    if not d.exists():
        return []
    strategies = []
    for p in sorted(d.glob("*.json")):
        try:
            strategies.append(BotCombatStrategy.load(p))
        except Exception:
            pass
    return strategies


def load_bot_strategy(uuid: str, root: str | Path | None = None) -> BotCombatStrategy:
    return BotCombatStrategy.load(_bot_strategy_dir(root) / f"{uuid}.json")


def save_bot_strategy(s: BotCombatStrategy, root: str | Path | None = None) -> None:
    s.save(_bot_strategy_dir(root) / f"{s.id}.json")


def delete_bot_strategy(uuid: str, root: str | Path | None = None) -> None:
    path = _bot_strategy_dir(root) / f"{uuid}.json"
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Deck Preset Library
# ---------------------------------------------------------------------------


def _deck_preset_dir(root: str | Path | None) -> Path:
    return _project_root(root) / "strategies" / "decks"


def list_deck_presets(root: str | Path | None = None) -> list[DeckPreset]:
    d = _deck_preset_dir(root)
    if not d.exists():
        return []
    presets = []
    for p in sorted(d.glob("*.json")):
        try:
            presets.append(DeckPreset.load(p))
        except Exception:
            pass
    return presets


def load_deck_preset(uuid: str, root: str | Path | None = None) -> DeckPreset:
    return DeckPreset.load(_deck_preset_dir(root) / f"{uuid}.json")


def save_deck_preset(preset: DeckPreset, root: str | Path | None = None) -> None:
    preset.save(_deck_preset_dir(root) / f"{preset.id}.json")


def delete_deck_preset(uuid: str, root: str | Path | None = None) -> None:
    path = _deck_preset_dir(root) / f"{uuid}.json"
    path.unlink(missing_ok=True)
