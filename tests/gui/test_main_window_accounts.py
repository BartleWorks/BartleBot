"""A running window rereads the vendor's accounts file, which BartleBuddy also writes (#303).

The other product is played by a second ``AccountStore`` over the same file: it writes to
disk, and this process's ``SettingsManager`` holds the copy it read at start.
"""

from __future__ import annotations

import pytest
from bartleby.shared.account_store import AccountStore
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QMenu

from bartlebot.gui.settings import settings_dialog
from bartlebot.settings_manager import SettingsManager

ONE = "one@example.com"
TWO = "two@example.com"


@pytest.fixture
def window(qapp):
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    SettingsManager.instance().accounts = [{"username": ONE, "alias": ""}]
    window = MainWindow()
    yield window
    window.bot_canvas.prepare_for_shutdown()


def _added_elsewhere() -> None:
    """BartleBuddy adds an account and a profile naming both."""
    other = AccountStore(SettingsManager.instance().account_store.path.parent)
    other.set_accounts([*other.accounts, {"username": TWO, "alias": ""}])
    other.set_launch_profiles([{"name": "Both", "accounts": [ONE, TWO]}])


def _activate(window, monkeypatch) -> None:
    """The user switches back to this window. Offscreen, nothing is ever really active."""
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    QApplication.sendEvent(window, QEvent(QEvent.Type.ActivationChange))


def test_an_account_added_elsewhere_reaches_quick_launch_when_the_window_is_activated(window, monkeypatch):
    from bartleby.qt.game_embed.game_view import _GamePlaceholder  # noqa: PLC0415

    placeholder = window.findChild(_GamePlaceholder)
    launch_menu = next(menu for menu in window.findChildren(QMenu) if menu.title() == "Launch")
    _added_elsewhere()
    assert len(placeholder.accounts_column.cards) == 1

    _activate(window, monkeypatch)

    assert len(placeholder.accounts_column.cards) == 2
    assert len(placeholder.profiles_column.cards) == 1
    assert "Both" in [action.text() for action in launch_menu.actions()]


def test_an_activation_with_nothing_changed_rebuilds_nothing(window, monkeypatch):
    """Every dialog that closes activates the window again: the cards stay as they are."""
    rebuilt = []
    monkeypatch.setattr(window, "catch_up_with_accounts", lambda: rebuilt.append(1))

    _activate(window, monkeypatch)

    assert rebuilt == []


def test_settings_opens_on_an_account_added_elsewhere(window, monkeypatch):
    seen = []
    monkeypatch.setattr(
        settings_dialog.SettingsDialog,
        "exec",
        lambda self: seen.append([a["username"] for a in SettingsManager.instance().accounts]) or 0,
    )
    _added_elsewhere()

    window._open_settings("game")

    assert seen == [[ONE, TWO]]
