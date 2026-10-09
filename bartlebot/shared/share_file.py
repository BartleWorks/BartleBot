"""Share files: one item, and everything Custom it needs, as one plain JSON file (#170).

A user exports the thing they mean -- a bot, a Bot Combat Strategy, a subgraph, a duel
strategy, a player strategy or a deck -- and the export follows its links. Every format
here is the vendor's, not one product's: BartleBuddy reads and writes the three kinds
this module owns (duel strategies, player strategies and decks), and BartleBot adds its
own kinds on top (:mod:`bartlebot.content.share_export`). ``docs/SHARING.md`` is the reader's
version of this docstring.

THE FORMAT
----------

::

    {
      "format": 1,
      "app": "BartleBot",
      "app_version": "1.0.0",
      "main": {"kind": "bot", "id": "…"},
      "items": [{"kind": "…", "id": "…", "sha256": "…", "history": […], "content": {…},
                 "overrides_official": false}],
      "requires": [{"kind": "subgraph", "id": "…", "sha256": "…"}]
    }

- ``format`` is :data:`SHARE_FORMAT`, the vendor's to raise. A product refuses a file
  whose format is newer than it knows, since its models drop fields they don't know.
- ``app`` and ``app_version`` say which product wrote the file, for the message that asks
  a receiver to update first.
- ``main`` says what the file is for. It sets the extension, and import reads it, never
  the extension alone.
- ``items`` are packed: every Custom item the main item needs, the main item included
  when it is Custom. ``content`` is what the item's own file would say, less the fields
  that record its lineage (:data:`~bartlebot.shared.content_hash.HISTORY_FIELDS`):
  ``history`` travels beside it, and an override's Official base never travels at all.
  ``sha256`` is :func:`~bartlebot.shared.content_hash.content_sha256` of exactly that
  content, so a receiver can check it. An Official (modified) item is packed with
  ``overrides_official`` set: the receiver gets it as a Custom copy, never as an
  override of their own Official item.
- ``requires`` names every Official item the main item needs, by id and the hash of the
  Official item as the sender has it. Official items are never packed: the receiver has
  them from their own install, and a share file cannot make anything Official.

WHAT NEVER TRAVELS
------------------

Anything from settings: which Bot Combat Strategy is active, every option value, the
favorites. Nothing here reads settings, and nothing that writes a share file may.

Shared files are **data only, permanently**: node params, the combat DSL and decks.
Nothing that executes.

DECKS
-----

A deck travels as its name, description, tags and cards (:func:`shareable_deck`). The
legacy ``tc`` field is left out, and so is ``apply_archmastery``: whether applying the
deck sets the school is the owner's decision, and the receiver becomes the owner.

Nothing here imports from outside :mod:`bartlebot.shared`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_serializer

from bartlebot.shared.content_hash import HISTORY_FIELDS, content_sha256
from bartlebot.shared.store_files import Origin
from bartlebot.shared.strategy_models import (
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    PlayerCombatStrategy,
    atomic_write,
)

#: The newest share file format this build reads and writes.
SHARE_FORMAT = 1

#: The kinds this module owns, as a file names them: the library shared by every product.
DUEL_STRATEGY = "duel_strategy"
PLAYER_STRATEGY = "player_strategy"
DECK = "deck"

#: The extension a share file takes, by the kind of its main item. The ``bb`` prefix is the
#: family's, so a sorted folder groups them.
SHARED_EXTENSIONS: dict[str, str] = {
    DUEL_STRATEGY: ".bbduel",
    PLAYER_STRATEGY: ".bbplayer",
    DECK: ".bbdeck",
}

#: What each kind is called in a sentence, and what a file of it is called. The file type
#: names are the ones the installer registers (#27), so Explorer and a save dialog agree.
SHARED_NOUNS: dict[str, str] = {DUEL_STRATEGY: "duel strategy", PLAYER_STRATEGY: "player strategy", DECK: "deck"}
SHARED_FILE_TYPES: dict[str, str] = {
    DUEL_STRATEGY: "Bartleby duel strategy",
    PLAYER_STRATEGY: "Bartleby player strategy",
    DECK: "Bartleby deck",
}


class ItemRef(BaseModel):
    """An item by its kind and id: what ``main`` holds."""

    kind: str
    id: str


class Requirement(ItemRef):
    """An Official item the main item needs, named and never packed."""

    sha256: str


class PackedItem(ItemRef):
    """A Custom item, carried whole."""

    sha256: str
    history: list[str] = Field(default_factory=list)
    content: dict[str, Any]
    #: Set on a user's modified copy of an Official item: the receiver gets it as a copy.
    overrides_official: bool = False
    #: The library folder the sender keeps it in, by name, so the receiver's lands in the
    #: same one: set on a bot only, and left out of the file when there is none. Not
    #: content, so not hashed. An import uses it only when the receiver has that folder.
    folder: str | None = None

    @model_serializer(mode="wrap")
    def _leave_out_no_folder(self, handler):
        data = handler(self)
        if data.get("folder") is None:
            data.pop("folder", None)
        return data


class ShareFile(BaseModel):
    """One share file. See the module docstring."""

    format: int = SHARE_FORMAT
    app: str
    app_version: str
    main: ItemRef
    items: list[PackedItem] = Field(default_factory=list)
    requires: list[Requirement] = Field(default_factory=list)


def shareable_content(item: Any) -> dict[str, Any]:
    """What ``item``'s file would say, less the fields that record its lineage.

    Taken through JSON, so it is exactly what the share file holds: a deck slot's ``1``
    is ``"1"`` here, as it is once written.
    """
    data = {k: v for k, v in item.file_dump().items() if k not in HISTORY_FIELDS}
    return json.loads(json.dumps(data, ensure_ascii=False))


def shareable_deck_data(deck: DeckData) -> DeckData:
    """``deck`` as it travels: no legacy ``tc``, and no ``apply_archmastery``, which is the owner's call."""
    return deck.model_copy(deep=True, update={"tc": {}, "apply_archmastery": None})


