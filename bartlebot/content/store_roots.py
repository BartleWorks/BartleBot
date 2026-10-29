"""The ordered pair of storage roots every two-root store reads and writes across.

  shipped — the project root (``bartlebot.paths.project_root``), which is the install
            directory: read-only, what the repo ships and version-controls, and
            replaced whole by every app update.
  user    — ``%APPDATA%/Bartleby/BartleBot`` (``bartleby.shared.vendor.product_dir``),
            where every app-initiated write lands.

Reads resolve user-first: a user file with the same id shadows the shipped one.
Writes and deletes only ever touch the user root, so editing a shipped file copies
it into the user root on save, keyed by the same id — an override, not a fork.
Deleting the user copy of a shipped id is "revert to default"; the shipped file
itself is never touched by the app. Bots and templates are copied rather than linked,
so they never override: a user file declaring a shipped one's id is ignored
(``docs/EPICS.md``, *Official and shared content*).

The shared strategy library (player strategies, duel strategies and decks) has roots
of its own, :func:`resolve_shared_roots`: its user root is the vendor directory, where
every product by this vendor reads and writes it, and its shipped root is whichever
installed product ships the newest copy (:mod:`bartleby.shared.official_set`).

Every store function takes an optional ``root``, which sets the shipped root and,
unless ``user_root`` is also given, doubles as the user root — so a single sandbox
directory in a test behaves like one flat store. Pass ``user_root`` explicitly to
exercise the shadowing.

Developer mode (``BARTLEBOT_DEV`` set) points every default user root at the project
root, so the two roots are one directory. Authoring a shipped definition, strategy,
template or bot then writes straight into the repo, where it can be diffed and
committed, instead of into an ``%APPDATA%`` copy that shadows the repo's and lets it
go stale. It only changes the default: a caller passing ``root`` or ``user_root`` is
unaffected.

Finding, naming and writing a file by the id inside it lives in
:mod:`bartleby.shared.store_files`, which the shared library uses too; it is
re-exported here because every store imports it from this module.
"""

from __future__ import annotations

import os
from pathlib import Path

from bartleby.shared import official_set
from bartleby.shared.store_files import (
    Origin,
    declared_id,
    delete_by_id,
    find_by_id,
    index_dir,
    load_by_id,
    merged_by_id,
    origin_in,
    save_by_id,
    slugify,
)
from bartleby.shared.vendor import product_dir, vendor_dir

from bartlebot.packaging.product import NAME as PRODUCT
from bartlebot.paths import project_root

__all__ = [
    "DEV_ENV_VAR",
    "LISTED_ORIGINS",
    "OFFICIAL_UPDATED",
    "SOURCE_LABELS",
    "Origin",
    "declared_id",
    "delete_by_id",
    "dev_mode",
    "find_by_id",
    "index_dir",
    "is_inside",
    "load_by_id",
    "merged_by_id",
    "origin_in",
    "pill_text",
    "resolve_bot_strategy_roots",
    "resolve_roots",
    "resolve_shared_roots",
    "save_by_id",
    "slugify",
    "source_label",
]

#: Set to anything but empty or ``0`` to author shipped content in place; see the module docstring.
DEV_ENV_VAR = "BARTLEBOT_DEV"


def dev_mode() -> bool:
    """Whether the default user root is redirected to the project root."""
    return os.environ.get(DEV_ENV_VAR, "") not in ("", "0")


#: What every surface calls where an item comes from: bots, templates, subgraphs and
#: strategies alike, so no two of them describe the same origin in two vocabularies.
#: "Official" means shipped, which means tested and proven in game: it is worked out from
#: the root a file is in, never read from the file, so nothing a user receives can claim
#: it (#149). "Custom" rather than "User": it is the user's own, in one word.
SOURCE_LABELS = {
    Origin.SHIPPED: "Official",
    Origin.SHIPPED_OVERRIDE: "Official (modified)",
    Origin.USER: "Custom",
    Origin.MISSING: "Missing",
}

#: The origins a list can show, in the order a Source filter offers them. Missing is not
#: one: a missing item is never listed.
LISTED_ORIGINS: tuple[Origin, ...] = (Origin.SHIPPED, Origin.SHIPPED_OVERRIDE, Origin.USER)


#: What an override's pill adds when the Official item under it has changed since the
#: override was made (#169): Revert takes the new one. Never a filter word: the item is
#: still Official (modified).
OFFICIAL_UPDATED = "Official updated"


def pill_text(origin: Origin, *, official_updated: bool = False) -> str:
    """What an item's Source pill says: its label, and on a stale override, *· Official updated*."""
    text = SOURCE_LABELS.get(origin, origin.value)
    if official_updated and origin is Origin.SHIPPED_OVERRIDE:
        text = f"{text} · {OFFICIAL_UPDATED}"
    return text


def source_label(origin: Origin) -> str:
    """``Source: <label>`` for an item's origin, as shown to the user."""
    return f"Source: {SOURCE_LABELS.get(origin, origin.value)}"


def resolve_roots(root: str | Path | None, user_root: str | Path | None) -> tuple[Path, Path]:
    """``(shipped, user)`` for the argument convention every store function shares."""
    shipped = Path(root) if root is not None else project_root()
    if user_root is not None:
        user = Path(user_root)
    elif root is not None:
        user = Path(root)
    elif dev_mode():
        user = project_root()
    else:
        user = product_dir(PRODUCT)
    return shipped, user


def resolve_bot_strategy_roots(root: str | Path | None, user_root: str | Path | None) -> tuple[Path, Path]:
    """``(shipped, user)`` for Bot Combat Strategies, which developer mode leaves alone (#180).

    No Bot Combat Strategy ships, so there is nothing to author in place: one made while
    authoring a bot holds a deck captured from a client, and belongs under ``%APPDATA%``
    rather than in the repo.
    """
    if root is None and user_root is None:
        return project_root(), product_dir(PRODUCT)
    return resolve_roots(root, user_root)


def resolve_shared_roots(root: str | Path | None, user_root: str | Path | None) -> tuple[Path, Path]:
    """``(shipped, user)`` for the shared strategy library, under the same argument convention.

    The default user root is the vendor directory, shared by every product. The default
    shipped root is the install directory with the newest copy of the shared Official
    set: this one, unless another product ships a newer set. In developer mode both are
    the project root, whatever else is installed, so the repo's set is the one authored.
    """
    if root is not None:
        return resolve_roots(root, user_root)
    if dev_mode():
        shipped = project_root()
        return shipped, Path(user_root) if user_root is not None else shipped
    shipped = official_set.shipped_root(project_root(), product=PRODUCT)
    return shipped, Path(user_root) if user_root is not None else vendor_dir()


def is_inside(path: str | Path, folder: str | Path) -> bool:
    """Whether ``path`` is ``folder`` or anywhere beneath it, compared after resolving both."""
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except OSError:
        return False
