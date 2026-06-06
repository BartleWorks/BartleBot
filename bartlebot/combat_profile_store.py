"""Global storage for exported Bot Combat Profiles and Duel Profiles.

Files are stored under %APPDATA%/BartleBot/:
  bot_combat_profiles/<name>.json  — exported BotCombatProfile objects
  duel_profiles/<name>.json        — exported DuelProfile objects
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bartlebot.bot.combat_profile import BotCombatProfile, DuelProfile


def _appdata_dir() -> Path:
    base = os.environ.get("APPDATA") or Path.home()
    return Path(base) / "BartleBot"


def _bot_profiles_dir() -> Path:
    return _appdata_dir() / "bot_combat_profiles"


def _duel_profiles_dir() -> Path:
    return _appdata_dir() / "duel_profiles"


# ---------------------------------------------------------------------------
# Bot Combat Profiles
# ---------------------------------------------------------------------------


def list_bot_profiles() -> list[str]:
    """Return names of all saved global Bot Combat Profiles."""
    d = _bot_profiles_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def load_bot_profile(name: str) -> BotCombatProfile:
    path = _bot_profiles_dir() / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return BotCombatProfile.model_validate(json.load(f))


def save_bot_profile(profile: BotCombatProfile) -> None:
    profile.save(_bot_profiles_dir() / f"{profile.name}.json")


def delete_bot_profile(name: str) -> None:
    path = _bot_profiles_dir() / f"{name}.json"
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Duel Profiles
# ---------------------------------------------------------------------------


def list_duel_profiles() -> list[str]:
    """Return names of all saved global Duel Profiles."""
    d = _duel_profiles_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def load_duel_profile(name: str) -> DuelProfile:
    path = _duel_profiles_dir() / f"{name}.json"
    with open(path, encoding="utf-8") as f:
        return DuelProfile.model_validate(json.load(f))


def save_duel_profile(profile: DuelProfile) -> None:
    d = _duel_profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    import tempfile  # noqa: PLC0415

    path = d / f"{profile.name}.json"
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp", prefix=profile.name + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(profile.model_dump(exclude_none=True), f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def delete_duel_profile(name: str) -> None:
    path = _duel_profiles_dir() / f"{name}.json"
    path.unlink(missing_ok=True)
