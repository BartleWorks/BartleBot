"""The on-disk cache of DSL check answers: it persists, it forgets on a new fingerprint, and it never fails a check."""

from __future__ import annotations

import json

from bartlebot.combat import dsl_check_cache
from bartlebot.combat.dsl_check_cache import DslCheckCache, cache_path, shared_cache, text_key
from bartlebot.shared.vendor import product_dir


def test_an_answer_put_in_one_session_is_there_in_the_next(tmp_path):
    path = tmp_path / "cache" / "dsl_checks.json"
    DslCheckCache(path, "fp").put("pass", {"error": None, "warnings": []})

    assert DslCheckCache(path, "fp").get("pass") == {"error": None, "warnings": []}


def test_a_different_fingerprint_discards_every_answer(tmp_path):
    path = tmp_path / "dsl_checks.json"
    DslCheckCache(path, "old grammar").put("pass", {"error": None, "warnings": []})

    assert DslCheckCache(path, "new grammar").get("pass") is None


def test_a_corrupt_file_reads_as_an_empty_cache(tmp_path):
    path = tmp_path / "dsl_checks.json"
    path.write_text("{ not json", encoding="utf-8")
    cache = DslCheckCache(path, "fp")

    assert cache.get("pass") is None
    cache.put("pass", 1)
    assert DslCheckCache(path, "fp").get("pass") == 1, "the next write replaces the corrupt file"


def test_a_write_that_fails_is_not_an_error(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the cache folder would go", encoding="utf-8")
    cache = DslCheckCache(blocker / "dsl_checks.json", "fp")

    cache.put("pass", 1)

    assert cache.get("pass") == 1, "the answer is still kept in memory"


def test_the_oldest_answers_go_first_once_it_is_full(tmp_path):
    path = tmp_path / "dsl_checks.json"
    cache = DslCheckCache(path, "fp", max_entries=2)
    for text in ("a", "b", "c"):
        cache.put(text, text)

    reread = DslCheckCache(path, "fp")
    assert [reread.get(text) for text in ("a", "b", "c")] == [None, "b", "c"]


def test_the_file_holds_hashes_of_the_strategies_never_their_text(tmp_path):
    path = tmp_path / "dsl_checks.json"
    DslCheckCache(path, "fp").put("any<blade>[sharp]@ally(2)", 1)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert list(stored["entries"]) == [text_key("any<blade>[sharp]@ally(2)")]
    assert "blade" not in path.read_text(encoding="utf-8")


def test_the_shared_cache_lives_in_the_product_directory_and_follows_appdata():
    assert cache_path() == product_dir() / "cache" / "dsl_checks.json"
    assert shared_cache("fp").path == cache_path()
    assert shared_cache("fp") is shared_cache("fp")


def test_the_cache_is_capped():
    assert dsl_check_cache.MAX_ENTRIES >= 512, "at least as many as the in-memory cache holds"
