"""Persist and retrieve the list of recently opened bot files."""

import json
from pathlib import Path

_RECENT_PATH = Path.home() / ".bartlebot_recent.json"
_MAX = 8


def load() -> list[str]:
    """Return up to _MAX recently opened paths, newest first."""
    try:
        data = json.loads(_RECENT_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(p) for p in data[:_MAX]]
    except Exception:
        pass
    return []


def add(path: str) -> list[str]:
    """Prepend path to the recent list, persist it, and return the updated list."""
    paths = [p for p in load() if p != path]
    paths.insert(0, path)
    paths = paths[:_MAX]
    try:
        _RECENT_PATH.write_text(json.dumps(paths, indent=2), encoding="utf-8")
    except Exception:
        pass
    return paths
