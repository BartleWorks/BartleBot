"""Editing a launch profile opens the editor and nothing else (#193).

The gear on a ProfileCard used to reach its editor through the whole Settings dialog:
``_open_settings(tab_name="game", edit_profile=name)`` built the dialog, switched it to
the Game tab and opened the editor on top. Dismissing the editor left the Settings dialog
standing -- a surface the user never asked for, whose OK/Cancel applies to every tab --
and the refresh that should follow a save was hung off *that* dialog's result, so
cancelling it dropped the refresh for a profile already written to disk.

``edit_launch_profile`` is now a module function reachable without an AccountsTab, and
both the card and the tab's Edit button go through it. These tests cover its save
decisions and the slot that runs the refresh, neither of which needs a MainWindow.
"""

import inspect

import pytest
from PyQt6.QtWidgets import QDialog

from bartlebot.gui.settings import accounts_tab
from bartlebot.settings_manager import SettingsManager
from tests.conftest import main_window_stub

ACCOUNTS = ["one@example.com", "two@example.com"]


class _FakeDialog:
    """Stands in for _ProfileDialog: exec() is the one thing a test cannot run.

    Class attributes carry what the editor would return, so a test says what the user
    typed rather than driving widgets. The constructor signature matches the keyword call
    in edit_launch_profile, which is what keeps this stub honest.
    """

    accepted = True
    result_name = "Farm Team"
    result_accounts: list[str] = ACCOUNTS
    result_resolution: tuple[int, int] | None = None
    seen: dict = {}

    def __init__(self, name="", selected_usernames=None, resolution=None, parent=None, live_game_area=None):
        # Recorded so a test can assert the editor was opened on the right profile, and
        # handed the live game area its resolution picker judges fit against.
        type(self).seen = {
            "name": name,
            "selected_usernames": selected_usernames,
            "resolution": resolution,
            "parent": parent,
            "live_game_area": live_game_area,
        }

    def exec(self):
        return QDialog.DialogCode.Accepted if type(self).accepted else QDialog.DialogCode.Rejected

    @property
    def name(self):
        return type(self).result_name

    @property
    def selected_usernames(self):
        return type(self).result_accounts

    @property
    def resolution(self):
        return type(self).result_resolution


@pytest.fixture
def editor(monkeypatch):
    """The profile editor, stubbed, with one profile already stored."""

    class Dialog(_FakeDialog):
        pass

    monkeypatch.setattr(accounts_tab, "_ProfileDialog", Dialog)
    # A refusal puts up a modal warning, which would block the suite. Its return is
    # never read; what the refusal does is decline to save, which is what is asserted.
    monkeypatch.setattr(accounts_tab.QMessageBox, "warning", lambda *a, **k: None)
    SettingsManager.instance().launch_profiles = [{"name": "Farm Team", "accounts": ACCOUNTS}]
    return Dialog


def _stored():
    return SettingsManager.instance().launch_profiles


# ---------------------------------------------------------------------------
# edit_launch_profile
# ---------------------------------------------------------------------------


def test_an_accepted_edit_is_saved(editor):
    editor.result_name = "Renamed"
    editor.result_accounts = ["one@example.com"]
    editor.result_resolution = (800, 600)

    assert accounts_tab.edit_launch_profile("Farm Team") is True
    assert _stored() == [{"name": "Renamed", "accounts": ["one@example.com"], "resolution": [800, 600]}]


def test_the_editor_opens_on_the_profile_that_was_named(editor):
    accounts_tab.edit_launch_profile("Farm Team", parent=None, live_game_area=(1200, 700))

    assert editor.seen["name"] == "Farm Team"
    assert editor.seen["selected_usernames"] == ACCOUNTS
    # Passed through rather than dropped: the picker has to judge fit the same way the
    # surface behind it does.
    assert editor.seen["live_game_area"] == (1200, 700)


def test_clearing_the_resolution_drops_the_key_rather_than_storing_none(editor):
    SettingsManager.instance().launch_profiles = [
        {"name": "Farm Team", "accounts": ACCOUNTS, "resolution": [1280, 720]}
    ]
    editor.result_resolution = None

    assert accounts_tab.edit_launch_profile("Farm Team") is True
    assert "resolution" not in _stored()[0]


def test_a_cancelled_edit_changes_nothing(editor):
    editor.accepted = False
    editor.result_name = "Renamed"

    assert accounts_tab.edit_launch_profile("Farm Team") is False
    assert _stored() == [{"name": "Farm Team", "accounts": ACCOUNTS}]


def test_an_unknown_profile_opens_no_editor(editor):
    """False rather than raising: the card naming it may be a frame behind the settings."""
    editor.seen = {}

    assert accounts_tab.edit_launch_profile("No Such Profile") is False
    assert editor.seen == {}


def test_an_empty_name_is_refused(editor):
    editor.result_name = ""

    assert accounts_tab.edit_launch_profile("Farm Team") is False
    assert _stored() == [{"name": "Farm Team", "accounts": ACCOUNTS}]


def test_renaming_onto_another_profile_is_refused(editor):
    SettingsManager.instance().launch_profiles = [
        {"name": "Farm Team", "accounts": ACCOUNTS},
        {"name": "Taken", "accounts": []},
    ]
    editor.result_name = "Taken"

    assert accounts_tab.edit_launch_profile("Farm Team") is False
    assert [p["name"] for p in _stored()] == ["Farm Team", "Taken"]


def test_keeping_the_same_name_is_not_a_duplicate(editor):
    """The profile collides with itself on every save that does not rename it."""
    editor.result_name = "Farm Team"
    editor.result_accounts = ["two@example.com"]

    assert accounts_tab.edit_launch_profile("Farm Team") is True
    assert _stored()[0]["accounts"] == ["two@example.com"]


# ---------------------------------------------------------------------------
# The slot behind the card's gear
# ---------------------------------------------------------------------------
# Called unbound against a stand-in: MainWindow is far too heavy to build for this, and
# what is under test is which refreshes run -- the second-order bug in #193.


def _main_window_stub(saved: bool, monkeypatch):
    monkeypatch.setattr(accounts_tab, "edit_launch_profile", lambda *a, **k: saved)
    return main_window_stub()


def test_a_saved_profile_refreshes_the_menu_and_the_cards(monkeypatch):
    from bartlebot.gui.main_window import MainWindow

    calls, stub = _main_window_stub(True, monkeypatch)

    MainWindow._edit_launch_profile(stub, "Farm Team")

    assert calls == ["menu", "minimum", "cards"]


def test_a_cancelled_edit_still_re_judges_the_cards(monkeypatch):
    """The screen behind a modal editor has been sitting on cards nothing re-judged.

    This is the half that used to be conditional on the *Settings* dialog's result, so
    cancelling it dropped the refresh for a profile the editor had already saved.
    """
    from bartlebot.gui.main_window import MainWindow

    calls, stub = _main_window_stub(False, monkeypatch)

    MainWindow._edit_launch_profile(stub, "Farm Team")

    assert calls == ["cards"]


def test_the_gear_no_longer_has_a_route_through_the_settings_dialog():
    """Both halves of the old path are gone, so nothing can drift back onto it."""
    from bartlebot.gui.main_window import MainWindow
    from bartlebot.gui.settings.settings_dialog import SettingsDialog

    assert not hasattr(SettingsDialog, "edit_profile")
    assert "edit_profile" not in inspect.signature(MainWindow._open_settings).parameters
