"""Tests for the Save/Cancel boundary: bartlebot.strategy_workset.

Qt-free by construction — the working set is what makes Cancel mean something,
and none of that reasoning needs a QApplication to check.
"""

from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.strategy_store import (
    Origin,
    StrategyKind,
    list_duel_strategies,
    list_player_strategies,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.strategy_workset import StrategyWorkingSet


def _ws(tmp_path: Path, **kw) -> StrategyWorkingSet:
    return StrategyWorkingSet(
        players=kw.get("players", []),
        duels=kw.get("duels", []),
        decks=kw.get("decks", []),
        bot_strategies=kw.get("bot_strategies", []),
        root=tmp_path,
    )


# ---------------------------------------------------------------------------
# The boundary itself
# ---------------------------------------------------------------------------


def test_editing_a_copy_leaves_the_caller_list_untouched(tmp_path: Path):
    """The Combat tab is still drawing from the lists it handed over."""
    original = PlayerCombatStrategy(name="Blade", dsl="any<blade> @ self")
    ws = _ws(tmp_path, players=[original])

    ws.get(StrategyKind.PLAYER, original.id).name = "Edited"

    assert original.name == "Blade"


def test_nothing_reaches_disk_until_commit(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Blade")
    save_player_strategy(pcs, tmp_path)
    ws = _ws(tmp_path, players=[pcs])

    ws.get(StrategyKind.PLAYER, pcs.id).name = "Edited"
    assert list_player_strategies(tmp_path)[0].name == "Blade"

    ws.commit()
    assert list_player_strategies(tmp_path)[0].name == "Edited"


def test_discarding_the_set_is_the_whole_of_cancel(tmp_path: Path):
    """Cancel is "drop the working set" — there is nothing to roll back."""
    pcs = PlayerCombatStrategy(name="Blade")
    save_player_strategy(pcs, tmp_path)

    ws = _ws(tmp_path, players=[pcs])
    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "garbage"
    ws.remove(StrategyKind.PLAYER, pcs.id)
    del ws  # no commit

    assert list_player_strategies(tmp_path)[0].name == "Blade"


# ---------------------------------------------------------------------------
# Dirtiness is derived, never reported
# ---------------------------------------------------------------------------


def test_a_fresh_set_is_clean(tmp_path: Path):
    ws = _ws(tmp_path, players=[PlayerCombatStrategy(name="A")], duels=[DuelCombatStrategy(name="B")])
    assert not ws.is_dirty


def test_an_edit_typed_back_out_leaves_it_clean(tmp_path: Path):
    """What a dirty flag set on a ``changed`` signal cannot say."""
    pcs = PlayerCombatStrategy(name="Blade")
    ws = _ws(tmp_path, players=[pcs])

    live = ws.get(StrategyKind.PLAYER, pcs.id)
    live.name = "Something else"
    assert ws.is_dirty

    live.name = "Blade"
    assert not ws.is_dirty


def test_a_nested_mutation_counts_as_modified(tmp_path: Path):
    """DuelConfigEditor mutates assignments in place and reports nothing."""
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1], dsl="x")])
    ws = _ws(tmp_path, duels=[dcs])

    ws.get(StrategyKind.DUEL, dcs.id).player_assignments[0].dsl = "y"

    assert [c for c in ws.changes().modified] == [dcs.id]


def test_changes_can_be_asked_for_one_kind(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="A")
    dcs = DuelCombatStrategy(name="B")
    ws = _ws(tmp_path, players=[pcs], duels=[dcs])
    ws.get(StrategyKind.PLAYER, pcs.id).name = "A2"

    assert ws.changes(StrategyKind.PLAYER).modified == [pcs.id]
    assert not ws.changes(StrategyKind.DUEL)


# ---------------------------------------------------------------------------
# Create / duplicate / remove
# ---------------------------------------------------------------------------


