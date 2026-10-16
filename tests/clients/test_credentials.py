"""Tests for what an account is called where its username must not appear (#241)."""

import logging

from bartlebot.clients.client_manager import ClientManager
from bartlebot.clients.credentials import account_label
from bartlebot.settings_manager import SettingsManager


def test_an_account_with_an_alias_is_called_by_it():
    SettingsManager.instance().accounts = [{"username": "jdoe1234", "alias": "Main"}]
    assert account_label("jdoe1234") == "Main"


def test_an_account_without_one_is_censored():
    SettingsManager.instance().accounts = [{"username": "jdoe1234", "alias": ""}]
    assert account_label("jdoe1234") == "j***4"


def test_an_account_the_store_does_not_know_is_censored_too():
    SettingsManager.instance().accounts = []
    assert account_label("jdoe1234") == "j***4"


class _OrderHarness:
    reserve_slots = ClientManager.reserve_slots

    def __init__(self) -> None:
        self._client_order: list[str | None] = [None]
        self._vacated_slots: dict[str, int] = {}
        self._pending_recovery: dict[int, str] = {}


def test_the_reserved_slot_order_is_logged_by_label_and_slot(caplog):
    """The line the issue found: it printed the ordering, usernames and all."""
    SettingsManager.instance().accounts = [{"username": "jdoe1234", "alias": "Main"}, {"username": "asmith99"}]
    harness = _OrderHarness()
    harness._client_order = [None, "jdoe1234"]

    with caplog.at_level(logging.INFO, logger="bartlebot.clients.client_manager"):
        harness.reserve_slots(["asmith99"])

    assert harness._client_order == ["asmith99", "jdoe1234"]
    assert "P1 'a***9', P2 'Main'" in caplog.text
    assert "jdoe1234" not in caplog.text and "asmith99" not in caplog.text
