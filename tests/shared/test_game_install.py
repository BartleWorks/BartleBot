"""Finding the game: each place in order, Steam's two library lists, and folders without the client (#227)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bartlebot.shared import game_install
from bartlebot.shared.game_install import (
    INSTALL_DIR_KEY,
    STEAM_APP_ID,
    FoundInstall,
    InstallSource,
    clear_install_dir,
    detect_install,
    find_install,
    is_install,
    parse_library_folders,
    running_executables,
    running_install,
    set_install_dir,
    steam_install,
    steam_libraries,
)
from bartlebot.shared.preferences import PreferenceStore

# Steam since 2021: a block per library, the first being Steam's own folder.
_BLOCKS_VDF = r"""
"libraryfolders"
{
    "0"
    {
        "path"      "C:\\Program Files (x86)\\Steam"
        "label"     ""
        "contentid" "1234"
        "apps"
        {
            "228980"    "0"
        }
    }
    "1"
    {
        "path"      "D:\\SteamLibrary"
        "label"     ""
        "apps"
        {
            "799960"    "21474836480"
        }
    }
}
"""

# Older clients: each library a bare value beside a few counters.
_VALUES_VDF = r"""
"LibraryFolders"
{
    "TimeNextStatsReport"   "1600000000"
    "ContentStatsID"        "-1234"
    "1"     "D:\\SteamLibrary"
    "2"     "E:\\Games\\Steam"
}
"""


@dataclass
class _Places:
    """A machine whose launcher folder, install record and Steam root are what the test says."""

    launcher: Path
    uninstall: Path | None = None
    steam: Path | None = None
    running: list[Path] = field(default_factory=list)

    def launcher_dir(self) -> Path:
        return self.launcher

    def uninstall_location(self) -> Path | None:
        return self.uninstall

    def steam_root(self) -> Path | None:
        return self.steam

    def running_clients(self) -> list[Path]:
        return list(self.running)


def _install(folder: Path) -> Path:
    (folder / "Bin").mkdir(parents=True)
    (folder / "Bin" / "WizardGraphicalClient.exe").write_bytes(b"")
    return folder


def _steam_library(library: Path, *, installdir: str | None = "Wizard101", manifest: bool = True) -> Path:
    """A Steam library holding Wizard101's manifest, and the game where the manifest says."""
    apps = library / "steamapps"
    apps.mkdir(parents=True, exist_ok=True)
    if manifest:
        body = f'\t"installdir"\t\t"{installdir}"\n' if installdir else ""
        (apps / f"appmanifest_{STEAM_APP_ID}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{STEAM_APP_ID}"\n{body}}}\n', encoding="utf-8"
        )
    return apps / "common" / (installdir or "Wizard101")


def _library_list(steam: Path, *libraries: Path) -> None:
    lines = [
        f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{str(lib).replace("\\", "\\\\")}"\n\t}}\n' for i, lib in enumerate(libraries)
    ]
    (steam / "steamapps").mkdir(parents=True, exist_ok=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders"\n{\n' + "".join(lines) + "}\n", "utf-8")


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


# -- a folder ------------------------------------------------------------------------


def test_a_folder_is_an_install_only_with_the_client_in_its_bin(tmp_path):
    assert is_install(_install(tmp_path / "game"))

    bare = tmp_path / "bare"
    (bare / "Bin").mkdir(parents=True)
    assert not is_install(bare)
    assert not is_install(tmp_path / "missing")
    assert not is_install("")
    assert not is_install(None)


# -- Steam's library list -------------------------------------------------------------


def test_the_block_layout_parses():
    assert parse_library_folders(_BLOCKS_VDF) == [Path(r"C:\Program Files (x86)\Steam"), Path(r"D:\SteamLibrary")]


def test_the_value_layout_parses_and_skips_its_counters():
    assert parse_library_folders(_VALUES_VDF) == [Path(r"D:\SteamLibrary"), Path(r"E:\Games\Steam")]


@pytest.mark.parametrize("text", ["", "not vdf {", '"something_else" { "0" "C:\\\\x" }'])
def test_a_list_that_cannot_be_read_names_no_library(text):
    assert parse_library_folders(text) == []


def test_steams_own_folder_is_a_library_and_none_is_listed_twice(tmp_path):
    steam = tmp_path / "Steam"
    other = tmp_path / "Library"
    _library_list(steam, steam, other)

    assert steam_libraries(_Places(tmp_path / "none", steam=steam)) == [steam, other]


def test_the_older_clients_list_under_config_is_read_too(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "config").mkdir(parents=True)
    (steam / "config" / "libraryfolders.vdf").write_text(_VALUES_VDF, encoding="utf-8")

    assert steam_libraries(_Places(tmp_path / "none", steam=steam)) == [
        steam,
        Path(r"D:\SteamLibrary"),
        Path(r"E:\Games\Steam"),
    ]


def test_no_steam_means_no_libraries(tmp_path):
    assert steam_libraries(_Places(tmp_path / "none")) == []


def test_the_manifest_names_the_games_folder(tmp_path):
    library = tmp_path / "Library"
    assert steam_install(library) is None

    expected = _steam_library(library, installdir="Wizard101 Renamed")
    assert steam_install(library) == expected


def test_a_manifest_without_an_installdir_means_the_usual_folder(tmp_path):
    library = tmp_path / "Library"
    _steam_library(library, installdir=None)

    assert steam_install(library) == library / "steamapps" / "common" / "Wizard101"


