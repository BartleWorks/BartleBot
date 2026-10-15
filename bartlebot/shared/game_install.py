"""Where Wizard101 is installed, for every product by this vendor (epic #215, Stage 2).

wizwalker's ``get_wiz_install`` looks in two places: the KingsIsle launcher's folder and
the launcher's uninstall key in the registry. A Steam install is in neither, so launching
from one failed with "Wizard101 install not found." :func:`find_install` looks further, in
this order:

==========================  ============================================================
place                       what is read
==========================  ============================================================
the folder the user chose   :data:`INSTALL_DIR_KEY` in ``preferences.json``
the launcher's folder       :data:`LAUNCHER_DIR`, wizwalker's ``DEFAULT_INSTALL``
the launcher's registry     ``InstallLocation`` under :data:`UNINSTALL_KEY`
each Steam library          Steam's root from the registry, its ``libraryfolders.vdf``,
                            and the library holding Wizard101's app manifest
==========================  ============================================================

A folder counts only when :func:`is_install` finds the game client in it, so a stale
choice, an emptied launcher folder or a library Steam still lists after the drive is gone
is passed over for the next place.

The folder is a preference, not a BartleBot setting, because BartleBuddy launches the game
from the same place. This module reads the registry and the disk and nothing else: no Qt,
and no wizwalker. The app hands what it finds to wizwalker (``clients/install_location``).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import vdf

if TYPE_CHECKING:
    from bartlebot.shared.preferences import PreferenceStore

logger = logging.getLogger(__name__)

#: The folder the user chose, in ``preferences.json``. Absent: the folder is detected.
INSTALL_DIR_KEY = "game.install_dir"

#: What makes a folder an install: the game client, under it.
CLIENT = Path("Bin") / "WizardGraphicalClient.exe"

#: Where the KingsIsle launcher installs the game; wizwalker's ``DEFAULT_INSTALL``.
LAUNCHER_DIR = Path("C:/ProgramData/KingsIsle Entertainment/Wizard101")

#: The KingsIsle launcher's uninstall key, under ``HKEY_CURRENT_USER``, as wizwalker reads it.
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{A9E27FF5-6294-46A8-B8FD-77B1DECA3021}"

#: Wizard101's app id on Steam: its manifest is ``steamapps/appmanifest_<id>.acf``.
STEAM_APP_ID = "799960"

#: Where a library's list sits under Steam's root: ``steamapps`` today, ``config`` on older clients.
LIBRARY_FOLDERS = (Path("steamapps") / "libraryfolders.vdf", Path("config") / "libraryfolders.vdf")


class InstallSource(Enum):
    """Where :func:`find_install` found the folder."""

    STORED = "stored"
    LAUNCHER = "launcher"
    REGISTRY = "registry"
    STEAM = "steam"


#: Each source as the user reads it, after "Found in" or "Chosen".
SOURCE_NAMES: dict[InstallSource, str] = {
    InstallSource.STORED: "the folder you chose",
    InstallSource.LAUNCHER: "the KingsIsle launcher's folder",
    InstallSource.REGISTRY: "the KingsIsle launcher's install record",
    InstallSource.STEAM: "a Steam library",
}


@dataclass(frozen=True)
class FoundInstall:
    """A folder holding the game, and the place it was found."""

    path: Path
    source: InstallSource


class InstallPlaces(Protocol):
    """The machine's answers: the launcher's folder, its registry record, and Steam's root."""

    def launcher_dir(self) -> Path: ...

    def uninstall_location(self) -> Path | None: ...

    def steam_root(self) -> Path | None: ...


class WindowsPlaces:
    """The real places: the launcher's folder, and the registry for the rest."""

    def launcher_dir(self) -> Path:
        return LAUNCHER_DIR

    def uninstall_location(self) -> Path | None:
        return _registry_value("HKEY_CURRENT_USER", UNINSTALL_KEY, "InstallLocation")

    def steam_root(self) -> Path | None:
        # SteamPath is the user's own; InstallPath is the machine's, written by the installer.
        return _registry_value("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath") or _registry_value(
            "HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"
        )


class NoPlaces:
    """A machine with nothing installed: only a stored folder can be found."""

    def launcher_dir(self) -> Path:
        return Path("\0nowhere")

    def uninstall_location(self) -> Path | None:
        return None

    def steam_root(self) -> Path | None:
        return None


def _registry_value(hive: str, key: str, name: str) -> Path | None:
    import winreg  # noqa: PLC0415 - Windows only

    try:
        with winreg.OpenKey(getattr(winreg, hive), key, 0, winreg.KEY_READ) as handle:
            value, _ = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    return Path(str(value)) if value else None


_places: InstallPlaces | None = None


def places() -> InstallPlaces:
    """The places :func:`find_install` looks: the real ones on Windows unless some were set."""
    if _places is not None:
        return _places
    return WindowsPlaces() if sys.platform == "win32" else NoPlaces()


