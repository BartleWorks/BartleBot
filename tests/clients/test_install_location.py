"""Handing wizwalker the game folder, at startup and after a change (#227)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from wizwalker import utils as wizwalker_utils

from bartlebot import app_controller
from bartlebot.app_controller import AppController
from bartlebot.clients.install_location import apply_install
from bartlebot.shared import game_install
from bartlebot.shared.game_install import InstallSource, set_install_dir
from bartlebot.shared.preferences import PreferenceStore


def _install(folder: Path) -> Path:
    (folder / "Bin").mkdir(parents=True)
    (folder / "Bin" / "WizardGraphicalClient.exe").write_bytes(b"")
    return folder


@pytest.fixture(autouse=True)
def _restore_wizwalker():
    yield
    wizwalker_utils.override_wiz_install_location(None)


def test_a_stored_folder_reaches_get_wiz_install(tmp_path):
    prefs = PreferenceStore()
    folder = _install(tmp_path / "Steam" / "steamapps" / "common" / "Wizard101")
    assert set_install_dir(prefs, folder)

    found = apply_install(prefs)

    assert found.source is InstallSource.STORED
    assert wizwalker_utils.get_wiz_install() == folder.absolute()


def test_nothing_found_leaves_wizwalkers_own_checks(tmp_path, monkeypatch):
    wizwalker_utils.override_wiz_install_location(str(tmp_path))
    monkeypatch.setattr(wizwalker_utils, "DEFAULT_INSTALL", str(_install(tmp_path / "Default")))

    assert apply_install(PreferenceStore()) is None

    # wizwalker's own first check, the launcher's folder, answers again.
    assert wizwalker_utils.get_wiz_install() == tmp_path / "Default"


def test_the_detected_folder_is_handed_over_too(tmp_path, monkeypatch):
    folder = _install(tmp_path / "KingsIsle")

    class _Launcher(game_install.NoPlaces):
        def launcher_dir(self) -> Path:
            return folder

    monkeypatch.setattr(game_install, "_places", _Launcher())

    assert apply_install(PreferenceStore()).source is InstallSource.LAUNCHER
    assert wizwalker_utils.get_wiz_install() == folder.absolute()


class _Stop(Exception):
    """Raised by the stand-in for the first thing after the install is applied."""


def test_the_app_applies_it_at_startup_before_anything_asks_wizwalker(monkeypatch):
    """Before the handler is made, and so before the window, the launcher or a client exists."""
    calls: list[str] = []
    monkeypatch.setattr(app_controller, "apply_install", lambda: calls.append("install"))
    monkeypatch.setattr(app_controller, "install_app_icon", lambda _app: None)
    monkeypatch.setattr(AppController, "_apply_theme", lambda _self: None)

    def handler():
        calls.append("handler")
        raise _Stop

    monkeypatch.setattr(app_controller, "WizSprinter", handler)
    app = SimpleNamespace(
        setOrganizationName=lambda _n: None, setApplicationName=lambda _n: None, setStyle=lambda _s: None
    )

    with pytest.raises(_Stop):
        AppController(app)

    assert calls == ["install", "handler"]
