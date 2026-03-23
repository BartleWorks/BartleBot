from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "general": {
        "maximize_on_open": False,
        "window_width": 1400,
        "window_height": 800,
        "left_sidebar_width": 300,
        "right_sidebar_width": 300,
        "bottom_section_height": 220,
    },
    "game_embed": {
        "resolution": [800, 600],
    },
    "canvas": {
        "minimap_visible": True,
        "auto_layout_on_load": True,
        "lod_compact_threshold": 0.6,
    },
    "accounts": [],
    "launch_profiles": [],
    "recent_launches": [],
    "recent_bot_files": [],
}

# Valid Wizard101 windowed resolutions (width, height)
VALID_RESOLUTIONS: list[tuple[int, int]] = [
    (800, 600),
    (1024, 768),
    (1280, 720),
    (1280, 800),
    (1280, 960),
    (1280, 1024),
    (1366, 768),
    (1600, 900),
    (1920, 1080),
]

# Minimum BotCanvas height (toolbar + enough graph space)
BOTCANVAS_MIN_HEIGHT = 180
# Sidebar minimum widths
SIDEBAR_MIN_WIDTH = 220
# Splitter handle allowance
SPLITTER_HANDLE_PX = 8


def _appdata_dir() -> Path:
    base = os.environ.get("APPDATA") or Path.home()
    return Path(base) / "BartleBot"


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge *overrides* into a copy of *base*."""
    result = dict(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class SettingsManager:
    """Singleton that loads/saves application-level settings.

    Access the singleton via ``SettingsManager.instance()``.
    """

    _instance: SettingsManager | None = None

    def __init__(self) -> None:
        self._path = _appdata_dir() / "settings.json"
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> SettingsManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._data = dict(_DEFAULTS)
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as fh:
                    on_disk = json.load(fh)
                self._data = _deep_merge(_DEFAULTS, on_disk)
            except Exception:
                pass  # corrupt file → use defaults

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)

    # ------------------------------------------------------------------
    # Generic accessors
    # ------------------------------------------------------------------

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, *keys: str, value: Any) -> None:
        node = self._data
        for key in keys[:-1]:
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]
        node[keys[-1]] = value

    # ------------------------------------------------------------------
    # Typed helpers
    # ------------------------------------------------------------------

    @property
    def game_resolution(self) -> tuple[int, int]:
        raw = self.get("game_embed", "resolution", default=[800, 600])
        try:
            return int(raw[0]), int(raw[1])
        except (TypeError, IndexError, ValueError):
            return 800, 600

    @game_resolution.setter
    def game_resolution(self, value: tuple[int, int]) -> None:
        self.set("game_embed", "resolution", value=list(value))

    def get_general(self, key: str, default: Any = None) -> Any:
        return self.get("general", key, default=default)

    def set_general(self, key: str, value: Any) -> None:
        self.set("general", key, value=value)

    @property
    def accounts(self) -> list[dict]:
        """List of {username, alias?} dicts (no passwords)."""
        return list(self.get("accounts", default=[]))

    @accounts.setter
    def accounts(self, value: list[dict]) -> None:
        self.set("accounts", value=value)

    @property
    def launch_profiles(self) -> list[dict]:
        """List of {name, accounts: [username, ...]} dicts."""
        return list(self.get("launch_profiles", default=[]))

    @launch_profiles.setter
    def launch_profiles(self, value: list[dict]) -> None:
        self.set("launch_profiles", value=value)

    @property
    def recent_launches(self) -> list[dict]:
        """List of {type, id} dicts for recently launched accounts/profiles."""
        return list(self.get("recent_launches", default=[]))

    def add_recent_launch(self, entry_type: str, identifier: str) -> None:
        """Prepend an entry to recent_launches, deduplicating and capping at 5."""
        entry = {"type": entry_type, "id": identifier}
        recent = [e for e in self.recent_launches if not (e.get("type") == entry_type and e.get("id") == identifier)]
        recent.insert(0, entry)
        self.set("recent_launches", value=recent[:5])
        self.save()

    @property
    def recent_bot_files(self) -> list[str]:
        """List of recently opened bot file paths, newest first. Non-existent paths are pruned."""
        raw: list[str] = list(self.get("recent_bot_files", default=[]))
        pruned = [p for p in raw if Path(p).exists()]
        if len(pruned) != len(raw):
            self.set("recent_bot_files", value=pruned)
            self.save()
        return pruned

    def add_recent_bot_file(self, path: str) -> list[str]:
        """Prepend path to recent_bot_files, deduplicating and capping at 8. Returns updated list."""
        recent = [p for p in self.recent_bot_files if p != path]
        recent.insert(0, path)
        self.set("recent_bot_files", value=recent[:8])
        self.save()
        return list(self.get("recent_bot_files", default=[]))

    # ------------------------------------------------------------------
    # Window sizing helper
    # ------------------------------------------------------------------

    def minimum_window_size(self) -> tuple[int, int]:
        """Return (min_width, min_height) based on the configured game resolution."""
        gw, gh = self.game_resolution
        min_w = SIDEBAR_MIN_WIDTH * 2 + gw + SPLITTER_HANDLE_PX * 2
        min_h = gh + BOTCANVAS_MIN_HEIGHT + 60  # 60px for menu + status bars
        return min_w, min_h
