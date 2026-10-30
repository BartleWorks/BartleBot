"""Tests for tools/render_sheet.py: every surface draws, none is blank, and two runs agree.

Two runs on one tree comparing identical is what lets a later difference mean a real change.
The sheet is drawn through bartleby-core's render sheet.
"""

import sys

from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager

sys.path.insert(0, str(project_root()))

from tools import render_sheet  # noqa: E402

core = render_sheet.core_render_sheet()


def test_the_sheet_draws_every_surface_in_both_themes_and_two_runs_agree(qapp, tmp_path):
    first = render_sheet.render(tmp_path / "a")
    render_sheet.render(tmp_path / "b")

    names = {p.name for p in first}
    assert names == {f"{s.name}.{t}.png" for s in render_sheet.surfaces() for t in ("light", "dark")}
    assert len(names) == 24
    assert [p.name for p in first if core.is_blank(p)] == []
    assert core.compare(tmp_path / "a", tmp_path / "b") == []


def test_the_sample_data_is_put_back(qapp):
    sm = SettingsManager.instance()
    sm.accounts = [{"username": "mine@example.com", "alias": "Mine"}]

    with render_sheet.sample_data():
        assert [a["alias"] for a in sm.accounts] == ["P1", "P2", "P3", "P4"]

    assert [a["alias"] for a in sm.accounts] == ["Mine"]


def test_the_sample_accounts_carry_no_real_name():
    """Slots and example.com addresses only: a sheet is pasted into reviews and issues."""
    assert all(a["username"].endswith("@example.com") for a in render_sheet.ACCOUNTS)
    assert [a["alias"] for a in render_sheet.ACCOUNTS] == ["P1", "P2", "P3", "P4"]
