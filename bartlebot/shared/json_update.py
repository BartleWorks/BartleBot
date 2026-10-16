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
4. write the result to a temporary file beside the target and ``os.replace`` it over. On
   Windows the rename is refused while another process has the file open, as the other
   product does whenever it reads it; a refused rename waits briefly and starts over from
   step 1, within the same :data:`UPDATE_RETRIES`.

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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How many times a write that was overtaken, or whose rename was refused, is read and
#: applied again before giving up.
UPDATE_RETRIES = 5

#: Seconds to wait after Windows refuses the rename, before trying again. A reader holds the
#: file for well under this, and the writes follow a click, so a short block is no cost.
REPLACE_RETRY_DELAY = 0.05


class UpdateConflict(OSError):
    """Another writer kept changing the file; :data:`UPDATE_RETRIES` attempts all lost."""


def update_json(path: Path, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Apply *mutate* to the JSON object at *path* and write it back. The record written.

    *mutate* edits the dict in place and may run more than once, so it must derive its
    change from the dict it is given and not from state it consumes. It should touch only
    the keys its caller owns; every other key, including ones this version has never
    heard of, is written back as it was read.

    Raises :class:`OSError` when the file cannot be written (a :class:`PermissionError`
    when the rename was refused on every attempt), and :class:`UpdateConflict` (an
    ``OSError``) when it was overtaken on every attempt. Nothing is written then.
    """
    refused: PermissionError | None = None
    for _ in range(UPDATE_RETRIES):
        before = _read(path)
        record = _parse(before, path)
        mutate(record)
        if _digest(_read(path)) != _digest(before):
            refused = None
            continue
        try:
            _replace(path, record)
        except PermissionError as exc:
            # Windows refuses to rename over a file another process has open, which the
            # other product does for the instant it reads it. Wait it out and start over,
            # from a fresh read, so the check above still covers the rename.
            refused = exc
            time.sleep(REPLACE_RETRY_DELAY)
            continue
        return record
    if refused is not None:
        raise refused  # refused every time: a real permission problem, not a reader
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
        _keep_unreadable(path, data, "is not valid JSON")
        return {}
    if not isinstance(record, dict):
        _keep_unreadable(path, data, "is not a JSON object")
        return {}
    return record


def _keep_unreadable(path: Path, data: bytes, why: str) -> None:
    """Copy a file about to be replaced as ``{}`` to ``<name>.unreadable``, and say so.

    The file may be accounts.json, every account record the user has, and the copy is what
    lets it be mended by hand rather than re-entered (#154).
    """
    kept = path.with_name(f"{path.name}.unreadable")
    try:
        kept.write_bytes(data)
    except OSError:
        logger.warning("%s %s; replacing it, and it could not be kept", path, why)
        return
    logger.warning("%s %s; replacing it, and keeping what it held as %s", path, why, kept.name)


def _replace(path: Path, record: dict[str, Any]) -> None:
    """Write *record* to a temporary beside *path*, then rename it over. Cleans up on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
            # On disk before the rename: without it a power cut can leave the new name on an empty file (#154).
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