def set_places(new: InstallPlaces | None) -> None:
    """Look in *new* from now on; None goes back to the real places."""
    global _places
    _places = new


# -- a folder -----------------------------------------------------------------------


def is_install(path: Path | str | None) -> bool:
    """Whether *path* holds the game: ``Bin/WizardGraphicalClient.exe`` is in it."""
    if not path:
        return False
    try:
        return (Path(path) / CLIENT).is_file()
    except OSError:
        return False


# -- Steam --------------------------------------------------------------------------


def parse_library_folders(text: str) -> list[Path]:
    """The library folders a ``libraryfolders.vdf`` lists, in its order.

    Two layouts are in use. Steam since 2021 writes each library as a block::

        "libraryfolders" { "0" { "path" "C:\\\\Program Files (x86)\\\\Steam" ... } }

    and older clients wrote each as a bare value beside a few counters::

        "LibraryFolders" { "TimeNextStatsReport" "..." "1" "D:\\\\SteamLibrary" }

    A library is a numbered entry either way. Text that is not VDF lists none.
    """
    try:
        record = vdf.loads(text)
    except (SyntaxError, ValueError, TypeError):
        logger.warning("Steam's libraryfolders.vdf could not be read, so no Steam library is searched", exc_info=True)
        return []
    root = next((value for key, value in record.items() if key.lower() == "libraryfolders"), None)
    if not isinstance(root, dict):
        return []
    folders: list[Path] = []
    for key, value in root.items():
        if not key.isdigit():
            continue
        path = value.get("path") if isinstance(value, dict) else value
        if isinstance(path, str) and path:
            folders.append(Path(path))
    return folders


def steam_libraries(where: InstallPlaces | None = None) -> list[Path]:
    """Every Steam library: Steam's own folder first, then each its list names, without repeats."""
    root = (where or places()).steam_root()
    if root is None:
        return []
    libraries = [root]
    for relative in LIBRARY_FOLDERS:
        try:
            text = (root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        libraries.extend(parse_library_folders(text))
        break
    unique: dict[str, Path] = {}
    for library in libraries:
        unique.setdefault(str(library).replace("/", "\\").rstrip("\\").casefold(), library)
    return list(unique.values())


def steam_install(library: Path) -> Path | None:
    """The game's folder in *library*, when the library holds Wizard101's app manifest."""
    manifest = library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf"
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    installdir = "Wizard101"
    try:
        state = next((value for key, value in vdf.loads(text).items() if key.lower() == "appstate"), {})
        installdir = next((v for k, v in state.items() if k.lower() == "installdir" and v), installdir)
    except (SyntaxError, ValueError, TypeError, AttributeError):
        logger.debug("Wizard101's Steam manifest in %s could not be read; trying its usual folder", library)
    return library / "steamapps" / "common" / installdir


# -- finding it ---------------------------------------------------------------------


def stored_install_dir(prefs: PreferenceStore) -> Path | None:
    """The folder the user chose, whether or not it still holds the game."""
    value = prefs.get(INSTALL_DIR_KEY)
    return Path(value) if isinstance(value, str) and value else None


def candidates(prefs: PreferenceStore | None, where: InstallPlaces | None = None) -> Iterator[FoundInstall]:
    """Each place to look, in order, as a folder that may or may not hold the game.

    With *prefs* None the stored folder is left out: what detection alone would find.
    """
    where = where or places()
    if prefs is not None and (stored := stored_install_dir(prefs)) is not None:
        yield FoundInstall(stored, InstallSource.STORED)
    yield FoundInstall(where.launcher_dir(), InstallSource.LAUNCHER)
    if (registered := where.uninstall_location()) is not None:
        yield FoundInstall(registered, InstallSource.REGISTRY)
    for library in steam_libraries(where):
        if (folder := steam_install(library)) is not None:
            yield FoundInstall(folder, InstallSource.STEAM)


def find_install(prefs: PreferenceStore, where: InstallPlaces | None = None) -> FoundInstall | None:
    """The first place in :func:`candidates` that holds the game, or None."""
    return next((found for found in candidates(prefs, where) if is_install(found.path)), None)


def detect_install(where: InstallPlaces | None = None) -> FoundInstall | None:
    """What :func:`find_install` finds without the stored folder: Settings' Detect."""
    return next((found for found in candidates(None, where) if is_install(found.path)), None)


def set_install_dir(prefs: PreferenceStore, path: Path | str) -> bool:
    """Store *path* as the folder chosen. False, writing nothing, for a folder without the game."""
    if not is_install(path):
        return False
    return prefs.set(INSTALL_DIR_KEY, str(Path(path)))


def clear_install_dir(prefs: PreferenceStore) -> bool:
    """Forget the folder chosen, so it is detected again. Whether that reached disk."""
    return prefs.clear(INSTALL_DIR_KEY)
