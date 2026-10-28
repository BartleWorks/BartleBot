"""Tests for tools/game_update/compare_installs.py: what changed in Root.wad between two installs."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tests.art_fixture import write_wad  # noqa: E402
from tools.game_update import compare_installs as compare  # noqa: E402


def test_diff_wads_compares_entries_by_content(tmp_path):
    write_wad(tmp_path / "old.wad", {"Kept.xml": b"same", "Changed.xml": b"before", "Gone.xml": b"x"})
    write_wad(tmp_path / "new.wad", {"Kept.xml": b"same", "Changed.xml": b"after", "Spells/New.xml": b"y"})
    diff = compare.diff_wads(tmp_path / "old.wad", tmp_path / "new.wad")
    assert (diff.added, diff.removed, diff.changed) == (["Spells/New.xml"], ["Gone.xml"], ["Changed.xml"])


def test_per_object_entries_and_other_locales_are_bulk():
    assert compare.is_bulk("Spells/Tiered Spells/Novus Storm - T05 - A.xml")
    assert compare.is_bulk("Guest_AH_WhaleBird_Base_A_01Template.xml")
    assert compare.is_bulk("Locale/fr/WorldNames.lang")
    assert compare.is_bulk("Locale/en-US/WizQst1ABBCE.lang")
    assert not compare.is_bulk("Locale/en-US/WorldNames.lang")
    assert not compare.is_bulk("zonemap.nav")
    assert not compare.is_bulk("GUI/Settings.gui")
