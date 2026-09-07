"""Tests for resolving a card's internal name to its display name.

Runs against a synthetic snapshot file, not the real one -- these tests need
neither the game installed nor a Root.wad read.
"""

from __future__ import annotations

import json

import pytest

from bartlebot.spell_names import _load_names, display_name


@pytest.fixture
def names_path(tmp_path):
    path = tmp_path / "spell_names.json"
    path.write_text(
        json.dumps({"names": {"Novus Storm - T05 - A": "Tree of Strife", "Soul Burner": "Ammut"}}),
        encoding="utf-8",
    )
    yield path
    _load_names.cache_clear()


def test_resolves_a_known_internal_name(names_path):
    assert display_name("Novus Storm - T05 - A", path=names_path) == "Tree of Strife"


def test_falls_back_to_the_internal_name_when_unmapped(names_path):
    assert display_name("Fire Cat", path=names_path) == "Fire Cat"


def test_falls_back_when_the_snapshot_file_is_missing(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert display_name("Soul Burner", path=missing) == "Soul Burner"
    _load_names.cache_clear()


def test_falls_back_when_the_snapshot_file_is_corrupt(tmp_path):
    path = tmp_path / "spell_names.json"
    path.write_text("not json", encoding="utf-8")
    assert display_name("Soul Burner", path=path) == "Soul Burner"
    _load_names.cache_clear()
