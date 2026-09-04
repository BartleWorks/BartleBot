from __future__ import annotations

import copy
import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from bartlebot import bot_options
from bartlebot.game_sizing import MENU_AND_STATUS_PX, SUB_FLOOR_RESOLUTIONS, screen_budget

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
    default_quicksell_enabled: bool = True
    default_apply_archmastery: bool = False
    default_level_scaling_players: list[int] = []
    auto_group_players: bool = True
    auto_select_character: bool = True
    relog_interval_hours: float = 4.0
    disable_quick_friend_teleport: bool = True
    default_friend_name: str = ""
    show_option_descriptions: bool = False
    streamer_mode: bool = False


class _GameEmbedSettings(BaseModel):
    resolution: list[int] = [800, 600]
    # The game's own UI scale as the user had it, so a clamped launch can give it back.
    original_ui_size: int | None = None


class _CanvasSettings(BaseModel):
    minimap_visible: bool = True
    auto_layout_on_load: bool = True
    lod_compact_threshold: float = 0.6
    snap_to_grid: bool = False
    show_failure_edges: bool = True
    zoom_step: float = 1.15  # wheel zoom multiplier per step (1.05–1.30)
    pan_animation_ms: int = 300  # smooth-pan duration in ms; 0 = instant
    grid_style: str = "dots"  # "dots" | "lines" | "none"
    bot_library_path: str = "bots"


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
        "default_quicksell_enabled": True,
        "default_apply_archmastery": False,
        "default_level_scaling_players": [],
        "auto_group_players": True,
        "auto_select_character": True,
        "relog_interval_hours": 4.0,
        "disable_quick_friend_teleport": True,
        "default_friend_name": "",
        "show_option_descriptions": False,
        "streamer_mode": False,
    },
    "game_embed": {
        "resolution": [800, 600],
        # The user's own <UISize>, captured once before the first launch clamps it.
        # See SettingsManager.original_ui_size.
        "original_ui_size": None,
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
    "recent_friend_names": [],
    "active_bot_strategies": {},  # {bot_uuid: bcs_uuid}
    "bot_option_overrides": {},  # {bot_uuid: {option_name: value}}
}

