"""Whether setup's Accounts page needs asking (epic #215, Stage 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bartlebot.shared.account_setup import ANSWERED_KEY, accounts_answered, record_accounts_answered
from bartlebot.shared.account_store import AccountStore
from bartlebot.shared.preferences import PreferenceStore


@pytest.fixture
def vendor(tmp_path: Path) -> Path:
    return tmp_path / "vendor"


def test_no_account_and_no_record_is_unanswered(vendor):
    assert not accounts_answered(PreferenceStore(vendor), AccountStore(vendor))


def test_an_account_answers_it_without_a_record(vendor):
    AccountStore(vendor).set_accounts([{"username": "one@example.com", "alias": ""}])

    assert accounts_answered(PreferenceStore(vendor), AccountStore(vendor))
    assert PreferenceStore(vendor).get(ANSWERED_KEY) is None


def test_the_record_answers_it_with_no_account(vendor):
    prefs = PreferenceStore(vendor)

    assert record_accounts_answered(prefs)
    assert accounts_answered(PreferenceStore(vendor), AccountStore(vendor))


def test_a_failed_write_says_so(vendor):
    vendor.parent.mkdir(parents=True, exist_ok=True)
    vendor.write_text("a file where the folder should be", encoding="utf-8")

    assert not record_accounts_answered(PreferenceStore(vendor))
