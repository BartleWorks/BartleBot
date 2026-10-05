"""``ui_state.json``: view state the app may lose, kept apart from ``settings.json``.

Which folding groups a reader left open is not a setting. Nobody edits it on purpose,
nothing breaks without it, and it grows with every surface that folds. The inspector
apps keep it out of their settings for those reasons -- Unreal in an editor-state ini,
Unity in its rebuildable ``Library``, VS Code and JetBrains in workspace state -- and
#142's survey settled on the same here: a separate file in this product's directory,
read and written by this module rather than by ``SettingsManager``.

Three rules make it safe to lose:

* **Only departures from a default are stored.** A group folded the way it starts has
  no entry, so most surfaces never write one, and a default that changes later reaches
  everyone who never touched that group.
* **Missing or unreadable means defaults.** A corrupt file is logged and treated as
  empty; it is never a reason to refuse to draw a surface.
* **Entries a surface no longer has are pruned** the next time that surface reads its
  folds, so a removed group does not linger in the file forever.

Keys are a surface id and a group id (Unreal's class-and-category scheme), never a
particular bot or instance.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from bartlebot.shared.vendor import product_dir

logger = logging.getLogger(__name__)

UI_STATE_FILE = "ui_state.json"

_FOLDS = "folds"


class UiState:
    """``ui_state.json``, read afresh by every call.

    Holds no copy between calls, so two surfaces each holding one -- three Settings tabs,
    each with its own folding group -- cannot write back a stale snapshot over the
    other's entry.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else product_dir() / UI_STATE_FILE
        self._data: dict[str, Any] = {}

    @property
    def path(self) -> Path:
        return self._path

    def folds(self, surface: str, defaults: dict[str, bool]) -> dict[str, bool]:
        """Whether each of *surface*'s groups is collapsed: the stored answer, else its default.

        *defaults* names every group the surface has, so a stored group missing from it
        is one the surface no longer draws, and is dropped here.
        """
        self._data = self._read()
        stored = self._surface(surface)
        stale = [group for group in stored if group not in defaults]
        if stale:
            for group in stale:
                del stored[group]
            self._tidy(surface)
            self._write()
        return {group: bool(stored.get(group, default)) for group, default in defaults.items()}

    def set_collapsed(self, surface: str, group: str, collapsed: bool, *, default: bool) -> None:
        """Remember *group*'s fold, storing nothing when it matches *default*."""
        self._data = self._read()
        stored = self._surface(surface)
        if collapsed == default:
            if group not in stored:
                return
            del stored[group]
            self._tidy(surface)
        else:
            if stored.get(group) is collapsed:
                return
            stored[group] = collapsed
            self._data.setdefault(_FOLDS, {})[surface] = stored
        self._write()

    # ------------------------------------------------------------------

    def _surface(self, surface: str) -> dict[str, Any]:
        folds = self._data.get(_FOLDS)
        if not isinstance(folds, dict):
            return {}
        stored = folds.get(surface)
        if not isinstance(stored, dict):
            return {}
        return stored

    def _tidy(self, surface: str) -> None:
        """Drop a surface with nothing stored, and the folds key with no surfaces."""
        folds = self._data.get(_FOLDS)
        if not isinstance(folds, dict):
            return
        if not folds.get(surface):
            folds.pop(surface, None)
        if not folds:
            self._data.pop(_FOLDS, None)

    def _read(self) -> dict[str, Any]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            logger.warning("Could not read %s; using default view state", self._path, exc_info=True)
            return {}
        try:
            data = json.loads(text)
        except ValueError:
            logger.warning("%s is not valid JSON; using default view state", self._path)
            return {}
        if not isinstance(data, dict):
            logger.warning("%s does not hold an object; using default view state", self._path)
            return {}
        return data

    def _write(self) -> None:
        """Write through a temporary file, so a crash mid-write leaves the old file whole."""
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            logger.warning("Could not write %s; view state will not be remembered", self._path, exc_info=True)
