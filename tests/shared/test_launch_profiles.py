"""Building and adding a launch profile, shared by Settings › Game and setup (epic #215, Stage 4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.shared.account_store import AccountStore
from bartlebot.shared.launch_profiles import EVERYONE, add_profile, build_profile, copy_name, free_name, profile_names


@pytest.fixture
def store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path)


def _on_disk(store: AccountStore) -> list[dict]:
    return json.loads(store.path.read_text(encoding="utf-8"))["launch_profiles"]


def test_a_free_name_is_the_name_itself_when_nothing_has_it():
    assert free_name(EVERYONE, set()) == "Everyone"
    assert free_name(EVERYONE, {"Solo"}) == "Everyone"


def test_a_taken_name_gets_the_next_number():
    assert free_name(EVERYONE, {"Everyone"}) == "Everyone 2"
    assert free_name(EVERYONE, {"Everyone", "Everyone 2"}) == "Everyone 3"


def test_a_copy_name_skips_a_number_already_taken():
    assert copy_name("Solo", set()) == "Solo (copy)"
    assert copy_name("Solo", {"Solo (copy)"}) == "Solo (copy 2)"
    assert copy_name("Solo", {"Solo (copy)", "Solo (copy 2)"}) == "Solo (copy 3)"


def test_a_profile_holds_its_accounts_in_order_and_a_resolution_only_when_given():
    assert build_profile("Duo", ["b@example.com", "a@example.com"]) == {
        "name": "Duo",
        "accounts": ["b@example.com", "a@example.com"],
    }
    assert build_profile("Duo", ["a@example.com"], (1280, 720))["resolution"] == [1280, 720]


def test_adding_a_profile_appends_it_on_disk(store):
    store.set_launch_profiles([build_profile("Solo", ["a@example.com"])])

    assert add_profile(store, build_profile("Duo", ["a@example.com", "b@example.com"]))

    assert [p["name"] for p in _on_disk(store)] == ["Solo", "Duo"]
    assert profile_names(store) == ["Solo", "Duo"]


def test_a_name_already_taken_is_refused_and_nothing_written(store):
    store.set_launch_profiles([build_profile("Duo", ["a@example.com"])])

    assert not add_profile(store, build_profile("Duo", ["b@example.com"]))

    assert _on_disk(store) == [{"name": "Duo", "accounts": ["a@example.com"]}]


def test_a_failed_write_is_reported(store, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("bartlebot.shared.account_store.update_json", fail)

    assert not add_profile(store, build_profile("Duo", ["a@example.com"]))
