"""Tests for ClientManager._learn_character_roster.

Binds the real method to a minimal harness, as tests/test_client_loop.py does, so the
production code runs without a QApplication, WizSprinter, or a Wizard101 process. The
keyring is faked: the suite must not write to the developer's credential store.
"""

from unittest.mock import MagicMock

import pytest

from bartlebot.clients import character_roster
from bartlebot.clients.client_manager import ClientManager
from bartlebot.game import wizard_names


class FakeClientWindow:
    def __init__(self, hwnd: int) -> None:
        self.window_handle = hwnd


class RosterHarness:
    """Just enough ClientManager for the learning pass."""

    _learn_character_roster = ClientManager._learn_character_roster
    characters_for = ClientManager.characters_for

    def __init__(self) -> None:
        self._hwnd_account_map: dict[int, str] = {}
        self._rosters: dict[str, list[dict]] = {}


@pytest.fixture
def store(monkeypatch):
    """A dict standing in for Windows Credential Manager."""
    saved: dict[str, list[dict]] = {}

    monkeypatch.setattr(character_roster, "load", lambda username: list(saved.get(username, [])))
    monkeypatch.setattr(
        character_roster,
        "save",
        lambda username, roster: (saved.__setitem__(username, list(roster)), True)[1],
    )
    return saved


@pytest.fixture(autouse=True)
def _clean_name_cache():
    wizard_names.clear_cache()
    yield
    wizard_names.clear_cache()


def _client(harness, hwnd, username, name, gid):
    """A live client that the account map has placed and the name cache knows."""
    client = FakeClientWindow(hwnd)
    harness._hwnd_account_map[hwnd] = username
    wizard_names.remember(client, name, gid)
    return client


def test_learns_a_character_for_an_identified_account(store):
    harness = RosterHarness()
    client = _client(harness, 100, "a@example.com", "Luke SkullHunter", 1001)

    harness._learn_character_roster([client])

    assert store["a@example.com"] == [{"gid": 1001, "name": "Luke SkullHunter"}]


def test_accumulates_characters_across_sessions(store):
    """The whole point of persisting: an account's second character is added to the
    first, not substituted for it."""
    harness = RosterHarness()
    harness._learn_character_roster([_client(harness, 100, "a@example.com", "Luke SkullHunter", 1001)])
    wizard_names.clear_cache()
    harness._learn_character_roster([_client(harness, 101, "a@example.com", "Misha SkullSeer", 1002)])

    assert store["a@example.com"] == [
        {"gid": 1001, "name": "Luke SkullHunter"},
        {"gid": 1002, "name": "Misha SkullSeer"},
    ]


def test_keeps_each_account_separate(store):
    harness = RosterHarness()
    harness._learn_character_roster(
        [
            _client(harness, 100, "a@example.com", "Luke SkullHunter", 1001),
            _client(harness, 101, "b@example.com", "Misha SkullSeer", 1002),
        ]
    )

    assert store["a@example.com"] == [{"gid": 1001, "name": "Luke SkullHunter"}]
    assert store["b@example.com"] == [{"gid": 1002, "name": "Misha SkullSeer"}]


def test_writes_nothing_for_an_unidentified_client(store):
    """An adopted window BartleBot has never launched has no account to file it under —
    guessing one would attribute a character to the wrong account permanently."""
    harness = RosterHarness()
    client = FakeClientWindow(100)  # deliberately absent from the account map
    wizard_names.remember(client, "Luke SkullHunter", 1001)

    harness._learn_character_roster([client])

    assert store == {}


def test_writes_nothing_at_character_select(store):
    """NO_CHARACTER is what a client reports before a character is chosen."""
    harness = RosterHarness()
    client = _client(harness, 100, "a@example.com", "Luke SkullHunter", wizard_names.NO_CHARACTER)

    harness._learn_character_roster([client])

    assert store == {}


def test_writes_nothing_before_the_name_is_read(store):
    harness = RosterHarness()
    client = FakeClientWindow(100)
    harness._hwnd_account_map[100] = "a@example.com"  # identified, but no cached name

    harness._learn_character_roster([client])

    assert store == {}


def test_does_not_write_again_when_nothing_changed(monkeypatch, store):
    """This runs on every client-loop tick, so a write per tick would be the bug."""
    harness = RosterHarness()
    client = _client(harness, 100, "a@example.com", "Luke SkullHunter", 1001)
    harness._learn_character_roster([client])

    writes = 0
    real_save = character_roster.save

    def counting_save(username, roster):
        nonlocal writes
        writes += 1
        return real_save(username, roster)

    monkeypatch.setattr(character_roster, "save", counting_save)
    for _ in range(5):
        harness._learn_character_roster([client])

    assert writes == 0


def test_a_rename_is_written_through(store):
    harness = RosterHarness()
    harness._learn_character_roster([_client(harness, 100, "a@example.com", "Luke SkullHunter", 1001)])
    wizard_names.clear_cache()
    harness._learn_character_roster([_client(harness, 100, "a@example.com", "Luke SkullBreaker", 1001)])

    assert store["a@example.com"] == [{"gid": 1001, "name": "Luke SkullBreaker"}]


def test_a_broken_client_does_not_stop_the_pass(store):
    """This runs inside the client loop, whose MemoryReadError branch empties the client
    list — an exception escaping here would look like every client vanishing at once."""
    harness = RosterHarness()
    broken = MagicMock()
    type(broken).window_handle = property(lambda _self: (_ for _ in ()).throw(RuntimeError("client is gone")))
    good = _client(harness, 101, "b@example.com", "Misha SkullSeer", 1002)

    harness._learn_character_roster([broken, good])

    assert store["b@example.com"] == [{"gid": 1002, "name": "Misha SkullSeer"}]


def test_characters_for_answers_an_account_that_is_not_running(store):
    """The case no client object can answer, and the reason the roster is persisted."""
    store["a@example.com"] = [{"gid": 1001, "name": "Luke SkullHunter"}]

    assert RosterHarness().characters_for("a@example.com") == [{"gid": 1001, "name": "Luke SkullHunter"}]


def test_characters_for_an_unknown_account_is_empty(store):
    assert RosterHarness().characters_for("nobody@example.com") == []
