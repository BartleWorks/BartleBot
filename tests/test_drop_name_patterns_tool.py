"""Tests for tools/drop_name_patterns.py.

The tool exists to make one silent failure loud: a wiki page introduces a name
suffix nobody has classified, the drop keeps a name the game never shows, and a
keep-list entry built from it matches nothing. The last test here is the guard —
it fails when the shipped library contains such a suffix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bartlebot.paths import project_root

PROJECT_ROOT = project_root()
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.wiki_drops import (  # noqa: E402
    KIND_LEVEL,
    KIND_LEVEL_AND_SCHOOL,
    KIND_SCHOOL,
    KIND_UNCLASSIFIED,
)
from tools.drop_name_patterns import scan  # noqa: E402


def _write(bots_dir: Path, name: str, drops: list) -> None:
    (bots_dir / name).write_text(json.dumps({"name": name, "drops": drops}), encoding="utf-8")


@pytest.fixture
def bots_dir(tmp_path):
    return tmp_path


def test_counts_names_by_suffix_kind(bots_dir):
    _write(
        bots_dir,
        "a.json",
        [
            {"name": "Balance Crushing Pin (60, Death)"},
            {"name": "Balance Crushing Pin (60, Fire)"},
            {"name": "Balance Accurate Pin (30)"},
        ],
    )
    per_kind, _, _ = scan(bots_dir)
    assert per_kind[KIND_LEVEL_AND_SCHOOL] == 2
    assert per_kind[KIND_LEVEL] == 1


def test_names_without_a_suffix_are_not_counted(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Amber"}, {"name": "Bone Dust"}])
    per_kind, _, _ = scan(bots_dir)
    assert sum(per_kind.values()) == 0


def test_bare_string_drops_are_scanned_too(bots_dir):
    # The pre-category storage form, which carries no category.
    _write(bots_dir, "a.json", ["Balance Crushing Pin (60, Death)"])
    per_kind, _, _ = scan(bots_dir)
    assert per_kind[KIND_LEVEL_AND_SCHOOL] == 1


def test_the_category_decides_the_kind_for_a_school_suffix(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Bright Critical Amethyst +44 (Ice)", "category": "Jewels"}])
    per_kind, _, _ = scan(bots_dir)
    assert per_kind[KIND_SCHOOL] == 1


def test_a_new_suffix_is_flagged_for_classification(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Widget (Brand New Thing)"}])
    per_kind, examples, _ = scan(bots_dir)
    assert per_kind[KIND_UNCLASSIFIED] == 1
    assert examples[KIND_UNCLASSIFIED][0][0] == "Widget (Brand New Thing)"


def test_a_missing_match_name_counts_as_needing_migration(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Balance Crushing Pin (60, Death)"}])
    _, _, stale = scan(bots_dir)
    assert stale[KIND_LEVEL_AND_SCHOOL] == 1


def test_a_stored_match_name_that_agrees_is_not_stale(bots_dir):
    _write(
        bots_dir,
        "a.json",
        [{"name": "Balance Crushing Pin (60, Death)", "match_name": "Balance Crushing Pin (60)"}],
    )
    _, _, stale = scan(bots_dir)
    assert sum(stale.values()) == 0


def test_a_stale_match_name_is_reported(bots_dir):
    _write(bots_dir, "a.json", [{"name": "Balance Crushing Pin (60, Death)", "match_name": "Something Old"}])
    _, _, stale = scan(bots_dir)
    assert stale[KIND_LEVEL_AND_SCHOOL] == 1


def test_unreadable_files_do_not_stop_the_scan(bots_dir, capsys):
    (bots_dir / "broken.json").write_text("{ not json", encoding="utf-8")
    _write(bots_dir, "a.json", [{"name": "Balance Crushing Pin (60, Death)"}])
    per_kind, _, _ = scan(bots_dir)
    assert per_kind[KIND_LEVEL_AND_SCHOOL] == 1
    assert "[ERR]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_the_shipped_library_has_no_unclassified_suffixes():
    per_kind, examples, _ = scan(PROJECT_ROOT / "bots")
    assert per_kind.get(KIND_UNCLASSIFIED, 0) == 0, (
        f"Unclassified drop name suffixes: {[n for n, _, _ in examples[KIND_UNCLASSIFIED]]}. "
        "Decide whether the game shows the suffix, then add a rule to bartlebot/wiki_drops.py."
    )


def test_the_shipped_library_has_no_stale_in_game_names():
    _, _, stale = scan(PROJECT_ROOT / "bots")
    assert sum(stale.values()) == 0, "Run: uv run python scripts/migrate_drop_entries.py --apply"
