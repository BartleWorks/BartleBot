"""What checking a combat DSL said, kept on disk so a new session does not parse it again.

THE COST

Checking a DSL is a full Earley parse of it against wizsprinter's grammar, and that parse
grows faster than the text does: 4 ms for a one-move line, 300 ms for a typical shipped
player strategy, 800 ms for the longest. ``combat_runner.dsl_check`` keeps every answer in
memory, so each string is parsed once per process -- but once per process is every time
the app starts, and the first thing to ask is a dialog drawing a health pill on every card
it lists. The shipped strategies alone were 3.8 s of parsing on the first open of the
Combat Strategies dialog, every session, for text that had not changed since the last.

THE CACHE

A JSON file under the product directory mapping a hash of each DSL to what checking it
said. The answer is a pure function of the text and of the code and data that check it,
so the file carries a *fingerprint* of those -- the caller supplies it -- and is thrown
away whole when the fingerprint differs. A wizsprinter bump, a new spell-name snapshot or
an edit to the checker therefore re-checks everything once, rather than serving an answer
the new code would not give.

Entries are kept in the order they were added, and the oldest go once there are more than
:data:`MAX_ENTRIES`: typing in a DSL editor adds one per pause, and nothing else bounds it.

The file is a cache, so every failure to read or write it is ignored: a missing, corrupt
or foreign file is an empty cache, and a write that fails leaves the answer in memory only.
Writes replace the file through a temporary one, so two sessions writing at once lose an
entry at worst, never the file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from bartleby.shared.vendor import product_dir

logger = logging.getLogger(__name__)

#: How many answers the file keeps. Each is a hash and a short message: ~100 bytes.
MAX_ENTRIES = 2000

_FILE_NAME = "dsl_checks.json"


def cache_path() -> Path:
    """``%APPDATA%/Bartleby/BartleBot/cache/dsl_checks.json`` -- never the install directory."""
    return product_dir() / "cache" / _FILE_NAME


def text_key(text: str) -> str:
    """The key an entry is stored under: the DSL's SHA-256, so the file holds no strategy text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DslCheckCache:
    """One cache file, read on first use and rewritten on every new answer.

    Thread-safe: the strategy dialog checks DSLs on a worker thread while the GUI thread
    can check one of its own.
    """

    def __init__(self, path: Path, fingerprint: str, *, max_entries: int = MAX_ENTRIES) -> None:
        self._path = path
        self._fingerprint = fingerprint
        self._max_entries = max_entries
        self._entries: dict[str, Any] | None = None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def get(self, text: str) -> Any | None:
        """The stored answer for *text*, or None if there is none."""
        with self._lock:
            return self._loaded().get(text_key(text))

    def put(self, text: str, value: Any) -> None:
        """Store *value* (JSON-serializable) as the answer for *text*, and write the file."""
        with self._lock:
            entries = self._loaded()
            key = text_key(text)
            if entries.get(key) == value:
                return
            entries.pop(key, None)
            entries[key] = value
            while len(entries) > self._max_entries:
                del entries[next(iter(entries))]
            self._write(entries)

    def _loaded(self) -> dict[str, Any]:
        if self._entries is None:
            self._entries = self._read()
        return self._entries

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("fingerprint") != self._fingerprint:
            return {}
        entries = data.get("entries")
        return dict(entries) if isinstance(entries, dict) else {}

    def _write(self, entries: dict[str, Any]) -> None:
        tmp_path = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp", prefix=self._path.stem + "_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"fingerprint": self._fingerprint, "entries": entries}, f, separators=(",", ":"))
            os.replace(tmp_path, self._path)
        except OSError:
            logger.debug("Could not write the DSL check cache at %s", self._path, exc_info=True)
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


_instances: dict[tuple[Path, str], DslCheckCache] = {}
_instances_lock = threading.Lock()


def shared_cache(fingerprint: str) -> DslCheckCache:
    """The cache at :func:`cache_path` for *fingerprint*, one instance per path.

    Keyed by path rather than made once, because the path follows ``APPDATA``: each test
    has its own, and a cache bound to the first one would write into another test's.
    """
    key = (cache_path(), fingerprint)
    with _instances_lock:
        cache = _instances.get(key)
        if cache is None:
            cache = _instances[key] = DslCheckCache(key[0], fingerprint)
        return cache