# Valid Wizard101 windowed resolutions (width, height), smallest first.
#
# The five below 800x600 are the game_sizing ladder: the game's own dropdown stops at
# 800x600, but that is a UI restriction rather than an engine limit, and each of these
# is the floor of exactly one UI scale (#101). They are what makes 2-4 embedded clients
# fit a 1080p screen. Nothing below 400x300 is offered -- that is where the HUD stops
# working even at Micro, the smallest scale the game has.
VALID_RESOLUTIONS: list[tuple[int, int]] = [
    *SUB_FLOOR_RESOLUTIONS,
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
    """Recursively merge *overrides* into a deep copy of *base*.

    Deep, because *base* is ``_DEFAULTS``: a shallow copy would leave every section the
    overrides do not mention — and every default list — aliased to the module's own,
    where the first mutation edits the defaults themselves.
    """
    result = copy.deepcopy(base)
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
        # deepcopy, not dict(): a shallow copy shares every nested section with _DEFAULTS,
        # so the first set_general() rewrites the module's own defaults for the life of
        # the process — and the next SettingsManager built with no file on disk starts
        # from the mutated ones.
        self._data = copy.deepcopy(_DEFAULTS)
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as fh:
                    on_disk = json.load(fh)
                self._data = _deep_merge(_DEFAULTS, on_disk)
            except Exception:
                pass  # corrupt file → use defaults

        # Validate structured sections; reset any that fail to their defaults.
        #
        # Every key in _DEFAULTS[section] must also be a field on the section's model.
        # Pydantic ignores an extra key rather than rejecting it, so a key present in
        # one and not the other is not an error -- it is simply never type-checked, and
        # a hand-edited settings.json can then put a string where a bool belongs and
        # have it reach the widget. streamer_mode and bot_library_path were both in
        # that state; test_every_default_is_declared_on_its_model is the guard.
        for section_key, model_cls in _SECTION_MODELS.items():
            try:
                model_cls(**self._data[section_key])
            except (ValidationError, TypeError) as exc:
                logger.warning(
                    "Settings section %r failed validation — resetting to defaults: %s",
                    section_key,
                    exc,
                )
                self._data[section_key] = copy.deepcopy(_DEFAULTS[section_key])

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

    @property
    def original_ui_size(self) -> int | None:
        """The user's own ``<UISize>``, captured before the first launch clamped it.

        Persisted here rather than re-read from config.xml because after one clamped
        session the file holds *our* value, so reading it back would treat a value we
        wrote as the user's preference -- and every launch after that would compound it.
        Deimos hit the identical bug shape with camera frustums.

        It also has to outlive the process: a crash that never reaches the restore leaves
        the user's game altered, and this is what the next start puts back.

        None means nothing has been captured, which is both the state before the first
        launch and the state after a restore.
        """
        raw = self.get("game_embed", "original_ui_size", default=None)
        try:
            return None if raw is None else int(raw)
        except (TypeError, ValueError):
            return None

    @original_ui_size.setter
    def original_ui_size(self, value: int | None) -> None:
        self.set("game_embed", "original_ui_size", value=None if value is None else int(value))

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
    def relog_interval_hours(self) -> float:
        """How often a run relogs every client, in hours. 0 turns it off.

        A game client degrades the longer it stays logged in (#143) and a relog is the only
        thing that clears it, so this is a maintenance interval rather than a preference
        about behaviour.
        """
        return float(self.get_general("relog_interval_hours", default=4.0))

    @relog_interval_hours.setter
    def relog_interval_hours(self, value: float) -> None:
        self.set_general("relog_interval_hours", max(0.0, float(value)))

    @property
    def auto_select_character(self) -> bool:
        """Whether a launched client is driven through to in-game without user input."""
        return bool(self.get_general("auto_select_character", default=True))

    @auto_select_character.setter
    def auto_select_character(self, value: bool) -> None:
        self.set_general("auto_select_character", bool(value))

    @property
    def disable_quick_friend_teleport(self) -> bool:
        """Whether launched clients get the game's quick friend teleport option turned off.

        With it on, double-clicking a friends list row teleports instead of opening that
        friend's profile, which is what the friends list nodes are trying to do.
        """
        return bool(self.get_general("disable_quick_friend_teleport", default=True))

    @disable_quick_friend_teleport.setter
    def disable_quick_friend_teleport(self, value: bool) -> None:
        self.set_general("disable_quick_friend_teleport", bool(value))

    @property
    def streamer_mode(self) -> bool:
        """Whether wizard names are replaced with positional placeholders in the UI.

        Off by default: a name is genuinely useful for telling characters apart, and
        someone who is not sharing their screen has nothing to gain from hiding it.
        See bartlebot/streamer_mode.py for what it applies to and why the placeholder
        is positional rather than a mask.
        """
        return bool(self.get_general("streamer_mode", default=False))

    @streamer_mode.setter
    def streamer_mode(self, value: bool) -> None:
        self.set_general("streamer_mode", bool(value))

    @property
    def default_friend_name(self) -> str:
        """Wizard name teleport_to_friend ports to when a bot names no hooked player.

        Machine configuration rather than bot content: the target is normally an idler
        account of the user's own, so it describes this machine's situation the way
        Safety Delay does. No bot file stores it, and none may — see the "No real player
        data" section of CLAUDE.md, and the guard in tests/test_bot_library.py.
        """
        return str(self.get_general("default_friend_name", default="") or "")

    @default_friend_name.setter
    def default_friend_name(self, value: str) -> None:
        self.set_general("default_friend_name", str(value or "").strip())

    @property
    def recent_friend_names(self) -> list[str]:
        """Previously used teleport targets, newest first. Capped at 20.

        Autocomplete only. Nothing reads this at run time — a suggestion list must never
        be able to decide where a bot teleports.
        """
        return list(self.get("recent_friend_names", default=[]))

    def add_recent_friend_name(self, name: str) -> None:
        """Prepend name to recent_friend_names, deduplicating and capping at 20.

        Matching is case-insensitive so a differently-cased retype does not add a second
        entry, and the newest spelling wins.
        """
        name = (name or "").strip()
        if not name:
            return
        recent = [n for n in self.recent_friend_names if n.lower() != name.lower()]
        recent.insert(0, name)
        self.set("recent_friend_names", value=recent[:20])
        self.save()

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

    # ------------------------------------------------------------------
    # Bot option overrides
    # ------------------------------------------------------------------

    def get_option_override(self, bot_id: str, name: str) -> Any:
        """Return the user's stored value for one bot option, or UNSET.

        ``UNSET`` rather than ``None``: a stored ``[]`` or ``false`` is an explicit
        choice, and only the *absence* of the key means "I have no value here".
        """
        row = self.get("bot_option_overrides", bot_id) or {}
        if not isinstance(row, dict) or name not in row:
            return bot_options.UNSET
        return row[name]

    def set_option_override(self, bot_id: str, name: str, value: Any) -> None:
        """Store the user's value for one bot option and persist."""
        overrides = dict(self.get("bot_option_overrides") or {})
        row = dict(overrides.get(bot_id) or {})
        row[name] = value
        overrides[bot_id] = row
        self.set("bot_option_overrides", value=overrides)
        self.save()

    def drop_option_override(self, bot_id: str, name: str) -> None:
        """Forget the user's value for one bot option, so it resolves again."""
        overrides = dict(self.get("bot_option_overrides") or {})
        row = dict(overrides.get(bot_id) or {})
        if name not in row:
            return
        row.pop(name)
        if row:
            overrides[bot_id] = row
        else:
            overrides.pop(bot_id, None)
        self.set("bot_option_overrides", value=overrides)
        self.save()

    def rename_option_override(self, bot_id: str, old_name: str, new_name: str) -> None:
        """Move the user's stored value from *old_name* to *new_name*.

        Renaming an option in the editor already fixes up every transition that
        guards on it; the override store has to join that fixup or the rename
        silently resets the user's value -- the failure mode that ruled out
        per-node-id keys for the friend name in #102.
        """
        if old_name == new_name:
            return
        overrides = dict(self.get("bot_option_overrides") or {})
        row = dict(overrides.get(bot_id) or {})
        if old_name not in row:
            return
        row[new_name] = row.pop(old_name)
        overrides[bot_id] = row
        self.set("bot_option_overrides", value=overrides)
        self.save()

    # There is deliberately no prune_option_overrides() analogue of
    # prune_active_strategies. A strategy id names a file that can genuinely
    # vanish; an option name is composed from whichever nodes are in the graph
    # right now, on every graph edit -- so cutting a node and pasting it back
    # would drop the user's value in between. A dead row cannot affect a
    # resolution either, since resolution only asks about names that exist.
    # Overrides go when the user deletes the option.

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
        """Return (min_width, min_height) based on the configured game resolution.

        This is the floor the window may be *dragged* to, so it is measured against the
        panel minimums rather than the configured layout sizes -- unlike
        ``layout_budget``, which asks the opposite question and must not assume the user
        will crush their sidebars. See bartlebot/game_sizing.py for the two directions.
        """
        gw, gh = self.game_resolution
        min_w = SIDEBAR_MIN_WIDTH * 2 + gw + SPLITTER_HANDLE_PX * 2
        min_h = gh + BOTCANVAS_MIN_HEIGHT + MENU_AND_STATUS_PX
        return min_w, min_h

    def layout_budget(self, screen_size: tuple[int, int]) -> tuple[int, int]:
        """The game-grid space *screen_size* leaves once this layout is placed on it.

        The cap that decides which resolutions may be launched. Reads the user's
        configured sidebar widths and canvas height -- see ``game_sizing.screen_budget``
        for why it must not read the minimums instead.
        """
        return screen_budget(
            screen_size,
            left_sidebar=int(self.get_general("left_sidebar_width", default=300)),
            right_sidebar=int(self.get_general("right_sidebar_width", default=300)),
            bottom_section=int(self.get_general("bottom_section_height", default=220)),
            splitter_handle=SPLITTER_HANDLE_PX,
        )
