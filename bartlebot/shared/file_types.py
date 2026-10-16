"""The share file types, registered per user so Explorer names them and a double-click opens them (#27).

A registration is two keys under ``HKCU\\Software\\Classes``, which needs no elevation:

- **the ProgID** (``Bartleby.DuelStrategy``), whose default value is the description
  Explorer shows in place of the extension, with an icon and an open command naming
  the product's exe and the file;
- **the extension** (``.bbduel``), whose default value names the ProgID, and which lists
  it under ``OpenWithProgids``.

Explorer reads its associations once and keeps them, so every change ends with
:func:`notify`, ``SHChangeNotify(SHCNE_ASSOCCHANGED)``, or it goes unseen until a sign-out.

The three types of the library every product shares (:data:`SHARED_TYPES`) are written
the same way by each product, with one ProgID, so whichever registered last opens them,
and uninstalling one product points them at another (``docs/INSTALLER.md``). Each product
lists its own types itself, since ``bartlebot.shared`` knows nothing of BartleBot's kinds.

Every function works under :data:`CLASSES_KEY`, read when it is called, or under the
``classes`` it is given, so a test works under a scratch key of its own and never
changes the machine's associations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bartlebot.shared.install_key import delete_tree
from bartlebot.shared.share_file import SHARED_EXTENSIONS, SHARED_FILE_TYPES
from bartlebot.shared.vendor import VENDOR

logger = logging.getLogger(__name__)

#: Where per-user file types live.
CLASSES_KEY = r"Software\Classes"
#: ``SHChangeNotify``'s event for "a file association changed".
SHCNE_ASSOCCHANGED = 0x08000000


@dataclass(frozen=True)
class FileType:
    """One registered share file type."""

    extension: str
    prog_id: str
    description: str


def prog_id(owner: str, kind: str) -> str:
    """``owner`` and ``kind`` as a ProgID: ``("Bartleby", "duel_strategy")`` is ``Bartleby.DuelStrategy``."""
    return f"{owner}.{''.join(word.capitalize() for word in kind.split('_'))}"


def file_types(owner: str, extensions: dict[str, str], descriptions: dict[str, str]) -> tuple[FileType, ...]:
    """One :class:`FileType` per kind in ``extensions``, its ProgID under ``owner``."""
    return tuple(FileType(ext, prog_id(owner, kind), descriptions[kind]) for kind, ext in extensions.items())


#: The shared library's types: one ProgID each, under the vendor, whichever product writes them.
SHARED_TYPES = file_types(VENDOR, SHARED_EXTENSIONS, SHARED_FILE_TYPES)


def open_command(executable: Path) -> str:
    """The command a double-click runs: the exe, given the file."""
    return f'"{executable}" "%1"'


def register(types: tuple[FileType, ...], executable: Path, *, classes: str | None = None) -> None:
    """Point each of ``types`` at ``executable``, replacing whatever it was registered to."""
    import winreg  # noqa: PLC0415 - Windows only

    hkcu = winreg.HKEY_CURRENT_USER
    classes = classes or CLASSES_KEY
    for t in types:
        with winreg.CreateKey(hkcu, rf"{classes}\{t.prog_id}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, t.description)
        with winreg.CreateKey(hkcu, rf"{classes}\{t.prog_id}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{executable}",0')
        with winreg.CreateKey(hkcu, rf"{classes}\{t.prog_id}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, open_command(executable))
        with winreg.CreateKey(hkcu, rf"{classes}\{t.extension}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, t.prog_id)
        with winreg.CreateKey(hkcu, rf"{classes}\{t.extension}\OpenWithProgids") as key:
            winreg.SetValueEx(key, t.prog_id, 0, winreg.REG_NONE, b"")


def unregister(types: tuple[FileType, ...], *, classes: str | None = None) -> None:
    """Remove each of ``types``: its ProgID, and its extension's key while that still names the ProgID.

    An extension some other program has claimed since is left to it, with only our
    ProgID taken off its ``OpenWithProgids``.
    """
    import winreg  # noqa: PLC0415 - Windows only

    hkcu = winreg.HKEY_CURRENT_USER
    classes = classes or CLASSES_KEY
    for t in types:
        delete_tree(hkcu, rf"{classes}\{t.prog_id}")
        ext = rf"{classes}\{t.extension}"
        try:
            with winreg.OpenKey(hkcu, ext) as key:
                owner, _ = winreg.QueryValueEx(key, "")
        except OSError:
            owner = None
        if owner == t.prog_id:
            delete_tree(hkcu, ext)
            continue
        try:
            with winreg.OpenKey(hkcu, rf"{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, t.prog_id)
        except OSError:
            pass


def registered_command(t: FileType, *, classes: str | None = None) -> str | None:
    """The open command ``t`` is registered with, or ``None`` when it is not registered."""
    import winreg  # noqa: PLC0415 - Windows only

    try:
        path = rf"{classes or CLASSES_KEY}\{t.prog_id}\shell\open\command"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            command, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return None
    return str(command)


def notify() -> None:
    """Tell Explorer the associations changed, so it shows the new descriptions now."""
    import ctypes  # noqa: PLC0415 - Windows only

    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)