def shareable_deck(preset: DeckPreset) -> DeckPreset:
    """``preset`` as a deck file carries it: see :func:`shareable_deck_data`."""
    return preset.model_copy(deep=True, update={"deck": shareable_deck_data(preset.deck)})


def write(share: ShareFile, path: str | Path) -> None:
    """Write ``share`` to ``path``, through a temporary file so a crash never half-writes it."""
    atomic_write(share.model_dump(), path)


def read(path: str | Path) -> ShareFile:
    """The share file at ``path``. Raises on a file that is not one."""
    with open(path, encoding="utf-8") as f:
        return ShareFile.model_validate(json.load(f))


class Disposition(Enum):
    """What an export does with one item, as its summary says it."""

    #: A Custom item: packed.
    PACKED = "packed"
    #: A user's modified copy of an Official item: packed, and received as a copy.
    COPY = "copy"
    #: An Official item: named, never packed.
    NAMED = "named"
    #: Linked, but not in the sender's library, so neither packed nor named.
    MISSING = "missing"


@dataclass(frozen=True)
class Entry:
    """One line of the export summary: an item, and what the export does with it."""

    kind: str
    id: str
    name: str
    #: The item's own description: the user's text, so the summary shows it.
    description: str
    disposition: Disposition


class Collector:
    """Gathers one share file: each item once, in the order the links first reach it.

    :meth:`add` decides from an item's origin whether it is packed or named, and says
    whether the item is new, so the caller follows an item's links only the first time.
    """

    def __init__(self, main_kind: str, main_id: str) -> None:
        self.main = ItemRef(kind=main_kind, id=main_id)
        self._seen: set[tuple[str, str]] = set()
        self.items: list[PackedItem] = []
        self.requires: list[Requirement] = []
        self.entries: list[Entry] = []

    def add(self, kind: str, item: Any, origin: Origin, *, folder: str | None = None) -> bool:
        """Pack ``item``, or name it when it is Official. False, doing nothing, when it is already here.

        ``origin`` is where the sender's copy is. Missing means an item in hand that no
        store holds, such as a Bot Combat Strategy's own strategy, which is Custom.
        ``folder`` is the library folder a packed bot is in (:attr:`PackedItem.folder`).
        """
        key = (kind, item.id)
        if key in self._seen:
            return False
        self._seen.add(key)
        if origin is Origin.SHIPPED:
            self.requires.append(Requirement(kind=kind, id=item.id, sha256=content_sha256(item)))
            disposition = Disposition.NAMED
        else:
            overrides = origin is Origin.SHIPPED_OVERRIDE
            self.items.append(
                PackedItem(
                    kind=kind,
                    id=item.id,
                    sha256=content_sha256(item),
                    history=list(item.history),
                    content=shareable_content(item),
                    overrides_official=overrides,
                    folder=folder,
                )
            )
            disposition = Disposition.COPY if overrides else Disposition.PACKED
        self.entries.append(Entry(kind, item.id, item.name, getattr(item, "description", None) or "", disposition))
        return True

    def note_missing(self, kind: str, item_id: str, name: str = "") -> None:
        """Record a link to ``item_id`` that the sender's library cannot resolve. Noted once."""
        key = (kind, item_id)
        if key in self._seen:
            return
        self._seen.add(key)
        self.entries.append(Entry(kind, item_id, name or item_id, "", Disposition.MISSING))

    def share_file(self, app: str, app_version: str) -> ShareFile:
        """The file, as gathered so far."""
        return ShareFile(
            app=app, app_version=app_version, main=self.main, items=list(self.items), requires=list(self.requires)
        )


#: How a caller finds a player strategy a duel links: the strategy and where it is, or None.
PlayerFinder = Callable[[str], "tuple[PlayerCombatStrategy, Origin] | None"]


def collect_duel(
    collector: Collector,
    duel: DuelCombatStrategy,
    origin: Origin,
    find_player: PlayerFinder,
    *,
    inside: Iterable[str] = (),
) -> None:
    """Add ``duel`` and the player strategies it links.

    ``inside`` names player strategies an enclosing item already carries, such as a Bot
    Combat Strategy's own, which are neither packed again nor missing.
    """
    if not collector.add(DUEL_STRATEGY, duel, origin):
        return
    carried = set(inside)
    for pcs_id in dict.fromkeys(duel.linked_pcs_ids()):
        if pcs_id in carried:
            continue
        found = find_player(pcs_id)
        if found is None:
            collector.note_missing(PLAYER_STRATEGY, pcs_id)
            continue
        collector.add(PLAYER_STRATEGY, *found)


def collect_deck(collector: Collector, preset: DeckPreset, origin: Origin) -> None:
    """Add ``preset`` as a deck file carries it (:func:`shareable_deck`).

    An Official deck is named by the hash of the deck the receiver has, so it is left whole.
    """
    collector.add(DECK, preset if origin is Origin.SHIPPED else shareable_deck(preset), origin)
