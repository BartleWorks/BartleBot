"""Tests for tools/dump_spell_schools.py: it writes core's snapshot, in the core checkout beside this one."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import dump_spell_schools as tool  # noqa: E402


def test_the_snapshot_it_writes_is_in_the_core_checkout_beside_this_one():
    """Never the copy of core in .venv, which uv links from its cache (BartleBuddy #31, Stage 5)."""
    assert tool.CORE_SNAPSHOT.parent.parent.parent.parent.parent == project_root().parent / "bartleby-core"
    assert tool.CORE_SNAPSHOT.parts[-6:] == (
        "bartleby-core",
        "bartleby",
        "runtime",
        "game",
        "data",
        "spell_schools.json",
    )
    assert ".venv" not in tool.CORE_SNAPSHOT.parts


def test_with_no_core_checkout_it_asks_for_out_and_writes_nothing(tmp_path, monkeypatch, capsys):
    wad, types = tmp_path / "Root.wad", tmp_path / "types.json"
    wad.write_bytes(b"")
    types.write_text("{}", encoding="utf-8")
    missing = tmp_path / "bartleby-core" / "bartleby" / "runtime" / "game" / "data" / "spell_schools.json"
    monkeypatch.setattr(tool, "CORE_SNAPSHOT", missing)
    monkeypatch.setattr(
        sys, "argv", ["dump_spell_schools.py", "--wad", str(wad), "--types", str(types), "--out", str(missing)]
    )

    assert tool.main() == 1
    assert "pass --out" in capsys.readouterr().out
    assert not missing.exists()
