"""The shared strategy library: player strategies, duel strategies and decks, read and written by every product.

The library sits at the vendor root (``%APPDATA%/Bartleby/strategies/``), beside
``accounts.json``, so BartleBot and BartleBuddy read and write one copy. Like every
two-root store it is read across a shipped root and a user root, user first, and
written only to the user root:

  strategies/player/   — PlayerCombatStrategy files
  strategies/duel/     — DuelCombatStrategy   files
  strategies/decks/    — DeckPreset           files

Every function here takes both roots as arguments. Which directories they are is the
product's to say (:func:`bartlebot.content.store_roots.resolve_shared_roots`), and which
install directory's shipped copy is read is :mod:`bartlebot.shared.official_set`'s.

TWO PRODUCTS, ONE FILE
----------------------

Two products can have the same file open, so every write here is checked first:

- **A save checks before it writes.** A load hands back the file's *fingerprint*: the
  SHA-256 of the user-root file's bytes as they were read, or ``None`` when the item
  had no user file. A save or delete passes the fingerprint it was loaded with, and
  writes nothing if the user root no longer holds those bytes, raising
  :class:`ChangedOnDisk`. The caller then offers to reload, or to save its copy under
  a new id. A save with no fingerprint expects no user file, which is what creating
  an item means.
- **An older product never overwrites a newer format.** A file whose ``format`` is
  newer than :data:`~bartlebot.shared.strategy_models.LIBRARY_FORMAT` loads read-only,
  because the models drop fields they don't know; saving or deleting it raises
  :class:`NewerFormat`.

The check and the write are not one atomic step, so two saves landing in the same
instant can still race. That window is milliseconds; the one this closes is the
minutes a dialog stays open.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from bartlebot.shared.store_files import delete_by_id, find_by_id, index_dir, record_save_in, save_by_id
from bartlebot.shared.strategy_models import LIBRARY_FORMAT, DeckPreset, DuelCombatStrategy, PlayerCombatStrategy

logger = logging.getLogger(__name__)

#: The folder under a root that holds the library.
LIBRARY_DIRNAME = "strategies"


class LibraryKind(Enum):
    """The three kinds the shared library holds."""

    PLAYER = "player"
    DUEL = "duel"
    DECK = "deck"


_SUBDIRS = {LibraryKind.PLAYER: "player", LibraryKind.DUEL: "duel", LibraryKind.DECK: "decks"}

MODELS: dict[LibraryKind, type[BaseModel]] = {
    LibraryKind.PLAYER: PlayerCombatStrategy,
    LibraryKind.DUEL: DuelCombatStrategy,
    LibraryKind.DECK: DeckPreset,
}


def kind_dir(root: Path, kind: LibraryKind) -> Path:
    """Where ``kind``'s files are under ``root``."""
    return Path(root) / LIBRARY_DIRNAME / _SUBDIRS[kind]


class ChangedOnDisk(Exception):
    """Nothing was written: the user-root file is not the one the item was loaded from."""

    def __init__(self, kind: LibraryKind, item_id: str, name: str) -> None:
        self.kind = kind
        self.item_id = item_id
        self.name = name
        super().__init__(
            f"{kind.value} strategy {name!r} ({item_id}) was changed by another program since it was loaded"
        )


class NewerFormat(Exception):
    """Nothing was written: the file is in a format newer than this build knows."""

    def __init__(self, kind: LibraryKind, item_id: str, name: str, file_format: int) -> None:
        self.kind = kind
        self.item_id = item_id
        self.name = name
        self.file_format = file_format
        super().__init__(
            f"{kind.value} strategy {name!r} ({item_id}) is in format {file_format}, newer than this version "
            f"knows ({LIBRARY_FORMAT}); update to edit it"
        )


@dataclass(frozen=True)
class Entry:
    """One item as a load found it."""

    item: BaseModel
    #: The SHA-256 of the user-root file's bytes as loaded, or ``None`` when it had none.
    fingerprint: str | None

    @property
    def read_only(self) -> bool:
        """Whether its file is in a format newer than this build knows, so it may not be saved over."""
        return is_newer_format(self.item)


def is_newer_format(item: BaseModel) -> bool:
    """Whether ``item`` was read from a file in a format newer than :data:`LIBRARY_FORMAT`."""
    return getattr(item, "format", 1) > LIBRARY_FORMAT


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse(kind: LibraryKind, data: bytes) -> BaseModel:
    return MODELS[kind].model_validate(json.loads(data))


