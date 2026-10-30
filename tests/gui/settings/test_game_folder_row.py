"""Settings › General › Game folder, and that it agrees with the setup page (#227)."""

from __future__ import annotations

from pathlib import Path

import pytest
from bartleby.qt.dialogs.first_run_dialog import GameFolderPage
from bartleby.qt.settings.game_folder_row import CHOSEN, NOT_FOUND, SUMMARY, TITLE
from bartleby.qt.widgets.inputs.game_folder_input import FOLDER_MISSING
from bartleby.shared import game_install
from bartleby.shared.game_install import INSTALL_DIR_KEY
from bartleby.shared.preferences import PreferenceStore
from wizwalker import utils as wizwalker_utils

from bartlebot.gui.settings.general_tab import GeneralTab


def _install(folder: Path) -> Path:
    (folder / "Bin").mkdir(parents=True)
    (folder / "Bin" / "WizardGraphicalClient.exe").write_bytes(b"")
    return folder


class _Launcher(game_install.NoPlaces):
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def launcher_dir(self) -> Path:
        return self.folder


@pytest.fixture(autouse=True)
def _restore_wizwalker():
    yield
    wizwalker_utils.override_wiz_install_location(None)


@pytest.fixture
def launcher(tmp_path, monkeypatch) -> Path:
    """The game, where the KingsIsle launcher installs it."""
    folder = _install(tmp_path / "KingsIsle")
    monkeypatch.setattr(game_install, "_places", _Launcher(folder))
    return folder


def _prefs() -> PreferenceStore:
    """The store both surfaces read: the vendor root under this test's ``%APPDATA%``."""
    return PreferenceStore()


def _summary(tab: GeneralTab) -> str:
    return tab.game_folder_row.summary.text()


def _control(tab: GeneralTab):
    return tab.game_folder_row.control


def test_it_is_the_first_row_on_general(qapp):
    assert GeneralTab().rows[0].title == TITLE


def test_it_shows_the_detected_folder_and_where_it_came_from(qapp, launcher):
    tab = GeneralTab()

    assert _control(tab).input.path() == str(launcher)
    assert _summary(tab) == SUMMARY.format(status="Found in the KingsIsle launcher's folder.")
    assert not tab.game_folder_row.is_changed


def test_it_says_when_nothing_is_found(qapp):
    tab = GeneralTab()

    assert _control(tab).input.path() == ""
    assert _summary(tab) == SUMMARY.format(status=NOT_FOUND)


def test_a_folder_browsed_to_is_stored_on_ok_and_handed_to_wizwalker(qapp, launcher, tmp_path):
    tab = GeneralTab()
    other = _install(tmp_path / "Steam" / "steamapps" / "common" / "Wizard101")
    _control(tab).input.choose_directory = lambda _parent, _start: str(other)

    _control(tab).input.browse_button.click()
    assert _summary(tab) == SUMMARY.format(status=CHOSEN)
    assert tab.game_folder_row.is_changed
    assert _prefs().get(INSTALL_DIR_KEY) is None  # pending until OK

    tab.apply()

    assert _prefs().get(INSTALL_DIR_KEY) == str(other)
    assert wizwalker_utils.get_wiz_install() == other.absolute()


def test_a_folder_without_the_game_is_not_stored(qapp, tmp_path):
    tab = GeneralTab()
    _control(tab).input.set_path(tmp_path)

    assert _summary(tab) == SUMMARY.format(status=FOLDER_MISSING)
    tab.apply()

    assert _prefs().get(INSTALL_DIR_KEY) is None


def test_detect_forgets_the_folder_chosen(qapp, launcher, tmp_path):
    game_install.set_install_dir(_prefs(), _install(tmp_path / "Chosen"))
    tab = GeneralTab()
    assert _summary(tab) == SUMMARY.format(status=CHOSEN)

    _control(tab).detect_button.click()

    assert _control(tab).input.path() == str(launcher)
    assert not tab.game_folder_row.is_changed  # detected is the default
    tab.apply()
    assert _prefs().get(INSTALL_DIR_KEY) is None


def test_the_rows_reset_is_detect(qapp, launcher, tmp_path):
    game_install.set_install_dir(_prefs(), _install(tmp_path / "Chosen"))
    tab = GeneralTab()

    tab.game_folder_row.reset()

    assert _control(tab).value() is None
    assert _control(tab).input.path() == str(launcher)


def test_ok_without_a_change_leaves_a_folder_the_other_product_chose(qapp, launcher, tmp_path):
    tab = GeneralTab()
    theirs = _install(tmp_path / "Theirs")
    game_install.set_install_dir(PreferenceStore(_prefs().path.parent), theirs)

    tab.apply()

    assert _prefs().get(INSTALL_DIR_KEY) == str(theirs)


class _Running(game_install.NoPlaces):
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def running_clients(self) -> list[Path]:
        return [self.folder / "Bin" / "WizardGraphicalClient.exe"]


def test_a_folder_found_from_the_running_game_is_stored_on_ok(qapp, tmp_path, monkeypatch):
    """Started after BartleBot opened, so startup did not store it; closed, nothing would find it again."""
    running = _install(tmp_path / "Games" / "W101")
    monkeypatch.setattr(game_install, "_places", _Running(running))
    tab = GeneralTab()
    assert _summary(tab) == SUMMARY.format(status="Found in the game running now.")

    tab.apply()

    assert _prefs().get(INSTALL_DIR_KEY) == str(running)
    assert wizwalker_utils.get_wiz_install() == running.absolute()


def test_the_page_and_the_row_read_and_write_the_same_key(qapp, tmp_path):
    """What setup stores, Settings shows; what Settings stores, setup opens on."""
    from_page = _install(tmp_path / "FromPage")
    page = GameFolderPage(_prefs())
    page.folder_input.set_path(from_page)
    assert page.record()

    tab = GeneralTab()
    assert _control(tab).input.path() == str(from_page)
    assert _summary(tab) == SUMMARY.format(status=CHOSEN)

    from_row = _install(tmp_path / "FromRow")
    _control(tab).input.set_path(from_row)
    tab.apply()

    assert GameFolderPage(_prefs()).folder_input.path() == str(from_row)
