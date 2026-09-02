"""Tests for the per-account character roster.

The pure half needs nothing. The persistence half is exercised against a fake keyring
backend rather than the real Windows Credential Manager: the suite must not write to the
developer's credential store, and CI has no unlocked one at all.
"""

import json

import pytest

from bartlebot import character_roster

LUKE = {"gid": 1001, "name": "Luke SkullHunter"}
MISHA = {"gid": 1002, "name": "Misha SkullSeer"}


@pytest.fixture
def fake_keyring(monkeypatch):
    """A dict standing in for Windows Credential Manager."""
    store: dict[tuple[str, str], str] = {}

    def get_password(service, username):
        return store.get((service, username))

    def set_password(service, username, value):
        store[(service, username)] = value

    def delete_password(service, username):
        if (service, username) not in store:
            raise RuntimeError("no such credential")
        del store[(service, username)]

    monkeypatch.setattr(character_roster.keyring, "get_password", get_password)
    monkeypatch.setattr(character_roster.keyring, "set_password", set_password)
    monkeypatch.setattr(character_roster.keyring, "delete_password", delete_password)
    return store


# ---------------------------------------------------------------------------
# entries / find / position
# ---------------------------------------------------------------------------


def test_entries_keeps_stored_order():
    assert character_roster.entries([LUKE, MISHA]) == [LUKE, MISHA]


def test_entries_of_nothing_is_empty():
    assert character_roster.entries(None) == []
    assert character_roster.entries([]) == []


@pytest.mark.parametrize(
    "junk",
    [
        "not a dict",
        {"name": "Luke SkullHunter"},  # no gid: nothing can ever match or update it
        {"gid": 0, "name": "Luke SkullHunter"},  # 0 is NO_CHARACTER, not an identity
        {"gid": -5, "name": "Luke SkullHunter"},
        {"gid": "1001", "name": "Luke SkullHunter"},  # a string gid would never compare equal
    ],
)
def test_entries_drops_anything_that_cannot_be_an_identity(junk):
    assert character_roster.entries([junk, LUKE]) == [LUKE]


def test_entries_tolerates_a_missing_name():
    """A gid with no name is still a character — it just has nothing to show yet."""
    assert character_roster.entries([{"gid": 1001}]) == [{"gid": 1001, "name": ""}]


def test_find_matches_on_gid():
    assert character_roster.find([LUKE, MISHA], 1002) == MISHA
    assert character_roster.find([LUKE, MISHA], 9999) is None


def test_position_is_the_index_in_stored_order():
    assert character_roster.position([LUKE, MISHA], 1001) == 0
    assert character_roster.position([LUKE, MISHA], 1002) == 1
    assert character_roster.position([LUKE, MISHA], 9999) == -1


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


def test_upsert_appends_a_new_character():
    assert character_roster.upsert([LUKE], 1002, "Misha SkullSeer") == [LUKE, MISHA]


def test_upsert_does_not_mutate_the_input():
    roster = [LUKE]
    character_roster.upsert(roster, 1002, "Misha SkullSeer")
    assert roster == [LUKE]


def test_upsert_returns_the_roster_unchanged_when_nothing_moves():
    """The caller compares the result to decide whether to write, and this pass runs on
    every client-loop tick — a keyring write per tick would be the bug."""
    roster = [LUKE, MISHA]
    assert character_roster.upsert(roster, 1001, "Luke SkullHunter") == roster


def test_upsert_follows_a_rename_on_the_same_gid():
    """The game allows a rename and the gid outlives it, which is why the gid is the key."""
    assert character_roster.upsert([LUKE], 1001, "Luke SkullBreaker") == [{"gid": 1001, "name": "Luke SkullBreaker"}]


def test_upsert_keeps_two_characters_that_share_a_name():
    """The case a name-keyed roster would silently collapse: plenty of accounts carry
    several wizards with the same first name."""
    roster = character_roster.upsert([{"gid": 1001, "name": "Wolf"}], 1002, "Wolf")
    assert roster == [{"gid": 1001, "name": "Wolf"}, {"gid": 1002, "name": "Wolf"}]


def test_upsert_ignores_a_missing_character_id():
    """NO_CHARACTER is what a client at character select reports; it is not a character."""
    assert character_roster.upsert([LUKE], 0, "Nobody") == [LUKE]


def test_upsert_does_not_blank_a_known_name():
    """A tick that has the id but not the name yet must not erase what is stored."""
    assert character_roster.upsert([LUKE], 1001, "") == [LUKE]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_then_load_round_trips(fake_keyring):
    character_roster.save("someone@example.com", [LUKE, MISHA])
    assert character_roster.load("someone@example.com") == [LUKE, MISHA]


def test_load_of_an_unknown_account_is_empty(fake_keyring):
    assert character_roster.load("nobody@example.com") == []


def test_rosters_do_not_leak_between_accounts(fake_keyring):
    character_roster.save("a@example.com", [LUKE])
    character_roster.save("b@example.com", [MISHA])
    assert character_roster.load("a@example.com") == [LUKE]
    assert character_roster.load("b@example.com") == [MISHA]


def test_a_corrupt_blob_reads_as_nothing_known(fake_keyring):
    fake_keyring[(character_roster._SERVICE, "someone@example.com")] = "{not json"
    assert character_roster.load("someone@example.com") == []


def test_a_blob_of_the_wrong_shape_reads_as_nothing_known(fake_keyring):
    fake_keyring[(character_roster._SERVICE, "someone@example.com")] = json.dumps({"gid": 1001})
    assert character_roster.load("someone@example.com") == []


def test_load_survives_a_credential_store_that_will_not_open(monkeypatch):
    """A locked or missing keyring degrades to "no characters known" — the state before
    this existed — rather than taking the client loop down with it."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("credential store unavailable")

    monkeypatch.setattr(character_roster.keyring, "get_password", boom)
    assert character_roster.load("someone@example.com") == []


def test_save_reports_failure_rather_than_raising(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("credential store unavailable")

    monkeypatch.setattr(character_roster.keyring, "set_password", boom)
    assert character_roster.save("someone@example.com", [LUKE]) is False


def test_forget_removes_the_roster(fake_keyring):
    character_roster.save("someone@example.com", [LUKE])
    character_roster.forget("someone@example.com")
    assert character_roster.load("someone@example.com") == []


def test_forget_is_a_no_op_for_an_account_with_no_roster(fake_keyring):
    character_roster.forget("nobody@example.com")  # must not raise
