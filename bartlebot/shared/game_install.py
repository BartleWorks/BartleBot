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
a running game              the folder above ``Bin`` of each running game client
==========================  ============================================================

The running game is last because it is there only while the game runs. It finds an
install every other place missed, since a user who can play has one somewhere, so the
app stores a folder found that way as the folder chosen as soon as it uses it
(``clients/install_location.apply_install``): next time the game may not be running.

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
#: The game client's process, as Windows names a running one.
CLIENT_EXE = CLIENT.name

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
    RUNNING = "running"


#: Each source as the user reads it, after "Found in" or "Chosen".
SOURCE_NAMES: dict[InstallSource, str] = {
    InstallSource.STORED: "the folder you chose",
    InstallSource.LAUNCHER: "the KingsIsle launcher's folder",
    InstallSource.REGISTRY: "the KingsIsle launcher's install record",
    InstallSource.STEAM: "a Steam library",
    InstallSource.RUNNING: "the game running now",
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

    def running_clients(self) -> list[Path]: ...


class WindowsPlaces:
    """The real places: the launcher's folder, the registry, and the running processes."""

    def launcher_dir(self) -> Path:
        return LAUNCHER_DIR

    def uninstall_location(self) -> Path | None:
        return _registry_value("HKEY_CURRENT_USER", UNINSTALL_KEY, "InstallLocation")

    def steam_root(self) -> Path | None:
        # SteamPath is the user's own; InstallPath is the machine's, written by the installer.
        return _registry_value("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath") or _registry_value(
            "HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"
        )

    def running_clients(self) -> list[Path]:
        return running_executables(CLIENT_EXE)


class NoPlaces:
    """A machine with nothing installed: only a stored folder can be found."""

    def launcher_dir(self) -> Path:
        return Path("\0nowhere")

    def uninstall_location(self) -> Path | None:
        return None

    def steam_root(self) -> Path | None:
        return None

    def running_clients(self) -> list[Path]:
        return []


def _registry_value(hive: str, key: str, name: str) -> Path | None:
    import winreg  # noqa: PLC0415 - Windows only

    try:
        with winreg.OpenKey(getattr(winreg, hive), key, 0, winreg.KEY_READ) as handle:
            value, _ = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    return Path(str(value)) if value else None


def running_executables(name: str) -> list[Path]:
    """The full path of each running process whose executable is *name*, without repeats.

    A process snapshot names each executable, and only the matches are opened, with the
    least access that reads a path, so a process of another user or a protected one is
    passed over rather than failing the rest. Empty off Windows, or when the snapshot
    cannot be taken.
    """
    if sys.platform != "win32":
        return []
    import ctypes  # noqa: PLC0415 - Windows only
    from ctypes import wintypes as w  # noqa: PLC0415

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", w.DWORD),
            ("cntUsage", w.DWORD),
            ("th32ProcessID", w.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", w.DWORD),
            ("cntThreads", w.DWORD),
            ("th32ParentProcessID", w.DWORD),
            ("pcPriClassBase", w.LONG),
            ("dwFlags", w.DWORD),
            ("szExeFile", w.WCHAR * 260),
        ]

    snap_processes = 0x2
    kernel32 = _kernel32()
    kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]

    snapshot = kernel32.CreateToolhelp32Snapshot(snap_processes, 0)
    if not snapshot or snapshot == w.HANDLE(-1).value:
        logger.debug("No process snapshot, so no running game is looked for")
        return []
    pids: list[int] = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.casefold() == name.casefold():
                pids.append(entry.th32ProcessID)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    found: dict[str, Path] = {}
    for pid in pids:
        path = process_executable(pid)
        if path is not None:
            found.setdefault(str(path).casefold(), path)
    return list(found.values())


def _kernel32():
    """kernel32 with typed process functions, private to this module.

    windll's shared one returns c_int, which cuts a handle in half.
    """
    import ctypes  # noqa: PLC0415 - Windows only
    from ctypes import wintypes as w  # noqa: PLC0415

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel32.OpenProcess.restype = w.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel32.CloseHandle.argtypes = [w.HANDLE]
    return kernel32


def process_executable(pid: int) -> Path | None:
    """The full path of process *pid*'s executable, or None when it cannot be read.

    Opened with the least access that reads a path, so a process of another user or a
    protected one is None rather than an error.
    """
    if sys.platform != "win32":
        return None
    import ctypes  # noqa: PLC0415 - Windows only
    from ctypes import wintypes as w  # noqa: PLC0415

    query_limited = 0x1000
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(query_limited, False, pid)
    if not handle:
        return None
    try:
        size = w.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value)
        return None
    finally:
        kernel32.CloseHandle(handle)


def install_of_client(executable: Path) -> Path:
    """The install folder a running client's executable is in: the folder above its ``Bin``."""
    return Path(executable).parent.parent


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
    for executable in where.running_clients():
        yield FoundInstall(install_of_client(executable), InstallSource.RUNNING)


def find_install(prefs: PreferenceStore, where: InstallPlaces | None = None) -> FoundInstall | None:
    """The first place in :func:`candidates` that holds the game, or None."""
    return next((found for found in candidates(prefs, where) if is_install(found.path)), None)


def running_install(where: InstallPlaces | None = None) -> FoundInstall | None:
    """The install of a game client running now, or None: what setup watches for while nothing is found."""
    where = where or places()
    folders = (install_of_client(executable) for executable in where.running_clients())
    return next((FoundInstall(folder, InstallSource.RUNNING) for folder in folders if is_install(folder)), None)


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
