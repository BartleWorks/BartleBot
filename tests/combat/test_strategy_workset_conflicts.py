"""The combat strategy working set when another program writes the shared library (#168).

Save never overwrites a file changed since the dialog loaded it. It lists the refused
write as a conflict and keeps the entry dirty; the user then reloads the other
program's version, or keeps theirs as a copy under a new id.
"""

from __future__ import annotations

import json
from pathlib import Path

from bartleby.shared.strategy_models import LIBRARY_FORMAT, PlayerCombatStrategy

from bartlebot.combat.strategy_store import (
    StrategyKind,
    list_player_strategies,
    load_player_strategy,
    save_player_strategy,
)
from bartlebot.combat.strategy_workset import StrategyWorkingSet

PLAYER = StrategyKind.PLAYER


def _file(root: Path) -> Path:
    (path,) = (root / "strategies" / "player").glob("*.json")
    return path


def _edit_elsewhere(root: Path, **changes) -> None:
    path = _file(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _loaded_then_changed(tmp_path: Path) -> tuple[StrategyWorkingSet, PlayerCombatStrategy]:
    """A working set holding one strategy, edited here, which another program has since changed."""
    s = PlayerCombatStrategy(name="Blade", dsl="original")
    save_player_strategy(s, tmp_path)
    ws = StrategyWorkingSet.from_store(tmp_path)
    ws.get(PLAYER, s.id).dsl = "mine"
    _edit_elsewhere(tmp_path, dsl="theirs")
    return ws, s


def test_an_unchanged_file_saves_as_before(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    save_player_strategy(s, tmp_path)
    ws = StrategyWorkingSet.from_store(tmp_path)
    ws.get(PLAYER, s.id).dsl = "mine"

    ws.commit()

    assert ws.conflicts == []
    assert not ws.is_dirty
    assert load_player_strategy(s.id, tmp_path).dsl == "mine"

    # And again: the fingerprint moved on with the save.
    ws.get(PLAYER, s.id).dsl = "mine again"
    ws.commit()
    assert load_player_strategy(s.id, tmp_path).dsl == "mine again"


def test_a_file_changed_since_load_is_listed_and_left_alone(tmp_path):
    ws, s = _loaded_then_changed(tmp_path)

    ws.commit()

    (conflict,) = ws.conflicts
    assert (conflict.kind, conflict.uuid, conflict.name) == (PLAYER, s.id, "Blade")
    assert not conflict.newer_format and not conflict.deleted
    assert ws.is_dirty, "the edit is still unsaved, so Cancel must still ask"
    assert load_player_strategy(s.id, tmp_path).dsl == "theirs"


def test_reload_takes_the_other_programs_version(tmp_path):
    ws, s = _loaded_then_changed(tmp_path)
    ws.commit()

    reloaded = ws.reload(PLAYER, s.id)

    assert reloaded.dsl == "theirs"
    assert ws.get(PLAYER, s.id) is reloaded
    assert ws.conflicts == []
    assert not ws.is_dirty


def test_keep_as_copy_saves_the_edit_beside_the_other_programs(tmp_path):
    ws, s = _loaded_then_changed(tmp_path)
    ws.commit()

    copy = ws.keep_as_copy(PLAYER, s.id)
    ws.commit()

    assert ws.conflicts == []
    assert not ws.is_dirty
    assert copy.id != s.id and copy.name == "Blade (2)"
    on_disk = {p.id: p for p in list_player_strategies(tmp_path)}
    assert on_disk[s.id].dsl == "theirs"
    assert on_disk[copy.id].dsl == "mine"


def test_a_delete_of_a_changed_file_is_refused_and_reload_brings_it_back(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    save_player_strategy(s, tmp_path)
    ws = StrategyWorkingSet.from_store(tmp_path)
    ws.remove(PLAYER, s.id)
    _edit_elsewhere(tmp_path, dsl="theirs")

    ws.commit()

    (conflict,) = ws.conflicts
    assert conflict.deleted and conflict.name == "Blade"
    assert _file(tmp_path).exists()
    assert ws.reload(PLAYER, s.id).dsl == "theirs"
    assert not ws.is_dirty


def test_an_entry_another_program_deleted_leaves_on_reload(tmp_path):
    ws, s = _loaded_then_changed(tmp_path)
    _file(tmp_path).unlink()
    ws.commit()

    assert ws.reload(PLAYER, s.id) is None
    assert ws.get(PLAYER, s.id) is None
    assert not ws.is_dirty


def test_an_entry_in_a_newer_format_is_read_only_and_never_saved(tmp_path):
    s = PlayerCombatStrategy(name="Blade")
    save_player_strategy(s, tmp_path)
    _edit_elsewhere(tmp_path, format=LIBRARY_FORMAT + 1)
    before = _file(tmp_path).read_bytes()
    ws = StrategyWorkingSet.from_store(tmp_path)

    assert ws.is_read_only(PLAYER, s.id)
    ws.get(PLAYER, s.id).dsl = "mine"
    ws.commit()

    (conflict,) = ws.conflicts
    assert conflict.newer_format
    assert _file(tmp_path).read_bytes() == before


def test_a_set_built_without_fingerprints_reads_them_from_disk(tmp_path):
    """The constructor tests use: what is on disk now is what the entries were loaded with."""
    s = PlayerCombatStrategy(name="Blade")
    save_player_strategy(s, tmp_path)
    ws = StrategyWorkingSet(players=list_player_strategies(tmp_path), duels=[], decks=[], root=tmp_path)
    ws.get(PLAYER, s.id).dsl = "mine"

    ws.commit()

    assert ws.conflicts == []
    assert load_player_strategy(s.id, tmp_path).dsl == "mine"
