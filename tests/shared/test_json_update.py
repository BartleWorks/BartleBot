"""``update_json``: change one part of a file another writer may be changing too (#205)."""

from __future__ import annotations

import json
import os

import pytest

from bartlebot.shared import json_update
from bartlebot.shared.json_update import UPDATE_RETRIES, UpdateConflict, update_json


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_an_absent_file_is_created_from_an_empty_record(tmp_path):
    path = tmp_path / "sub" / "f.json"

    update_json(path, lambda r: r.update(a=1))

    assert _read(path) == {"a": 1}


def test_keys_the_mutation_does_not_touch_are_kept(tmp_path):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"a": 1, "from_a_newer_version": {"x": [1, 2]}}), encoding="utf-8")

    update_json(path, lambda r: r.update(a=2))

    assert _read(path) == {"a": 2, "from_a_newer_version": {"x": [1, 2]}}


def test_a_write_that_was_overtaken_is_applied_again_to_what_the_other_writer_wrote(tmp_path):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    calls = []

    def mutate(record):
        calls.append(dict(record))
        if len(calls) == 1:  # another product writes between our read and our check
            path.write_text(json.dumps({"a": 1, "theirs": True}), encoding="utf-8")
        record["mine"] = True

    update_json(path, mutate)

    assert len(calls) == 2
    assert _read(path) == {"a": 1, "theirs": True, "mine": True}


def test_a_writer_that_always_loses_gives_up_and_writes_nothing(tmp_path):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"n": 0}), encoding="utf-8")
    attempts = []

    def mutate(record):
        attempts.append(1)
        path.write_text(json.dumps({"n": len(attempts)}), encoding="utf-8")
        record["mine"] = True

    with pytest.raises(UpdateConflict):
        update_json(path, mutate)

    assert len(attempts) == UPDATE_RETRIES
    assert "mine" not in _read(path)


def test_a_failure_before_the_rename_leaves_the_previous_file_whole_and_no_temporary(tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        update_json(path, lambda r: r.update(a=2))

    assert _read(path) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["f.json"]


def test_a_rename_refused_while_another_process_reads_is_tried_again(tmp_path, monkeypatch):
    """Windows refuses to rename over a file the other product has open to read."""
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    real_replace = os.replace
    refusals = []

    def busy_once(src, dst):
        if not refusals:
            refusals.append(1)
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", busy_once)
    monkeypatch.setattr(json_update, "REPLACE_RETRY_DELAY", 0)

    update_json(path, lambda r: r.update(a=2))

    assert refusals == [1]
    assert _read(path) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["f.json"]


def test_a_rename_refused_every_time_raises_the_refusal_and_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    attempts = []

    def refuse(src, dst):
        attempts.append(1)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "replace", refuse)
    monkeypatch.setattr(json_update, "REPLACE_RETRY_DELAY", 0)

    with pytest.raises(PermissionError):
        update_json(path, lambda r: r.update(a=2))

    assert len(attempts) == UPDATE_RETRIES
    assert _read(path) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["f.json"]


def test_a_file_that_is_not_an_object_is_replaced(tmp_path):
    path = tmp_path / "f.json"
    path.write_text("[1, 2", encoding="utf-8")

    update_json(path, lambda r: r.update(a=1))

    assert _read(path) == {"a": 1}


def test_what_an_unreadable_file_held_is_kept_beside_it(tmp_path, caplog):
    """It may be accounts.json: the copy is what lets it be mended rather than re-entered (#154)."""
    path = tmp_path / "accounts.json"
    path.write_bytes(b'{"accounts": [{"user')

    update_json(path, lambda r: r.update(a=1))

    assert (tmp_path / "accounts.json.unreadable").read_bytes() == b'{"accounts": [{"user'
    assert "accounts.json.unreadable" in caplog.text


def test_retry_count_is_bounded():
    assert json_update.UPDATE_RETRIES >= 2
