from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Settings schema (structured sections only — list fields managed separately)
# ---------------------------------------------------------------------------


class _GeneralSettings(BaseModel):
    theme: str = "system"  # "light", "dark", or "system"
    maximize_on_open: bool = False
    clear_log_on_run: bool = True
    log_max_lines: int = 5000
    window_width: int = 1400
    window_height: int = 800
    left_sidebar_width: int = 300
    right_sidebar_width: int = 300
    bottom_section_height: int = 220
    auto_save_enabled: bool = True
    auto_save_interval_seconds: int = 300
    default_safety_delay: float = 3.0
    default_quicksell_sell_crowns: bool = True
    default_quicksell_sell_pets: bool = True
    default_quicksell_sell_socket_wrenches: bool = False
    default_quicksell_sell_skeleton_keys: bool = False


class _GameEmbedSettings(BaseModel):
    resolution: list[int] = [800, 600]


class _CanvasSettings(BaseModel):
    minimap_visible: bool = True
    auto_layout_on_load: bool = True
    lod_compact_threshold: float = 0.6
    snap_to_grid: bool = False
    show_failure_edges: bool = True


_SECTION_MODELS: dict[str, type[BaseModel]] = {
    "general": _GeneralSettings,
    "game_embed": _GameEmbedSettings,
    "canvas": _CanvasSettings,
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "general": {
        "theme": "system",
        "maximize_on_open": False,
        "clear_log_on_run": True,
        "log_max_lines": 5000,
        "window_width": 1400,
        "window_height": 800,
        "left_sidebar_width": 300,
        "right_sidebar_width": 300,
        "bottom_section_height": 220,
        "auto_save_enabled": True,
        "auto_save_interval_seconds": 300,
        "default_safety_delay": 3.0,
        "default_quicksell_sell_crowns": True,
        "default_quicksell_sell_pets": True,
        "default_quicksell_sell_socket_wrenches": False,
        "default_quicksell_sell_skeleton_keys": False,
    },
    "game_embed": {
        "resolution": [800, 600],
    },
    "canvas": {
        "minimap_visible": True,
        "auto_layout_on_load": True,
        "lod_compact_threshold": 0.6,
        "snap_to_grid": False,
        "show_failure_edges": True,
        "bot_library_path": "bots",
    },
    "accounts": [],
    "launch_profiles": [],
    "recent_launches": [],
    "recent_bot_files": [],
    "recently_used_nodes": [],
    "favorite_nodes": [],
    "custom_theme": {},  # {token_name: "#rrggbb"} — only overridden tokens stored
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

    @staticmethod
    def autosave_dir() -> Path:
        """Return the dedicated auto-save directory, creating it if necessary."""
        d = _appdata_dir() / "autosave"
        d.mkdir(parents=True, exist_ok=True)
        return d

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

        # Validate structured sections; reset any that fail to their defaults.
        for section_key, model_cls in _SECTION_MODELS.items():
            try:
                model_cls(**self._data[section_key])
            except (ValidationError, TypeError) as exc:
                logger.warning(
                    "Settings section %r failed validation — resetting to defaults: %s",
                    section_key,
                    exc,
                )
                self._data[section_key] = dict(_DEFAULTS[section_key])

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
    def default_safety_delay(self) -> float:
        return float(self.get_general("default_safety_delay", default=3.0))

    @default_safety_delay.setter
    def default_safety_delay(self, value: float) -> None:
        self.set_general("default_safety_delay", value)

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
        """List of recently opened bot file paths (absolute), newest first. Non-existent paths are pruned."""
        raw: list[str] = list(self.get("recent_bot_files", default=[]))
        seen: set[str] = set()
        resolved: list[str] = []
        for p in raw:
            abs_p = str(Path(p).resolve())
            if Path(abs_p).exists() and abs_p not in seen:
                seen.add(abs_p)
                resolved.append(abs_p)
        if resolved != raw:
            self.set("recent_bot_files", value=resolved)
            self.save()
        return resolved

    def add_recent_bot_file(self, path: str) -> list[str]:
        """Prepend path to recent_bot_files, deduplicating and capping at 8. Returns updated list."""
        norm = str(Path(path).resolve())
        recent = [p for p in self.recent_bot_files if p != norm]
        recent.insert(0, norm)
        self.set("recent_bot_files", value=recent[:8])
        self.save()
        return list(self.get("recent_bot_files", default=[]))

    @property
    def recently_used_nodes(self) -> list[str]:
        """List of recently added node types, newest first. Capped at 10."""
        return list(self.get("recently_used_nodes", default=[]))

    def add_recently_used_node(self, node_type: str) -> None:
        """Prepend node_type to recently_used_nodes, deduplicating and capping at 10."""
        recent = [n for n in self.recently_used_nodes if n != node_type]
        recent.insert(0, node_type)
        self.set("recently_used_nodes", value=recent[:10])
        self.save()

    @property
    def favorite_nodes(self) -> list[str]:
        """List of favorited node_type keys, in insertion order."""
        return list(self.get("favorite_nodes", default=[]))

    def is_favorite_node(self, node_type: str) -> bool:
        return node_type in self.favorite_nodes

    def toggle_favorite_node(self, node_type: str) -> bool:
        """Toggle node_type in favorites. Returns True if now favorited."""
        favs = list(self.favorite_nodes)
        if node_type in favs:
            favs.remove(node_type)
            is_fav = False
        else:
            favs.append(node_type)
            is_fav = True
        self.set("favorite_nodes", value=favs)
        self.save()
        return is_fav

    # ------------------------------------------------------------------
    # Window sizing helper
    # ------------------------------------------------------------------

    def minimum_window_size(self) -> tuple[int, int]:
        """Return (min_width, min_height) based on the configured game resolution."""
        gw, gh = self.game_resolution
        min_w = SIDEBAR_MIN_WIDTH * 2 + gw + SPLITTER_HANDLE_PX * 2
        min_h = gh + BOTCANVAS_MIN_HEIGHT + 60  # 60px for menu + status bars
        return min_w, min_h