def test_create_names_uniquely_and_reads_as_created(tmp_path: Path):
    ws = _ws(tmp_path, players=[PlayerCombatStrategy(name="New Strategy")])
    made = ws.create(StrategyKind.PLAYER, "New Strategy")

    assert made.name == "New Strategy (2)"
    assert ws.changes().created == [made.id]


def test_duplicate_takes_a_new_id(tmp_path: Path):
    """An override shares its id with the shipped file it shadows; a duplicate must not."""
    dcs = DuelCombatStrategy(name="Storm AoE")
    ws = _ws(tmp_path, duels=[dcs])

    copy = ws.duplicate(StrategyKind.DUEL, dcs.id)

    assert copy.id != dcs.id
    assert copy.name == "Storm AoE (2)"
    assert len(ws.duels) == 2


def test_duplicating_a_duplicate_counts_from_the_stem(tmp_path: Path):
    ws = _ws(tmp_path, duels=[DuelCombatStrategy(name="Storm AoE")])
    first = ws.duplicate(StrategyKind.DUEL, ws.duels[0].id)
    second = ws.duplicate(StrategyKind.DUEL, first.id)

    assert second.name == "Storm AoE (3)"


def test_removing_something_created_here_records_no_tombstone(tmp_path: Path):
    """There is no file to delete, and counting one would overstate what Save does."""
    ws = _ws(tmp_path)
    made = ws.create(StrategyKind.PLAYER, "Temp")
    ws.remove(StrategyKind.PLAYER, made.id)

    assert not ws.is_dirty


