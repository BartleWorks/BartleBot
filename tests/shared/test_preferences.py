"""``PreferenceStore``: preferences two products can write at once (#205)."""

from __future__ import annotations

import json
import os

import pytest

from bartlebot.shared.preferences import FILENAME, SCHEMA_VERSION, PreferenceStore
from bartlebot.shared.vendor import vendor_dir


@pytest.fixture
def directory(tmp_path):
    return tmp_path / "vendor"


def test_a_value_round_trips_through_a_second_store(directory):
    PreferenceStore(directory).set("combat.default_duel.solo", "some-id")

    assert PreferenceStore(directory).get("combat.default_duel.solo") == "some-id"


def test_an_unset_key_reads_as_its_default(directory):
    store = PreferenceStore(directory)

    assert store.get("combat.nothing") is None
    assert store.get("combat.nothing", 7) == 7
    assert not store.path.exists(), "a read is not a reason to write"


def test_two_stores_setting_different_keys_both_survive(directory):
    first, second = PreferenceStore(directory), PreferenceStore(directory)

    first.set("a.one", 1)
    second.set("b.two", 2)

    assert first.get("a.one") == 1
    assert second.get("b.two") == 2
    assert first.get("b.two") == 2, "a read sees what the other writer wrote"


def test_a_key_from_a_newer_version_survives_a_write_by_this_one(directory):
    directory.mkdir()
    (directory / FILENAME).write_text(json.dumps({"version": 2, "future.key": {"x": 1}}), encoding="utf-8")

    PreferenceStore(directory).set("a.one", 1)

    record = json.loads((directory / FILENAME).read_text(encoding="utf-8"))
    assert record["future.key"] == {"x": 1}
    assert record["a.one"] == 1
    assert record["version"] == 2, "a newer version's marker is not lowered"


def test_the_file_carries_a_version(directory):
    store = PreferenceStore(directory)
    store.set("a", 1)

    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == SCHEMA_VERSION


def test_clear_removes_only_that_key(directory):
    store = PreferenceStore(directory)
    store.set("a", 1)
    store.set("b", 2)

    assert store.clear("a")

    assert store.get("a") is None
    assert store.get("b") == 2


def test_clearing_an_absent_key_writes_nothing(directory):
    store = PreferenceStore(directory)

    assert store.clear("a")

    assert not store.path.exists()


def test_a_write_that_fails_before_the_rename_leaves_the_previous_file_whole(directory, monkeypatch):
    store = PreferenceStore(directory)
    store.set("a", 1)
    before = store.path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    assert store.set("a", 2) is False

    assert store.path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in directory.iterdir()) == [FILENAME]


def test_the_version_key_is_not_a_preference(directory):
    store = PreferenceStore(directory)

    with pytest.raises(ValueError):
        store.set("version", 3)
    assert store.get("version", "d") == "d"


def test_the_default_directory_is_the_vendor_root():
    assert PreferenceStore().path == vendor_dir() / FILENAME
