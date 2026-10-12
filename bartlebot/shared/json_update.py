"""Changing one part of a JSON file that another product may be changing too.

Two products by this publisher can be open at once, and each can write the same file in
the vendor root. A store that loads once and writes the whole file silently replaces the
other's change with its own stale copy. The fix is to never write from memory: read the
file as it is now, change only the part this writer owns, and write that back.

:func:`update_json` does that, and checks that nothing else wrote in between:

1. read the file's bytes, and take a fingerprint of them;
2. apply ``mutate`` to the parsed dict;
3. read the file's fingerprint again. If it changed, another writer got in between, so
   start over from step 1 against what they wrote. Up to :data:`UPDATE_RETRIES` times;
4. write the result to a temporary file beside the target and ``os.replace`` it over.

A crash before the rename leaves the previous file whole. The rename itself is atomic, so
a reader sees the old file or the new one, never half of either.

The window between the check in step 3 and the rename in step 4 is not closed, and cannot
be without a lock file that a crashed product would leave behind. It is a few
microseconds, against writes that follow a user's click, and what it can lose is one
key that lost a race, not the file. Where that is not good enough (the strategy library
has authored content to lose) the writer refuses and asks instead; this is for a
preference, where the last writer of a key winning is the right answer.

A file that is absent starts as ``{}``. A file that is not a JSON object is also treated
as ``{}`` and replaced, with a warning: the alternative is a store that can never save
again until the user deletes a file they did not know existed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How many times a write that was overtaken is read and applied again before giving up.
UPDATE_RETRIES = 5


class UpdateConflict(OSError):
    """Another writer kept changing the file; :data:`UPDATE_RETRIES` attempts all lost."""


def update_json(path: Path, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Apply *mutate* to the JSON object at *path* and write it back. The record written.

    *mutate* edits the dict in place and may run more than once, so it must derive its
    change from the dict it is given and not from state it consumes. It should touch only
    the keys its caller owns; every other key, including ones this version has never
    heard of, is written back as it was read.

    Raises :class:`OSError` when the file cannot be written, and :class:`UpdateConflict`
    (an ``OSError``) when it was overtaken on every attempt. Nothing is written then.
    """
    for _ in range(UPDATE_RETRIES):
        before = _read(path)
        record = _parse(before, path)
        mutate(record)
        if _digest(_read(path)) != _digest(before):
            continue
        _replace(path, record)
        return record
    raise UpdateConflict(f"{path} changed under {UPDATE_RETRIES} attempts in a row")


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _parse(data: bytes | None, path: Path) -> dict[str, Any]:
    if data is None:
        return {}
    try:
        record = json.loads(data.decode("utf-8"))
    except ValueError:
        logger.warning("%s is not valid JSON; replacing it", path)
        return {}
    if not isinstance(record, dict):
        logger.warning("%s is not a JSON object; replacing it", path)
        return {}
    return record


def _replace(path: Path, record: dict[str, Any]) -> None:
    """Write *record* to a temporary beside *path*, then rename it over. Cleans up on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
