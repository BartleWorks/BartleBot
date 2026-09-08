"""Tests for account fingerprinting.

The construction is a pure function of (user id, key), so most of this needs no
keyring at all. The two tests that do exercise key storage patch the keyring module —
nothing here may touch the real Windows Credential Manager.
"""

import base64

import pytest

from bartlebot import account_identity
from bartlebot.account_identity import (
    SCHEME,
    fingerprint,
    mask,
    match_account,
    stored_fingerprint,
    with_fingerprint,
)
from bartlebot.shared.vendor import keyring_service

KEY = b"\x01" * 32
OTHER_KEY = b"\x02" * 32
UID = 196751008724043973


@pytest.fixture(autouse=True)
def _no_cached_key():
    account_identity.reset_key_cache()
    yield
    account_identity.reset_key_cache()


class FakeKeyring:
    """Stands in for the keyring module, holding passwords in a dict."""

    def __init__(self, initial: dict | None = None) -> None:
        self.store = dict(initial or {})
        self.writes = 0

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value
        self.writes += 1


# ---------------------------------------------------------------------------
# The construction
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_for_the_same_inputs():
    assert fingerprint(UID, KEY) == fingerprint(UID, KEY)


def test_fingerprint_differs_per_user_id():
    assert fingerprint(UID, KEY) != fingerprint(UID + 1, KEY)


def test_fingerprint_differs_per_key():
    """The point of the pepper: the same account fingerprints differently per install."""
    assert fingerprint(UID, KEY) != fingerprint(UID, OTHER_KEY)


def test_fingerprint_does_not_contain_the_user_id():
    fp = fingerprint(UID, KEY)
    assert str(UID) not in fp
    assert len(fp) == 64 and int(fp, 16) >= 0, "hex sha256 digest"


def test_mask_keeps_only_the_last_four_digits():
    assert mask(196751008724043973) == "**************3973"
    assert str(UID)[:-4] not in mask(UID)


def test_mask_hides_a_short_id_entirely():
    assert mask(42) == "****"


# ---------------------------------------------------------------------------
# Key storage
# ---------------------------------------------------------------------------


def test_account_key_is_generated_once_and_reused(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(account_identity, "keyring", fake)

    first = account_identity.account_key()
    account_identity.reset_key_cache()
    second = account_identity.account_key()

    assert first == second, "the key must survive a cache drop, or every fingerprint changes"
    assert len(first) == 32
    assert fake.writes == 1, "an existing key must not be overwritten"


def test_account_key_is_cached_across_calls(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(account_identity, "keyring", fake)

    account_identity.account_key()
    reads_after_first = len(fake.store)
    account_identity.account_key()

    assert len(fake.store) == reads_after_first
    assert fake.writes == 1


def test_unreadable_stored_key_is_replaced(monkeypatch):
    """A corrupt entry must not wedge identification permanently."""
    service = keyring_service("identity")
    fake = FakeKeyring({(service, "account-id-key"): "not base64 !!!"})
    monkeypatch.setattr(account_identity, "keyring", fake)

    key = account_identity.account_key()

    assert len(key) == 32
    assert base64.b64decode(fake.store[(service, "account-id-key")]) == key


# ---------------------------------------------------------------------------
# Account lookup
# ---------------------------------------------------------------------------


def _accounts(fp: str | None = None, scheme: int = SCHEME) -> list[dict]:
    entry = {"username": "user1", "alias": "Jeff"}
    if fp is not None:
        entry["uid_fingerprint"] = fp
        entry["uid_scheme"] = scheme
    return [entry, {"username": "user2", "alias": "Emma"}]


def test_match_account_finds_the_owner():
    fp = fingerprint(UID, KEY)
    assert match_account(_accounts(fp), fp) == "user1"


def test_match_account_returns_none_for_an_unknown_fingerprint():
    assert match_account(_accounts(fingerprint(UID, KEY)), fingerprint(999, KEY)) is None


def test_match_account_ignores_an_older_scheme():
    """A fingerprint means nothing without knowing how it was built."""
    fp = fingerprint(UID, KEY)
    assert match_account(_accounts(fp, scheme=SCHEME - 1), fp) is None


def test_match_account_handles_accounts_with_no_fingerprint():
    assert match_account(_accounts(), fingerprint(UID, KEY)) is None


def test_stored_fingerprint_reads_back_what_was_written():
    fp = fingerprint(UID, KEY)
    updated = with_fingerprint(_accounts(), "user1", fp)
    assert stored_fingerprint(updated, "user1") == fp
    assert stored_fingerprint(updated, "user2") is None


def test_with_fingerprint_does_not_mutate_the_input():
    """The caller hands the result to the settings setter; mutating in place would make
    the write look like a no-op to anything comparing before and after."""
    accounts = _accounts()
    updated = with_fingerprint(accounts, "user1", fingerprint(UID, KEY))
    assert accounts[0] == {"username": "user1", "alias": "Jeff"}
    assert updated[0]["uid_fingerprint"] == fingerprint(UID, KEY)
    assert updated[0]["uid_scheme"] == SCHEME


def test_with_fingerprint_replaces_a_stale_one():
    accounts = with_fingerprint(_accounts(), "user1", fingerprint(UID, KEY))
    accounts = with_fingerprint(accounts, "user1", fingerprint(UID, OTHER_KEY))
    assert stored_fingerprint(accounts, "user1") == fingerprint(UID, OTHER_KEY)


def test_with_fingerprint_leaves_other_accounts_alone():
    updated = with_fingerprint(_accounts(), "user1", fingerprint(UID, KEY))
    assert updated[1] == {"username": "user2", "alias": "Emma"}
