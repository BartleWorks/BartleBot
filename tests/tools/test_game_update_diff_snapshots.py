"""Tests for tools/game_update/diff_snapshots.py: what a patch changes in the shipped game data."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools.game_update import diff_snapshots as snapshots  # noqa: E402


def test_differences_names_each_added_removed_and_changed_value():
    old = {"names": {"Kept": "A", "Renamed": "Old", "Retired": "B"}}
    new = {"names": {"Kept": "A", "Renamed": "New", "Added": "C"}}
    assert snapshots.differences(old, new) == [
        '+ /names/Added = "C"',
        '~ /names/Renamed: "Old" -> "New"',
        '- /names/Retired (was "B")',
    ]


def test_the_run_stamps_are_ignored_at_the_top_level_only():
    old = {"generated": "2026-01-01", "source": "a", "names": {"generated": "x"}}
    new = {"generated": "2026-10-21", "source": "b", "names": {"generated": "y"}}
    assert snapshots.differences(old, new) == ['~ /names/generated: "x" -> "y"']


def test_a_list_of_keyed_objects_is_compared_key_by_key():
    old = {"categories": [{"key": "A", "n": 1}, {"key": "B", "n": 2}]}
    new = {"categories": [{"key": "A", "n": 1}, {"key": "New", "n": 3}, {"key": "B", "n": 2}]}
    assert snapshots.differences(old, new) == ['+ /categories/New = {"key": "New", "n": 3}']


def test_a_reorder_of_keyed_objects_is_reported():
    old = {"categories": [{"key": "A"}, {"key": "B"}]}
    new = {"categories": [{"key": "B"}, {"key": "A"}]}
    assert snapshots.differences(old, new) == ["~ /categories: the order of the shared keys changed"]


def test_it_never_regenerates_into_the_shipped_data(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["diff_snapshots.py", "--out", str(snapshots.DATA)])
    assert snapshots.main() == 2
    assert "scratch folder" in capsys.readouterr().out
