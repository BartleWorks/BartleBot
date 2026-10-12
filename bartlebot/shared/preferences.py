"""The user's preferences, in a file every product by this publisher reads and writes.

``%APPDATA%/Bartleby/preferences.json`` is for a value that describes the *user* rather
than any one product: the combat defaults BartleBuddy fights with as much as BartleBot
does. Where a value lives:

==============================  ======================================================
file                            holds
==============================  ======================================================
``preferences.json``            a preference both products read, whichever wrote it
``accounts.json``               accounts and launch profiles (``account_store``)
``BartleBot/settings.json``     what only this app has: canvas layout, sidebars, delays
==============================  ======================================================

The record is ``{"version": 1, "<dotted.key>": value, ...}``, flat, so a key is spelled
the same in the file, in a log and in code (``combat.default_duel.solo``).

Every write goes through :func:`~bartlebot.shared.json_update.update_json`: read the file
as it is, change one key, write it back. So two stores on one directory, in one process or
two, setting different keys both keep their values, and a key written by a newer version
survives a write by this one. Nothing is cached: :meth:`PreferenceStore.get` reads the
file, so a value the other product just wrote is the value you get.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bartlebot.shared.json_update import update_json
from bartlebot.shared.vendor import vendor_dir

logger = logging.getLogger(__name__)

#: Schema version of the stored record.
SCHEMA_VERSION = 1

#: The filename beneath the vendor directory.
FILENAME = "preferences.json"

#: The one key that is not a preference.
_VERSION_KEY = "version"


class PreferenceStore:
    """Dotted-key preferences in the vendor-level file.

    Constructed with its directory, as ``AccountStore`` is, so a test or a sibling
    product points it anywhere without patching a module global.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._path = (directory or vendor_dir()) / FILENAME

    @property
    def path(self) -> Path:
        """Where this store reads and writes."""
        return self._path

    def get(self, key: str, default: Any = None) -> Any:
        """The stored value of *key*, or *default* when it is absent or the file unreadable."""
        if key == _VERSION_KEY:
            return default
        return self._read().get(key, default)

    def set(self, key: str, value: Any) -> bool:
        """Store *value* under *key*. Whether it reached disk; never raises for I/O.

        Called from GUI slots, where an exception escaping looks like the app breaking.
        A failed write is logged and the previous file is left whole.
        """
        if key == _VERSION_KEY:
            raise ValueError(f"{_VERSION_KEY!r} is the file's schema version, not a preference")

        def mutate(record: dict[str, Any]) -> None:
            record.setdefault(_VERSION_KEY, SCHEMA_VERSION)
            record[key] = value

        return self._update(mutate)

    def clear(self, key: str) -> bool:
        """Remove *key*, so :meth:`get` falls back to its default. Whether it reached disk."""
        if key not in self._read():
            return True

        def mutate(record: dict[str, Any]) -> None:
            record.pop(key, None)

        return self._update(mutate)

    def _update(self, mutate: Callable[[dict[str, Any]], None]) -> bool:
        try:
            update_json(self._path, mutate)
        except (OSError, TypeError, ValueError):
            logger.exception("Could not write the shared preferences at %s", self._path)
            return False
        return True

    def _read(self) -> dict[str, Any]:
        try:
            record = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.exception("The shared preferences at %s are not readable", self._path)
            return {}
        return record if isinstance(record, dict) else {}
