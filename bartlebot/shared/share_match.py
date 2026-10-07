"""Import's matching rules: what an item received in a share file is to the receiver's own (#171).

Every product imports the same way, so the rules are the vendor's: BartleBot and
BartleBuddy both match each item by its id, and tell its relation to the receiver's
copy of that id from the content hashes and histories every item keeps
(:mod:`bartlebot.shared.content_hash`). Nobody is ever asked for an id.

==============================================  ============  ==========================
the incoming item                               outcome       what may be done
==============================================  ============  ==========================
has the same hash as the receiver's copy        ``SAME``      nothing
came in as a copy before                        ``COPIED``    nothing
has an Official id, or ``overrides_official``   ``OFFICIAL``  Import as copy, always
has an id the receiver doesn't have             ``NEW``       Add
has the receiver's hash in its history          ``NEWER``     **Update**, copy, keep
has a hash the receiver's history contains      ``OLDER``     nothing: theirs is newer
matches none of these                           ``CONFLICT``  **Import as copy**,
                                                              Replace, Keep mine
==============================================  ============  ==========================

The first action listed is the default (:data:`CHOICES`). A conflict defaults to Import
as copy, never to Replace: an import that overwrote by id without asking is how Grafana
users lost dashboards.

AN IMPORT TWICE CHANGES NOTHING
-------------------------------

A copy's id is not random. :func:`copy_id` derives it from the item's kind, its id and
the hash it was sent with, so the same file imported again finds the copy it made the
first time (``COPIED``) and writes nothing, and the same item reaching the receiver in
two files becomes one copy, not two.

Nothing here imports from outside :mod:`bartlebot.shared`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from bartlebot.shared.strategy_models import DuelCombatStrategy


class Outcome(Enum):
    """What an incoming item is to the receiver's copy of the same id. See the module docstring."""

    NEW = "new"
    SAME = "same"
    NEWER = "newer"
    OLDER = "older"
    CONFLICT = "conflict"
    OFFICIAL = "official"
    COPIED = "copied"


class Action(Enum):
    """What an import does with one item."""

    #: Written under its own id, which the receiver doesn't have.
    ADD = "add"
    #: Written over the receiver's copy, which it is a newer version of.
    UPDATE = "update"
    #: Written over the receiver's copy, which it is not a version of.
    REPLACE = "replace"
    #: Written under a new id (:func:`copy_id`), with every link to it in the file following.
    COPY = "copy"
    #: Nothing written: links to it stay on the receiver's own item of that id.
    KEEP = "keep"


#: The actions each outcome allows, the default first. One action means there is nothing to ask.
CHOICES: dict[Outcome, tuple[Action, ...]] = {
    Outcome.NEW: (Action.ADD,),
    Outcome.SAME: (Action.KEEP,),
    Outcome.COPIED: (Action.KEEP,),
    Outcome.OFFICIAL: (Action.COPY,),
    Outcome.NEWER: (Action.UPDATE, Action.COPY, Action.KEEP),
    Outcome.OLDER: (Action.KEEP,),
    Outcome.CONFLICT: (Action.COPY, Action.REPLACE, Action.KEEP),
}

#: The actions that write something.
WRITES = frozenset({Action.ADD, Action.UPDATE, Action.REPLACE, Action.COPY})


@dataclass(frozen=True)
class Held:
    """The receiver's copy of an id, as matching needs it: its content hash and its history."""

    sha256: str
    history: tuple[str, ...] = ()


def match(
    sha256: str,
    history: Iterable[str],
    held: Held | None,
    *,
    official: bool = False,
    copied: bool = False,
) -> Outcome:
    """What an item hashing to ``sha256``, with ``history``, is to ``held``, the receiver's copy of its id.

    ``official`` says the item may never take its own id here: the receiver has an
    Official item of that id, or the sender's was a change to an Official one.
    ``copied`` says the copy :func:`copy_id` names already exists, because this same
    item was imported as a copy before.
    """
    if held is not None and held.sha256 == sha256:
        return Outcome.SAME
    if copied:
        return Outcome.COPIED
    if official:
        return Outcome.OFFICIAL
    if held is None:
        return Outcome.NEW
    if held.sha256 in set(history):
        return Outcome.NEWER
    if sha256 in held.history:
        return Outcome.OLDER
    return Outcome.CONFLICT


#: Fixed, so every product derives the same copy id from the same item.
_COPY_NAMESPACE = uuid.UUID("5d0b3c1e-8f7a-4e62-9c1d-2b6f0a4e7c31")


def copy_id(kind: str, item_id: str, sha256: str) -> str:
    """The id an item of ``kind`` gets when imported as a copy: the same for the same item, every time."""
    return str(uuid.uuid5(_COPY_NAMESPACE, f"{kind}\n{item_id}\n{sha256}"))


def relink_duel(duel: DuelCombatStrategy, players: Mapping[str, str]) -> None:
    """Point ``duel``'s player links at the copies ``players`` maps their ids to. Changes ``duel`` in place."""
    for assignment in duel.player_assignments:
        pcs_id = assignment.player_combat_strategy_id
        if pcs_id in players:
            assignment.player_combat_strategy_id = players[pcs_id]


def update_message(app: str, app_version: str, product: str) -> str:
    """Why a file from a newer product, or needing a newer Official item, is refused."""
    return f"Made with a newer {app} ({app_version}). Update {product}, then import again."