# -- each place, in order ---------------------------------------------------------------


@pytest.fixture
def machine(tmp_path: Path) -> _Places:
    """A machine with the game in every place: the launcher's folder, its record, and a Steam library."""
    steam = tmp_path / "Steam"
    library = tmp_path / "SteamLibrary"
    _library_list(steam, steam, library)
    _install(_steam_library(library))
    return _Places(
        launcher=_install(tmp_path / "KingsIsle"),
        uninstall=_install(tmp_path / "Registered"),
        steam=steam,
    )


def test_each_place_is_found_in_order(tmp_path, prefs, machine):
    """The stored folder, the launcher's folder, its install record, then Steam."""
    chosen = _install(tmp_path / "Chosen")
    assert set_install_dir(prefs, chosen)
    assert find_install(prefs, machine) == FoundInstall(chosen, InstallSource.STORED)

    assert clear_install_dir(prefs)
    assert find_install(prefs, machine) == FoundInstall(machine.launcher, InstallSource.LAUNCHER)

    machine.launcher = tmp_path / "gone"
    assert find_install(prefs, machine) == FoundInstall(machine.uninstall, InstallSource.REGISTRY)

    machine.uninstall = None
    found = find_install(prefs, machine)
    assert found == FoundInstall(tmp_path / "SteamLibrary" / "steamapps" / "common" / "Wizard101", InstallSource.STEAM)

    machine.steam = None
    assert find_install(prefs, machine) is None


def test_a_running_game_is_found_after_every_other_place(tmp_path, prefs, machine):
    """Last, because it is there only while the game runs: an install every other place missed."""
    elsewhere = _install(tmp_path / "Games" / "W101")
    machine.running = [elsewhere / "Bin" / "WizardGraphicalClient.exe"]
    assert find_install(prefs, machine).source is InstallSource.LAUNCHER

    machine.launcher, machine.uninstall, machine.steam = tmp_path / "gone", None, None

    assert find_install(prefs, machine) == FoundInstall(elsewhere, InstallSource.RUNNING)
    assert detect_install(machine) == FoundInstall(elsewhere, InstallSource.RUNNING)
    assert running_install(machine) == FoundInstall(elsewhere, InstallSource.RUNNING)


def test_a_running_executable_outside_an_install_finds_nothing(tmp_path, prefs):
    stray = tmp_path / "Copied" / "WizardGraphicalClient.exe"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"")
    where = _Places(tmp_path / "gone", running=[stray])

    assert find_install(prefs, where) is None
    assert running_install(where) is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads Windows' process list")
def test_running_executables_reads_this_machines_processes():
    """The suite's own interpreter is running, so it is found, by its full path."""
    found = running_executables(Path(sys.executable).name)

    assert Path(sys.executable).resolve() in [path.resolve() for path in found]
    assert running_executables("no-such-program-anywhere.exe") == []


def test_a_folder_without_the_client_is_never_found(tmp_path, prefs):
    """A stale choice, an emptied launcher folder, a registered folder gone, a manifest with no game."""
    for folder in ("Chosen", "KingsIsle", "Registered"):
        (tmp_path / folder).mkdir()
    prefs.set(INSTALL_DIR_KEY, str(tmp_path / "Chosen"))
    steam = tmp_path / "Steam"
    _library_list(steam, steam)
    _steam_library(steam)  # the manifest, but no game

    where = _Places(tmp_path / "KingsIsle", tmp_path / "Registered", steam)
    assert find_install(prefs, where) is None


def test_steam_is_searched_only_in_a_library_with_the_manifest(tmp_path, prefs):
    steam = tmp_path / "Steam"
    library = tmp_path / "Library"
    _library_list(steam, steam, library)
    _install(_steam_library(steam, manifest=False))  # a game folder Steam no longer lists
    expected = _install(_steam_library(library))

    assert find_install(prefs, _Places(tmp_path / "none", steam=steam)).path == expected


def test_detect_leaves_the_stored_folder_out(tmp_path, prefs, machine):
    set_install_dir(prefs, _install(tmp_path / "Chosen"))

    assert detect_install(machine) == FoundInstall(machine.launcher, InstallSource.LAUNCHER)


def test_the_real_places_are_used_unless_others_are_set(monkeypatch, tmp_path):
    monkeypatch.setattr(game_install, "_places", None)
    assert isinstance(game_install.places(), game_install.WindowsPlaces)

    fake = _Places(tmp_path)
    game_install.set_places(fake)
    assert game_install.places() is fake
    game_install.set_places(None)
    assert isinstance(game_install.places(), game_install.WindowsPlaces)


def test_the_suite_never_reads_the_developers_machine():
    """``tests/conftest.py`` puts no places in; a game installed here would otherwise decide setup's pages."""
    assert isinstance(game_install.places(), game_install.NoPlaces)


# -- storing a choice ---------------------------------------------------------------


def test_a_folder_without_the_client_is_refused_and_nothing_written(tmp_path, prefs):
    assert not set_install_dir(prefs, tmp_path)
    assert not prefs.path.exists()


def test_a_choice_is_stored_in_the_shared_preferences(tmp_path, prefs):
    folder = _install(tmp_path / "game")

    assert set_install_dir(prefs, folder)

    assert prefs.get(INSTALL_DIR_KEY) == str(folder)
    assert PreferenceStore(prefs.path.parent).get(INSTALL_DIR_KEY) == str(folder)
    assert clear_install_dir(prefs)
    assert prefs.get(INSTALL_DIR_KEY) is None
