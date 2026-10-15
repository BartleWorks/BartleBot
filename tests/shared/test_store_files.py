"""``held_listings``: one listing per directory for a pass over many ids, and never a stale one after a write.

The filename rules themselves are tested in ``tests/content/test_store_filenames.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.shared import store_files
from bartlebot.shared.store_files import delete_by_id, find_by_id, held_listings, save_by_id


def _write(d: Path, name: str, item_id: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    path.write_text(json.dumps({"id": item_id, "name": name}), encoding="utf-8")
    return path


def _writer(item_id: str):
    return lambda path: path.write_text(json.dumps({"id": item_id}), encoding="utf-8")


@pytest.fixture
def listings(monkeypatch):
    """How many times each directory was actually listed."""
    counts: dict[Path, int] = {}
    real = store_files._scan_now

    def counting(d):
        counts[d] = counts.get(d, 0) + 1
        return real(d)

    monkeypatch.setattr(store_files, "_scan_now", counting)
    return counts


def test_inside_the_block_a_directory_is_listed_once_for_any_number_of_lookups(tmp_path, listings):
    for i in range(5):
        _write(tmp_path, f"item-{i}", f"id-{i}")

    with held_listings():
        found = [find_by_id(tmp_path, f"id-{i}") for i in range(5)]

    assert all(found)
    assert listings == {tmp_path: 1}


def test_outside_the_block_every_lookup_sees_the_disk_as_it_is_now(tmp_path, listings):
    _write(tmp_path, "a", "id-a")
    assert find_by_id(tmp_path, "id-b") is None

    _write(tmp_path, "b", "id-b")

    assert find_by_id(tmp_path, "id-b") is not None
    assert listings[tmp_path] == 2


def test_a_save_inside_the_block_is_seen_by_the_next_lookup(tmp_path):
    _write(tmp_path, "a", "id-a")
    with held_listings():
        assert find_by_id(tmp_path, "id-new") is None
        save_by_id(tmp_path, "id-new", "New", _writer("id-new"))

        assert find_by_id(tmp_path, "id-new") is not None


def test_a_delete_inside_the_block_is_seen_by_the_next_lookup(tmp_path):
    _write(tmp_path, "a", "id-a")
    with held_listings():
        assert find_by_id(tmp_path, "id-a") is not None
        delete_by_id(tmp_path, "id-a")

        assert find_by_id(tmp_path, "id-a") is None


def test_a_nested_block_shares_the_outer_listings(tmp_path, listings):
    _write(tmp_path, "a", "id-a")
    with held_listings():
        find_by_id(tmp_path, "id-a")
        with held_listings():
            find_by_id(tmp_path, "id-a")
        find_by_id(tmp_path, "id-a")

    assert listings == {tmp_path: 1}


def test_the_block_lets_go_when_it_ends(tmp_path, listings):
    _write(tmp_path, "a", "id-a")
    with held_listings():
        find_by_id(tmp_path, "id-a")
    find_by_id(tmp_path, "id-a")

    assert listings == {tmp_path: 2}
