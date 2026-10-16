"""Each installed product's record of itself: ``HKCU\\Software\\Bartleby\\<product>`` (#27).

Every product by this vendor writes one key when it is installed and deletes it when it
is uninstalled, so each can find the others: the shared Official set picks the newest
copy among the install directories (:mod:`bartlebot.shared.official_set`), and the
uninstall of one product hands the shared file types to another
(:mod:`bartlebot.shared.file_types`).

The key holds only values an update never changes, so no update hook has to rewrite it
and a hook that fails cannot leave it stale:

- :data:`INSTALL_DIR_VALUE`: the folder the product's shipped content sits under, the
  one holding ``strategies/``. In a PyInstaller build that is ``current\\_internal``,
  and Velopack's ``current\\`` keeps its name across updates.
- :data:`EXECUTABLE_VALUE`: the exe that opens the product, Velopack's stub beside
  ``current\\``, whose path also stays the same across updates.

Anything that changes with a release, such as the shared set's version, is read from the
install directory instead, so it can never disagree with the files beside it.

Every function works under :data:`REGISTRY_KEY`, read when it is called, or under the
``base`` it is given, so a test works under a scratch key of its own and never touches
the vendor's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bartlebot.shared.vendor import VENDOR

logger = logging.getLogger(__name__)

#: ``HKCU\Software\Bartleby``: one subkey per installed product.
REGISTRY_KEY = rf"Software\{VENDOR}"
#: The folder the product's shipped content sits under (``REG_SZ``).
INSTALL_DIR_VALUE = "InstallDir"
#: The exe that opens the product, stable across updates (``REG_SZ``).
EXECUTABLE_VALUE = "Executable"


@dataclass(frozen=True)
class InstallRecord:
    """One product's install key."""

    product: str
    install_dir: Path
    executable: Path


def write(product: str, install_dir: Path, executable: Path, *, base: str | None = None) -> None:
    """Record ``product`` as installed, replacing any record it had."""
    import winreg  # noqa: PLC0415 - Windows only

    base = base or REGISTRY_KEY
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{product}") as key:
        winreg.SetValueEx(key, INSTALL_DIR_VALUE, 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(key, EXECUTABLE_VALUE, 0, winreg.REG_SZ, str(executable))


def delete(product: str, *, base: str | None = None) -> None:
    """Remove ``product``'s key, and nothing else: the vendor key stays while another product has one."""
    import winreg  # noqa: PLC0415 - Windows only

    base = base or REGISTRY_KEY
    delete_tree(winreg.HKEY_CURRENT_USER, rf"{base}\{product}")
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base)  # fails while a sibling remains
    except OSError:
        pass


def read_all(*, base: str | None = None) -> list[InstallRecord]:
    """Every product's record. A key missing a value, or with one unreadable, is skipped."""
    import winreg  # noqa: PLC0415 - Windows only

    records: list[InstallRecord] = []
    try:
        vendor = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base or REGISTRY_KEY)
    except OSError:
        return records
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
                    executable, _ = winreg.QueryValueEx(key, EXECUTABLE_VALUE)
                records.append(InstallRecord(product, Path(str(install_dir)), Path(str(executable))))
            except OSError:
                logger.debug("Skipping install key %s: a value is missing", product, exc_info=True)
    return records


def delete_tree(root: int, path: str) -> None:
    """Delete ``path`` and every key under it; nothing when it is not there."""
    import winreg  # noqa: PLC0415 - Windows only

    try:
        winreg.DeleteKeyEx(root, path)
        return
    except FileNotFoundError:
        return
    except OSError:
        pass  # it has subkeys
    with winreg.OpenKey(root, path) as key:
        children = []
        while True:
            try:
                children.append(winreg.EnumKey(key, len(children)))
            except OSError:
                break
    for child in children:
        delete_tree(root, rf"{path}\{child}")
    winreg.DeleteKeyEx(root, path)