def test_remove_then_commit_deletes_the_file(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Doomed")
    save_player_strategy(pcs, tmp_path)
    ws = _ws(tmp_path, players=[pcs])

    ws.remove(StrategyKind.PLAYER, pcs.id)
    assert ws.changes().deleted == [pcs.id]

    ws.commit()
    assert list_player_strategies(tmp_path) == []


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def test_commit_re_baselines_so_a_second_save_writes_nothing(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Blade")
    save_player_strategy(pcs, tmp_path)
    ws = _ws(tmp_path, players=[pcs])
    ws.get(StrategyKind.PLAYER, pcs.id).name = "Edited"

    assert ws.commit().modified == [pcs.id]
    assert not ws.is_dirty
    assert not ws.commit()


def test_commit_writes_only_to_the_user_root(tmp_path: Path):
    """Copy-on-write falls out of the store; this checks the set honours both roots."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    dcs = DuelCombatStrategy(name="Shipped")
    save_duel_strategy(dcs, shipped)

    ws = StrategyWorkingSet(
        players=[], duels=list_duel_strategies(shipped, user), decks=[], root=shipped, user_root=user
    )
    ws.get(StrategyKind.DUEL, dcs.id).name = "Mine"
    ws.commit()

    assert not (shipped / "strategies" / "duel" / "shipped.json").read_text().count("Mine")
    assert (user / "strategies" / "duel" / "mine.json").exists()
    assert list_duel_strategies(shipped, user)[0].name == "Mine"


def test_a_failed_write_leaves_the_entry_dirty(tmp_path: Path, monkeypatch):
    """One unwritable file must not forfeit the session, and must not read as saved."""
    import bartlebot.strategy_workset as ws_mod

    pcs = PlayerCombatStrategy(name="Blade")
    save_player_strategy(pcs, tmp_path)
    ws = _ws(tmp_path, players=[pcs])
    ws.get(StrategyKind.PLAYER, pcs.id).name = "Edited"

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setitem(ws_mod._SAVERS, StrategyKind.PLAYER, boom)
    ws.commit()

    assert ws.is_dirty
    assert list_player_strategies(tmp_path)[0].name == "Blade"


def test_deck_presets_round_trip(tmp_path: Path):
    preset = DeckPreset(name="Storm 60", deck=DeckData(normal={"Storm Blade": 4}))
    ws = _ws(tmp_path, decks=[preset])
    ws.get(StrategyKind.DECK, preset.id).name = "Storm 60 v2"
    ws.commit()

    from bartlebot.strategy_store import list_deck_presets  # noqa: PLC0415

    assert list_deck_presets(tmp_path)[0].name == "Storm 60 v2"


# ---------------------------------------------------------------------------
# Usage counting against unsaved state
# ---------------------------------------------------------------------------


def test_usage_sees_a_link_made_a_minute_ago(tmp_path: Path):
    """The reason find_references takes lists rather than reading the store."""
    pcs = PlayerCombatStrategy(name="Blade")
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    ws = _ws(tmp_path, players=[pcs], duels=[dcs])

    assert ws.usage(pcs.id).total == 0

    ws.get(StrategyKind.DUEL, dcs.id).player_assignments[0].player_combat_strategy_id = pcs.id

    assert ws.usage(pcs.id).duels == 1


def test_usage_counts_bot_strategies_too(tmp_path: Path):
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(name="Farm", bot_id="b", default_strategy_id=dcs.id)
    ws = _ws(tmp_path, duels=[dcs], bot_strategies=[bcs])

    assert ws.usage(dcs.id).bots == 1


def test_items_are_name_sorted(tmp_path: Path):
    ws = _ws(
        tmp_path,
        players=[PlayerCombatStrategy(name="zeta"), PlayerCombatStrategy(name="Alpha")],
    )
    assert [p.name for p in ws.players] == ["Alpha", "zeta"]


# ---------------------------------------------------------------------------
# Built-ins: status, revert, and never delete (#124)
# ---------------------------------------------------------------------------


def _two_roots(tmp_path: Path, *shipped_items: PlayerCombatStrategy) -> tuple[Path, Path]:
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    for item in shipped_items:
        save_player_strategy(item, shipped)
    (user / "strategies" / "player").mkdir(parents=True, exist_ok=True)
    return shipped, user


def _reopen(shipped: Path, user: Path) -> StrategyWorkingSet:
    return StrategyWorkingSet.from_store(root=shipped, user_root=user)


def test_an_untouched_built_in_reads_as_built_in(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Support", dsl="any<blade> @ ally")
    ws = _reopen(*_two_roots(tmp_path, pcs))

    assert ws.status(StrategyKind.PLAYER, pcs.id) is Origin.SHIPPED


def test_an_unsaved_edit_to_a_built_in_already_reads_as_modified(tmp_path: Path):
    """The defect behind #124's third point: disk said unedited, so Revert was not offered."""
    pcs = PlayerCombatStrategy(name="Support")
    ws = _reopen(*_two_roots(tmp_path, pcs))

    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "pass"

    assert ws.status(StrategyKind.PLAYER, pcs.id) is Origin.SHIPPED_OVERRIDE


def test_an_entry_made_here_reads_as_custom(tmp_path: Path):
    ws = _reopen(*_two_roots(tmp_path))

    made = ws.create(StrategyKind.PLAYER, "Mine")

    assert ws.status(StrategyKind.PLAYER, made.id) is Origin.USER


def test_one_root_means_nothing_is_built_in(tmp_path: Path):
    """Developer mode, where the repo is both roots: everything is the author's own."""
    pcs = PlayerCombatStrategy(name="Support")
    save_player_strategy(pcs, tmp_path)
    ws = StrategyWorkingSet.from_store(root=tmp_path)

    assert ws.status(StrategyKind.PLAYER, pcs.id) is Origin.USER
    ws.remove(StrategyKind.PLAYER, pcs.id)
    assert ws.changes().deleted == [pcs.id]


def test_a_built_in_cannot_be_removed(tmp_path: Path):
    """Removing only touches the user root, so it would be back on the next open."""
    pcs = PlayerCombatStrategy(name="Support")
    ws = _reopen(*_two_roots(tmp_path, pcs))

    ws.remove(StrategyKind.PLAYER, pcs.id)

    assert ws.get(StrategyKind.PLAYER, pcs.id) is not None
    assert not ws.is_dirty


def test_reverting_a_saved_override_keeps_the_entry_in_the_set(tmp_path: Path):
    """The defect behind #124's fourth point: the entry vanished until reopen."""
    pcs = PlayerCombatStrategy(name="Support", dsl="shipped")
    shipped, user = _two_roots(tmp_path, pcs)
    save_player_strategy(pcs.model_copy(update={"name": "Mine", "dsl": "mine"}), user)
    ws = _reopen(shipped, user)

    restored = ws.revert(StrategyKind.PLAYER, pcs.id)

    assert ws.get(StrategyKind.PLAYER, pcs.id) is restored
    assert (restored.name, restored.dsl) == ("Support", "shipped")
    assert ws.status(StrategyKind.PLAYER, pcs.id) is Origin.SHIPPED
    assert ws.changes().modified == [pcs.id]


def test_saving_a_revert_removes_the_user_copy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Support", dsl="shipped")
    shipped, user = _two_roots(tmp_path, pcs)
    save_player_strategy(pcs.model_copy(update={"dsl": "mine"}), user)
    ws = _reopen(shipped, user)

    ws.revert(StrategyKind.PLAYER, pcs.id)
    ws.commit()

    assert list((user / "strategies" / "player").glob("*.json")) == []
    assert ws.origin(StrategyKind.PLAYER, pcs.id) is Origin.SHIPPED
    assert not ws.is_dirty


def test_reverting_an_unsaved_edit_leaves_the_set_clean(tmp_path: Path):
    """With no user copy on disk, a revert only undoes the edit — there is nothing to write."""
    pcs = PlayerCombatStrategy(name="Support", dsl="shipped")
    ws = _reopen(*_two_roots(tmp_path, pcs))
    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "edited"

    ws.revert(StrategyKind.PLAYER, pcs.id)

    assert ws.get(StrategyKind.PLAYER, pcs.id).dsl == "shipped"
    assert not ws.is_dirty


def test_editing_after_a_revert_saves_an_override_again(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Support", dsl="shipped")
    shipped, user = _two_roots(tmp_path, pcs)
    save_player_strategy(pcs.model_copy(update={"dsl": "mine"}), user)
    ws = _reopen(shipped, user)

    ws.revert(StrategyKind.PLAYER, pcs.id).dsl = "second thoughts"
    ws.commit()

    assert _reopen(shipped, user).get(StrategyKind.PLAYER, pcs.id).dsl == "second thoughts"


def test_editing_an_override_back_to_the_shipped_content_removes_the_copy(tmp_path: Path):
    """However the content got back, a copy identical to the shipped one would only shadow updates."""
    pcs = PlayerCombatStrategy(name="Support", dsl="shipped")
    shipped, user = _two_roots(tmp_path, pcs)
    save_player_strategy(pcs.model_copy(update={"dsl": "mine"}), user)
    ws = _reopen(shipped, user)

    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "shipped"
    ws.commit()

    assert ws.origin(StrategyKind.PLAYER, pcs.id) is Origin.SHIPPED


def test_an_identical_copy_on_disk_still_reads_as_modified(tmp_path: Path):
    """It shadows every future update to the built-in, so Revert has to stay on offer."""
    pcs = PlayerCombatStrategy(name="Support", dsl="shipped")
    shipped, user = _two_roots(tmp_path, pcs)
    save_player_strategy(pcs, user)
    ws = _reopen(shipped, user)

    assert ws.status(StrategyKind.PLAYER, pcs.id) is Origin.SHIPPED_OVERRIDE
    ws.revert(StrategyKind.PLAYER, pcs.id)
    assert ws.is_dirty
    ws.commit()
    assert ws.origin(StrategyKind.PLAYER, pcs.id) is Origin.SHIPPED
