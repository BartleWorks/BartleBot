"""Which shipped copy of the shared Official set to read, when more than one product ships it.

Duel strategies, player strategies and decks are one library for every product by this
vendor (:mod:`bartlebot.shared.strategy_library`). Each product ships the Official ones
in its own install directory, pinned from one source, so with two products installed
there are two shipped copies. The rule (``docs/SHARED_CONTENT.md``, and #27):

- **The higher set version is used whole.** Items from two sets are never mixed,
  because a duel links player strategies from its own set.
- **A set's version decides, not an install date,** so reinstalling the older product
  changes nothing. On a tie the running product's own copy is used.
- **Uninstalling a product removes its key,** and the other falls back to its own set.

Each installed product records itself under ``HKCU\\Software\\Bartleby\\<product>``
(#27): :data:`INSTALL_DIR_VALUE` and :data:`SHARED_SET_VERSION_VALUE` are the two values
read here. The shared set sits at the same place under every install directory as it
does under this one, so another product's install directory is a shipped root exactly
as ours is.

The registry is read through :class:`InstallRegistry`, so a test supplies the keys it
wants and the suite never reads the machine it runs on.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bartlebot.shared.vendor import PRODUCT, VENDOR

logger = logging.getLogger(__name__)

#: The version of the shared Official set this build ships. Raised whenever a shared
#: Official item is added, changed or removed; it is never lowered. Nothing leaves a set
#: after 1.0.0, since a user's link to it would then point at nothing. Before 1.0.0 there
#: are no users: version 6 removed two duels (#223), and version 8 retired a player strategy (#212).
SHARED_SET_VERSION = 8

#: ``HKCU\Software\Bartleby``: one subkey per installed product.
REGISTRY_KEY = rf"Software\{VENDOR}"
#: The product's install directory (``REG_SZ``).
INSTALL_DIR_VALUE = "InstallDir"
#: The version of the shared Official set the product ships (``REG_DWORD``).
SHARED_SET_VERSION_VALUE = "SharedSetVersion"


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
    """The install keys under ``HKCU\\Software\\Bartleby``. A key missing a value is skipped."""

    def install_keys(self) -> list[InstallKey]:
        import winreg  # noqa: PLC0415 - Windows only

        keys: list[InstallKey] = []
        try:
            vendor = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY)
        except OSError:
            return keys
        with vendor:
            index = 0
            while True:
                try:
                    product = winreg.EnumKey(vendor, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(vendor, product) as key:
                        install_dir, _ = winreg.QueryValueEx(key, INSTALL_DIR_VALUE)
                        version, _ = winreg.QueryValueEx(key, SHARED_SET_VERSION_VALUE)
                    keys.append(InstallKey(product, Path(str(install_dir)), int(version)))
                except (OSError, ValueError, TypeError):
                    logger.debug("Skipping install key %s: a value is missing or unreadable", product, exc_info=True)
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
    best_version, best_root, best_product = SHARED_SET_VERSION, own_root, product
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
