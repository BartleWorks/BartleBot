"""One content hash for every kind of item, and the history of hashes each item keeps (#169).

Three questions need a way to tell one version of an item from another: whether the
Official item under a user's override has changed since the override was made, whether
an item received in a share file is a newer copy of one the user has or a separate edit
of it (Stage 5), and whether a shared item's signature changed. All three compare
:func:`content_sha256`, and nothing else hashes an item.

THE HASH
--------

An item's *content* is what its file would say: its validated model, dumped the way
its ``save`` writes it (each model's ``file_dump()``), with the keys sorted and the
history fields themselves left out (:data:`HISTORY_FIELDS`). Hashing content rather
than file bytes means a file reformatted, or written by an older version with a default
spelled out, hashes the same as long as it says the same thing. Only the top level is
stripped: a nested field that happens to be named ``history`` is content.

Template releases are the exception, and keep hashing their file bytes
(:mod:`bartlebot.template_store`): a released file must never change at all, so there a
reformat *is* a change.

THE HISTORY
-----------

Bots, subgraphs, duel and player strategies, deck presets and Bot Combat Strategies each
carry a ``history``: the content hashes of what earlier saves replaced, newest first,
each hash once, at most :data:`HISTORY_CAP` of them. Only a save adds one, and only a
save that changes the content; loading never writes. :func:`record_save` is what every
store calls before it writes, and it reads the history from what the save replaces on
disk, not from the item in memory, so an item opened as a document, rebuilt from it,
or carried through undo needs to carry nothing. An item whose ancestry falls off the
cap reads as a separate edit on import, which is the safe outcome.

THE OFFICIAL BASE
-----------------

A user's override of an Official subgraph or strategy records ``official_sha256``: the
content hash of the Official item it was made from, set by the save that first makes
the override and kept by every save after it. When the Official item later hashes to
something else -- an app update, or the other product's newer shared set taking over --
:func:`official_updated` says so, and the item's pill reads *Official (modified) ·
Official updated*. An override made before this existed has none, and is never said to
be out of date: there is no record of what it was made from.

Nothing here imports from outside :mod:`bartlebot.shared`: an item is anything with a
``file_dump()``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

#: The most hashes an item's history keeps.
HISTORY_CAP = 64

#: The fields that record an item's lineage rather than its content, left out of its hash.
HISTORY_FIELDS = frozenset({"history", "official_sha256"})


def content_sha256(item: Any) -> str:
    """The SHA-256 of ``item``'s content: what its file would say, keys sorted, history fields left out."""
    data = {k: v for k, v in item.file_dump().items() if k not in HISTORY_FIELDS}
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def next_history(history: Iterable[str], replaced: str, current: str) -> list[str]:
    """The history after a save that replaced content hashing to ``replaced`` with ``current``.

    Unchanged when the save changed nothing. Otherwise ``replaced`` goes to the front,
    moved there if an earlier save already listed it (content edited back to what it
    once was), and the oldest falls off past :data:`HISTORY_CAP`.
    """
    kept = list(history)
    if replaced == current:
        return kept[:HISTORY_CAP]
    return [replaced, *(h for h in kept if h != replaced)][:HISTORY_CAP]


def record_save(item: Any, *, stored: Any | None = None, shipped: Any | None = None) -> None:
    """Set ``item``'s history, and its Official base if it has one, for a save about to write it.

    ``stored`` is the item's file in the user root as it is now, and ``shipped`` the
    Official item of the same id; either is None when there is none, and ``shipped`` is
    None whenever the two roots are one directory, since nothing is Official there. The
    save replaces ``stored``, or, when there is no user file yet, ``shipped``: the first
    save of an override descends from the Official item. Changes ``item`` in place, so
    the caller's copy says what was written.
    """
    replaced = stored if stored is not None else shipped
    if replaced is not None:
        item.history = next_history(replaced.history, content_sha256(replaced), content_sha256(item))
    else:
        item.history = list(item.history)[:HISTORY_CAP]
    if "official_sha256" not in type(item).model_fields:
        return
    if shipped is None:
        item.official_sha256 = None
    elif stored is None:
        item.official_sha256 = content_sha256(shipped)
    else:
        item.official_sha256 = stored.official_sha256


def official_updated(item: Any, shipped: Any | None) -> bool:
    """Whether the Official item under ``item``, a user's override of it, has changed since the override was made.

    False when there is no Official item, or when ``item`` has no record of the one it
    was made from.
    """
    base = getattr(item, "official_sha256", None)
    return shipped is not None and base is not None and content_sha256(shipped) != base
