from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
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
    default_quicksell_sell_pets: bool = True
    default_quicksell_sell_socket_wrenches: bool = False
    default_quicksell_sell_skeleton_keys: bool = False
    auto_group_players: bool = True


class _GameEmbedSettings(BaseModel):
    resolution: list[int] = [800, 600]


class _CanvasSettings(BaseModel):
    minimap_visible: bool = True
    auto_layout_on_load: bool = True
    lod_compact_threshold: float = 0.6
    snap_to_grid: bool = False
    show_failure_edges: bool = True
    zoom_step: float = 1.15  # wheel zoom multiplier per step (1.05–1.30)
    pan_animation_ms: int = 300  # smooth-pan duration in ms; 0 = instant
    grid_style: str = "dots"  # "dots" | "lines" | "none"


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
        "default_quicksell_sell_pets": True,
        "default_quicksell_sell_socket_wrenches": False,
        "default_quicksell_sell_skeleton_keys": False,
        "auto_group_players": True,
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
        "zoom_step": 1.15,
        "pan_animation_ms": 300,
        "grid_style": "dots",
        "bot_library_path": "bots",
    },
    "accounts": [],
    "launch_profiles": [],
    "recent_launches": [],
    "recent_bot_files": [],
    "autosave_origins": {},
    "recently_used_nodes": [],
    "favorite_nodes": [],
    "favorited_hotkey_nodes": ["", "", "", "", "", "", "", "", "", ""],  # indices 0–9 → keys 1–9, 0
    "custom_theme": {},  # {token_name: "#rrggbb"} — only overridden tokens stored
    "recent_zones": [],
    "active_bot_strategies": {},  # {bot_uuid: bcs_uuid}
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
    def auto_group_players(self) -> bool:
        """Whether to form an in-game group at run start and after a client recovers."""
        return bool(self.get_general("auto_group_players", default=True))

    @auto_group_players.setter
    def auto_group_players(self, value: bool) -> None:
        self.set_general("auto_group_players", bool(value))

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

    def set_autosave_origin(self, stem: str, original_path: str) -> None:
        """Record which original bot file an auto-save stem was derived from."""
        origins = dict(self.get("autosave_origins", default={}))
        origins[stem] = str(Path(original_path).resolve())
        self.set("autosave_origins", value=origins)
        self.save()

    def get_autosave_origin(self, stem: str) -> str | None:
        """Look up the original bot file path recorded for an auto-save stem, if any."""
        origins: dict = self.get("autosave_origins", default={})
        return origins.get(stem)

    def remove_autosave_origin(self, stem: str) -> None:
        """Forget the recorded origin for an auto-save stem (called once its file is deleted)."""
        origins = dict(self.get("autosave_origins", default={}))
        if origins.pop(stem, None) is not None:
            self.set("autosave_origins", value=origins)
            self.save()

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

    @property
    def favorited_hotkey_nodes(self) -> list[str]:
        """10-slot list of node type keys bound to hotkeys 1–9 and 0. Empty string = unassigned."""
        raw = self.get("favorited_hotkey_nodes", default=[""] * 10)
        result = list(raw) + [""] * 10
        return result[:10]

    def set_favorited_hotkey_node(self, slot: int, node_type: str) -> None:
        """Assign node_type to the given 0-based slot (0 = key 1, …, 9 = key 0)."""
        slots = self.favorited_hotkey_nodes
        slots[slot] = node_type
        self.set("favorited_hotkey_nodes", value=slots)
        self.save()

    def clear_favorited_hotkey_node(self, slot: int) -> None:
        """Clear the assignment for the given 0-based slot."""
        self.set_favorited_hotkey_node(slot, "")

    @property
    def recent_zones(self) -> list[str]:
        """List of recently seen zone path strings, newest first. Capped at 30."""
        return list(self.get("recent_zones", default=[]))

    def add_recent_zone(self, zone: str) -> None:
        """Prepend zone to recent_zones, deduplicating and capping at 30. No-op if already at top."""
        zone = zone.strip()
        if not zone or zone == "—":
            return
        recent = self.recent_zones
        if recent and recent[0] == zone:
            return
        recent = [z for z in recent if z != zone]
        recent.insert(0, zone)
        self.set("recent_zones", value=recent[:30])
        self.save()

    # ------------------------------------------------------------------
    # Active bot strategy helpers
    # ------------------------------------------------------------------

    def get_active_strategy(self, bot_id: str) -> str | None:
        """Return the active BotCombatStrategy UUID for the given bot, or None."""
        return self.get("active_bot_strategies", bot_id)

    def set_active_strategy(self, bot_id: str, strategy_id: str | None) -> None:
        """Set (or clear) the active BotCombatStrategy UUID for a bot and persist."""
        mapping = dict(self.get("active_bot_strategies") or {})
        if strategy_id is None:
            mapping.pop(bot_id, None)
        else:
            mapping[bot_id] = strategy_id
        self.set("active_bot_strategies", value=mapping)
        self.save()

    def prune_active_strategies(self, known_strategy_ids: Iterable[str]) -> int:
        """Drop remembered selections whose BotCombatStrategy file no longer exists.

        Returns the number of entries removed.

        Deliberately prunes on the *strategy* id only, never the bot id.
        ``bot_strategies/`` is the single authoritative home for BCS files, so a
        strategy id matching nothing there is definitively dead.  A bot id, by
        contrast, may simply belong to a bot saved outside ``bots/`` that the
        user has not opened this session — dropping those would silently break
        the binding for every bot kept elsewhere on disk.
        """
        mapping = self.get("active_bot_strategies") or {}
        known = set(known_strategy_ids)
        kept = {bot_id: sid for bot_id, sid in mapping.items() if sid in known}
        removed = len(mapping) - len(kept)
        if removed:
            self.set("active_bot_strategies", value=kept)
            self.save()
        return removed

    # ------------------------------------------------------------------
    # Window sizing helper
    # ------------------------------------------------------------------

    def minimum_window_size(self) -> tuple[int, int]:
        """Return (min_width, min_height) based on the configured game resolution."""
        gw, gh = self.game_resolution
        min_w = SIDEBAR_MIN_WIDTH * 2 + gw + SPLITTER_HANDLE_PX * 2
        min_h = gh + BOTCANVAS_MIN_HEIGHT + 60  # 60px for menu + status bars
        return min_w, min_h