def entries(shipped: Path, user: Path, kind: LibraryKind) -> dict[str, Entry]:
    """Every ``kind`` item under both roots, by id, the user's winning. One that does not load is skipped.

    Each file is read once, so an item and its fingerprint always describe the same bytes.
    """
    # One directory when the roots are one (dev mode, or a single-root test sandbox):
    # everything in it is the user's.
    roots = [(user, True)] if shipped == user else [(shipped, False), (user, True)]
    found: dict[str, Entry] = {}
    for root, is_user in roots:
        for path in index_dir(kind_dir(root, kind)).values():
            try:
                data = path.read_bytes()
                item = _parse(kind, data)
            except Exception:
                logger.warning("Skipping unloadable %s", path, exc_info=True)
                continue
            found[item.id] = Entry(item, _digest(data) if is_user else None)  # type: ignore[attr-defined]
    return found


def load(shipped: Path, user: Path, kind: LibraryKind, item_id: str) -> Entry:
    """``item_id``, from the user root, else the shipped one. Raises ``FileNotFoundError`` if neither has it."""
    path = find_by_id(kind_dir(user, kind), item_id)
    if path is not None:
        data = path.read_bytes()
        return Entry(_parse(kind, data), _digest(data))
    path = find_by_id(kind_dir(shipped, kind), item_id)
    if path is None:
        raise FileNotFoundError(f"No {kind.value} strategy declares id {item_id} in {user} or {shipped}")
    return Entry(_parse(kind, path.read_bytes()), None)


def fingerprint(user: Path, kind: LibraryKind, item_id: str) -> str | None:
    """The fingerprint ``item_id`` would load with now: its user-root file's SHA-256, or ``None``."""
    path = find_by_id(kind_dir(user, kind), item_id)
    if path is None:
        return None
    try:
        return _digest(path.read_bytes())
    except OSError:
        return None


def _check(user: Path, kind: LibraryKind, item_id: str, name: str, expected: str | None) -> None:
    """Raise unless the user root still holds what ``expected`` describes, in a format this build may overwrite."""
    path = find_by_id(kind_dir(user, kind), item_id)
    data = None
    if path is not None:
        try:
            data = path.read_bytes()
        except OSError:
            data = None
    current = _digest(data) if data is not None else None
    if current != expected:
        logger.warning("Not writing %s strategy %s: it was changed by another program", kind.value, item_id)
        raise ChangedOnDisk(kind, item_id, name)
    if data is not None:
        try:
            on_disk = json.loads(data)
        except ValueError:
            return
        file_format = on_disk.get("format", 1) if isinstance(on_disk, dict) else 1
        if isinstance(file_format, int) and file_format > LIBRARY_FORMAT:
            raise NewerFormat(kind, item_id, name, file_format)


def save(
    user: Path,
    kind: LibraryKind,
    item: BaseModel,
    *,
    expected: str | None = None,
    shipped: Path | None = None,
    record: bool = True,
) -> str:
    """Write ``item`` to the user root, if it still holds what ``expected`` describes. Returns the new fingerprint.

    ``expected`` is the fingerprint the item was loaded with; ``None``, the default,
    expects no user file, as for a new item or an edit of a shipped one. Raises
    :class:`ChangedOnDisk` or :class:`NewerFormat`, having written nothing.

    ``shipped`` is the shipped root, which the history and an override's Official base
    are recorded against (:mod:`bartlebot.shared.content_hash`); leave it None when
    there is none, or when it is the user root. Recorded into ``item`` itself, and only
    once the checks have passed. ``record=False`` writes the history ``item`` already
    has, as an import does: an item received in a share file arrives with its own (#171).
    """
    item_id, name = item.id, item.name  # type: ignore[attr-defined]
    if is_newer_format(item):
        raise NewerFormat(kind, item_id, name, item.format)  # type: ignore[attr-defined]
    _check(user, kind, item_id, name, expected)
    if record:
        shipped_dir = kind_dir(shipped if shipped is not None else user, kind)
        record_save_in(shipped_dir, kind_dir(user, kind), item, MODELS[kind].load)  # type: ignore[attr-defined]
    path = save_by_id(kind_dir(user, kind), item_id, name, item.save)  # type: ignore[attr-defined]
    return _digest(path.read_bytes())


def delete(user: Path, kind: LibraryKind, item_id: str, name: str = "", *, expected: str | None = None) -> None:
    """Remove ``item_id``'s user-root file, if it still holds what ``expected`` describes.

    On an override of a shipped item this is a revert. Raises :class:`ChangedOnDisk` or
    :class:`NewerFormat`, having removed nothing.
    """
    _check(user, kind, item_id, name or item_id, expected)
    delete_by_id(kind_dir(user, kind), item_id)
