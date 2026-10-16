"""Which shipped copy of the shared Official set to read, when more than one product ships it.

Duel strategies, player strategies and decks are one library for every product by this
vendor (:mod:`bartlebot.shared.strategy_library`). Each product ships the Official ones
in its own install directory, pinned from one source, so with two products installed
there are two shipped copies. The rule (``docs/EPICS.md``, *Official and shared content*, and #27):

- **The higher set version is used whole.** Items from two sets are never mixed,
  because a duel links player strategies from its own set.
- **A set's version decides, not an install date,** so reinstalling the older product
  changes nothing. On a tie the running product's own copy is used.
- **Uninstalling a product removes its key,** and the other falls back to its own set.

Each set says its own version, in :data:`SET_FILE` beside it, and that file is the only
place the version lives. It is raised whenever a shared Official item is added, changed
or removed, and never lowered. Nothing leaves a set after 1.0.0, since a user's link to
it would then point at nothing. Before 1.0.0 there are no users: version 6 removed two
duels (#223), and version 8 retired a player strategy (#212). Version 8 also first
shipped the decks.

Each installed product records its install directory under
``HKCU\\Software\\Bartleby\\<product>`` (:mod:`bartlebot.shared.install_key`). The
shared set sits at the same place under every install directory as it does under this
one, so another product's install directory is a shipped root exactly as ours is, and
its version is read from its own :data:`SET_FILE`. The registry holds no version, so no
update has to rewrite it.

The registry is read through :class:`InstallRegistry`, so a test supplies the keys it
wants and the suite never reads the machine it runs on.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bartlebot.shared import install_key
from bartlebot.shared.strategy_library import LIBRARY_DIRNAME
from bartlebot.shared.vendor import PRODUCT

logger = logging.getLogger(__name__)

#: Where a set says its version, under the root it ships in: ``{"version": 8}``.
SET_FILE = Path(LIBRARY_DIRNAME) / "official_set.json"


def set_version(root: Path) -> int | None:
    """The version of the shared set under ``root``, or ``None`` when it says none that can be read."""
    try:
        version = json.loads((Path(root) / SET_FILE).read_bytes())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        logger.debug("No readable shared set version under %s", root, exc_info=True)
        return None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


@dataclass(frozen=True)
class InstallKey:
    """One product's install key, as far as the shared set needs it."""

    product: str
    install_dir: Path
    shared_set_version: int


class InstallRegistry(Protocol):
    """Where the installed products are recorded."""

    def install_keys(self) -> list[InstallKey]: ...


class NoRegistry:
    """Nothing installed: every product reads its own set."""

    def install_keys(self) -> list[InstallKey]:
        return []


class WindowsRegistry:
    """The install keys under ``HKCU\\Software\\Bartleby``, each with the version its directory ships.

    A key whose directory is gone, or whose set says no version, is skipped.
    """

    def install_keys(self) -> list[InstallKey]:
        keys: list[InstallKey] = []
        for record in install_key.read_all():
            if not record.install_dir.is_dir():
                logger.debug("Skipping install key %s: %s is not there", record.product, record.install_dir)
                continue
            version = set_version(record.install_dir)
            if version is None:
                continue
            keys.append(InstallKey(record.product, record.install_dir, version))
        return keys


_registry: InstallRegistry | None = None


def registry() -> InstallRegistry:
    """The registry :func:`shipped_root` reads: the real one on Windows unless one was set."""
    if _registry is not None:
        return _registry
    return WindowsRegistry() if sys.platform == "win32" else NoRegistry()


def set_registry(reg: InstallRegistry | None) -> None:
    """Read install keys from ``reg`` from now on; ``None`` goes back to the real registry."""
    global _registry
    _registry = reg
    reset_shipped_root()


#: The other product last reported as supplying the set, so the choice is logged once, not per read.
_reported: str | None = None

#: The roots chosen so far this session, by ``(own_root, product)``. The keys change only when
#: a product is installed or removed, and one dialog must read one set whole, so the registry
#: is read once and the answer kept.
_chosen: dict[tuple[Path, str], Path] = {}


def reset_shipped_root() -> None:
    """Forget the cached choice, so the next :func:`shipped_root` reads the registry again."""
    global _reported
    _chosen.clear()
    _reported = None


def shipped_root(own_root: Path, *, product: str = PRODUCT, reg: InstallRegistry | None = None) -> Path:
    """The install directory whose copy of the shared set is used: ``own_root`` unless another's is newer.

    Another product's key counts only while its install directory exists, so a key an
    uninstall left behind cannot leave the library empty. The registry is read once per
    session; a ``reg`` passed in is always read, and its answer is not kept.
    """
    if reg is None and (cached := _chosen.get((own_root, product))) is not None:
        return cached
    root = _choose(own_root, product, reg or registry())
    if reg is None:
        _chosen[(own_root, product)] = root
    return root


def _choose(own_root: Path, product: str, reg: InstallRegistry) -> Path:
    global _reported
    best_version, best_root, best_product = set_version(own_root) or 0, own_root, product
    for key in reg.install_keys():
        if key.product == product or key.shared_set_version <= best_version:
            continue
        if not key.install_dir.is_dir():
            logger.debug("Ignoring %s's shared set: %s is not there", key.product, key.install_dir)
            continue
        best_version, best_root, best_product = key.shared_set_version, key.install_dir, key.product
    if best_product != product and best_product != _reported:
        logger.info("Using %s's shared Official set (version %d), newer than ours", best_product, best_version)
    _reported = best_product if best_product != product else None
    return best_root
