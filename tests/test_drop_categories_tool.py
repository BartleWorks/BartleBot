"""Tests for tools/drop_categories.py — the drop-category vocabulary survey."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.drop_categories import _UNCATEGORIZED, classify, scan  # noqa: E402


def _write(bots_dir: Path, name: str, drops: list) -> None:
    (bots_dir / name).parent.mkdir(parents=True, exist_ok=True)
    (bots_dir / name).write_text(json.dumps({"name": name, "drops": drops}), encoding="utf-8")


@pytest.fixture
def bots_dir(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_never_sold_categories_are_recognised():
    assert classify("Reagents") == "never sold"
    assert classify("Spellements") == "never sold"


def test_sellable_categories_are_recognised():
    assert classify("Hats") == "sellable"
    # Pins go through the jewel quicksell, so they are sellable despite the name.
    assert classify("Pins") == "sellable"


def test_missing_category_is_unknown_not_sellable():
    assert classify(_UNCATEGORIZED) == "unknown"


def test_a_new_category_is_flagged_for_classification():
    assert classify("Fishing Poles") == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def test_counts_drops_per_category(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Amber", "category": "Reagents"}, {"name": "Hat", "category": "Hats"}])
    _write(bots_dir, "b.json", [{"name": "Sunstone", "category": "Reagents"}])
    per_category, _, _ = scan(bots_dir)
    assert per_category["Reagents"] == 2
    assert per_category["Hats"] == 1


def test_tracks_which_bots_use_a_category(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Amber", "category": "Reagents"}])
    _write(bots_dir, "nested/b.json", [{"name": "Sunstone", "category": "Reagents"}])
    _, bots_using, _ = scan(bots_dir)
    assert len(bots_using["Reagents"]) == 2


def test_bare_string_drops_count_as_uncategorized(bots_dir):
    """The pre-category storage form still appears in unmigrated files."""
    _write(bots_dir, "a.json", ["Amber", {"name": "Hat", "category": "Hats"}])
    per_category, _, per_bot = scan(bots_dir)
    assert per_category[_UNCATEGORIZED] == 1
    assert per_bot["a.json"] == (2, 1)


def test_a_null_category_counts_as_uncategorized(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Amber", "category": None}])
    per_category, _, _ = scan(bots_dir)
    assert per_category[_UNCATEGORIZED] == 1


def test_bots_without_drops_are_skipped(bots_dir):
    _write(bots_dir, "a.json", [])
    _, _, per_bot = scan(bots_dir)
    assert per_bot == {}


def test_unreadable_files_do_not_stop_the_scan(bots_dir, capsys):
    (bots_dir / "broken.json").write_text("{not json", encoding="utf-8")
    _write(bots_dir, "good.json", [{"name": "Amber", "category": "Reagents"}])
    per_category, _, _ = scan(bots_dir)
    assert per_category["Reagents"] == 1
    assert "broken.json" in capsys.readouterr().err


def test_the_shipped_library_has_no_unclassified_categories():
    """Guards the real vocabulary: a wiki import producing a new label fails here."""
    per_category, _, _ = scan(Path(__file__).parent.parent / "bots")
    assert [c for c in per_category if classify(c) == "UNCLASSIFIED"] == []
