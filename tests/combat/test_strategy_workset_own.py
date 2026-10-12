"""Tests for the active Bot Combat Strategy inside the working set (#118).

Qt-free, like ``test_strategy_workset.py`` beside it: its own strategies sit
inside the same Save/Cancel boundary as the library, and every rule about them — where
New lands, what a move takes with it, the order Save writes in — is checkable without a
widget.
"""

from pathlib import Path

import bartlebot.combat.strategy_workset as workset_mod
from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.strategy_store import (
    Origin,
    StrategyKind,
    list_all_bot_strategies,
    list_duel_strategies,
    list_player_strategies,
    load_bot_strategy,
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.combat.strategy_workset import SharedUse, StrategyWorkingSet
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy


def _bcs(**kw) -> BotCombatStrategy:
    return BotCombatStrategy(name=kw.pop("name", "Solo"), bot_id=kw.pop("bot_id", "bot-1"), **kw)


def _own_ws(tmp_path: Path, bcs: BotCombatStrategy | None, **kw) -> StrategyWorkingSet:
    if bcs is not None:
        save_bot_strategy(bcs, tmp_path)
    for d in kw.get("duels", []):
        save_duel_strategy(d, tmp_path)
    for p in kw.get("players", []):
        save_player_strategy(p, tmp_path)
    return StrategyWorkingSet.from_store(
        root=tmp_path, bot_strategy_id=bcs.id if bcs else None, bot_id=kw.get("bot_id", "bot-1")
    )


def _linking(name: str, *pcs: PlayerCombatStrategy) -> DuelCombatStrategy:
    return DuelCombatStrategy(
        name=name,
        player_assignments=[
            PlayerAssignment(players=[i + 1], player_combat_strategy_id=p.id) for i, p in enumerate(pcs)
        ],
    )


# ---------------------------------------------------------------------------
# Held, edited, written
# ---------------------------------------------------------------------------


def test_the_active_bot_strategy_is_held_as_an_editable_copy(tmp_path: Path):
    own = DuelCombatStrategy(name="Own")
    bcs = _bcs(duel_strategies=[own])
    ws = _own_ws(tmp_path, bcs)

    ws.get(StrategyKind.DUEL, own.id).name = "Edited"

    assert bcs.duel_strategies[0].name == "Own"
    assert load_bot_strategy(bcs.id, tmp_path).duel_strategies[0].name == "Own"
    assert ws.is_dirty
    ws.commit()
    assert load_bot_strategy(bcs.id, tmp_path).duel_strategies[0].name == "Edited"
    assert not ws.is_dirty


def test_get_looks_in_the_bot_strategy_before_the_library(tmp_path: Path):
    library = DuelCombatStrategy(name="Library")
    own = library.model_copy(update={"name": "Own"})
    ws = _own_ws(tmp_path, _bcs(duel_strategies=[own]), duels=[library])

    assert ws.get(StrategyKind.DUEL, library.id).name == "Own"
    assert ws.is_own_entry(StrategyKind.DUEL, library.id)


def test_new_in_the_bot_strategy_lands_in_the_bot_strategy_file_not_the_library(tmp_path: Path):
    bcs = _bcs()
    ws = _own_ws(tmp_path, bcs)

    made = ws.create(StrategyKind.DUEL, "New Duel Strategy", in_bot_strategy=True)
    ws.commit()

    assert [d.id for d in load_bot_strategy(bcs.id, tmp_path).duel_strategies] == [made.id]
    assert list_duel_strategies(tmp_path) == []
    assert ws.status(StrategyKind.DUEL, made.id) is Origin.USER


def test_duplicating_a_library_entry_into_the_bot_strategy_leaves_the_library_alone(tmp_path: Path):
    library = PlayerCombatStrategy(name="Blade", dsl="any<blade>")
    ws = _own_ws(tmp_path, _bcs(), players=[library])

    copy = ws.duplicate(StrategyKind.PLAYER, library.id, in_bot_strategy=True)

    assert copy.id != library.id
    assert copy.dsl == "any<blade>"
    assert ws.is_own_entry(StrategyKind.PLAYER, copy.id)
    assert [p.id for p in ws.players] == [library.id]
    assert ws.changes().created == [copy.id]


def test_a_name_is_made_unique_across_the_bot_strategy_and_the_library(tmp_path: Path):
    ws = _own_ws(tmp_path, _bcs(duel_strategies=[DuelCombatStrategy(name="Boss")]))

    assert ws.create(StrategyKind.DUEL, "Boss").name == "Boss (2)"


def test_removing_an_own_entry_is_a_plain_delete(tmp_path: Path):
    own = PlayerCombatStrategy(name="Own")
    bcs = _bcs(player_strategies=[own])
    ws = _own_ws(tmp_path, bcs)

    ws.remove(StrategyKind.PLAYER, own.id)

    assert ws.changes().deleted == [own.id]
    ws.commit()
    assert load_bot_strategy(bcs.id, tmp_path).player_strategies == []


def test_was_created_means_made_on_this_visit(tmp_path: Path):
    old = DuelCombatStrategy(name="Old")
    ws = _own_ws(tmp_path, _bcs(duel_strategies=[old]))
    new = ws.create(StrategyKind.DUEL, "New", in_bot_strategy=True)

    assert ws.was_created(StrategyKind.DUEL, new.id)
    assert not ws.was_created(StrategyKind.DUEL, old.id)


# ---------------------------------------------------------------------------
# A bot with no Bot Combat Strategy yet
# ---------------------------------------------------------------------------


def test_a_bot_with_no_bot_strategy_can_have_one_made_and_cancel_discards_it(tmp_path: Path):
    ws = _own_ws(tmp_path, None, bot_id="bot-1")
    assert ws.bot_strategy is None and ws.bot_id == "bot-1"

    made = ws.create_bot_strategy("Solo")

    assert ws.bot_strategy_created and ws.is_dirty
    assert made.bot_id == "bot-1"
    del ws  # Cancel
    assert list_all_bot_strategies(tmp_path) == []


def test_a_bot_strategy_made_in_the_dialog_is_written_on_save(tmp_path: Path):
    ws = _own_ws(tmp_path, None, bot_id="bot-1")
    made = ws.create_bot_strategy("Solo")
    ws.create(StrategyKind.DUEL, "Own", in_bot_strategy=True)

    ws.commit()

    assert [d.name for d in load_bot_strategy(made.id, tmp_path).duel_strategies] == ["Own"]
    assert not ws.bot_strategy_created


# ---------------------------------------------------------------------------
# Add to library
# ---------------------------------------------------------------------------


def _move_fixture(tmp_path: Path):
    frost = PlayerCombatStrategy(name="Frost Heal")
    blade = PlayerCombatStrategy(name="Blade Stack")
    library_pcs = PlayerCombatStrategy(name="Library P")
    boss = _linking("Boss Burn", frost, blade, library_pcs)
    bcs = _bcs(default_strategy_id=boss.id, duel_strategies=[boss], player_strategies=[frost, blade])
    ws = _own_ws(tmp_path, bcs, players=[library_pcs])
    return ws, bcs, boss, frost, blade


def test_moving_a_duel_to_the_library_takes_its_own_player_strategies(tmp_path: Path):
    ws, _bcs, boss, frost, blade = _move_fixture(tmp_path)

    moved = ws.move_to_library(StrategyKind.DUEL, boss.id)

    assert [m.id for m in moved] == [boss.id, frost.id, blade.id]
    assert ws.own_items(StrategyKind.DUEL) == []
    assert ws.own_items(StrategyKind.PLAYER) == []
    assert ws.get(StrategyKind.DUEL, boss.id) is not None
    assert not ws.is_own_entry(StrategyKind.DUEL, boss.id)


def test_a_moved_entry_keeps_its_id_so_every_link_still_resolves(tmp_path: Path):
    ws, bcs, boss, frost, _blade = _move_fixture(tmp_path)
    ws.move_to_library(StrategyKind.DUEL, boss.id)

    ws.commit()

    saved = load_bot_strategy(bcs.id, tmp_path)
    assert saved.default_strategy_id == boss.id
    assert saved.duel_strategies == []
    assert saved.player_strategies == []
    assert {d.id for d in list_duel_strategies(tmp_path)} == {boss.id}
    assert frost.id in {p.id for p in list_player_strategies(tmp_path)}


def test_a_move_counts_once_as_a_change(tmp_path: Path):
    ws, _bcs, boss, frost, blade = _move_fixture(tmp_path)

    ws.move_to_library(StrategyKind.DUEL, boss.id)

    changes = ws.changes()
    assert sorted(changes.created) == sorted([boss.id, frost.id, blade.id])
    assert changes.deleted == []


def test_cancelling_a_move_leaves_disk_as_it_was(tmp_path: Path):
    ws, bcs, boss, _frost, _blade = _move_fixture(tmp_path)

    ws.move_to_library(StrategyKind.DUEL, boss.id)
    del ws  # Cancel

    assert list_duel_strategies(tmp_path) == []
    assert [d.id for d in load_bot_strategy(bcs.id, tmp_path).duel_strategies] == [boss.id]


def test_commit_writes_the_library_before_the_bot_strategy(tmp_path: Path, monkeypatch):
    """Interrupted, a move leaves two copies, never none — save_by_id's rule for a rename."""
    ws, _bcs, boss, _frost, _blade = _move_fixture(tmp_path)
    ws.move_to_library(StrategyKind.DUEL, boss.id)
    order: list[str] = []
    real_duel, real_bot = workset_mod._SAVERS[StrategyKind.DUEL], workset_mod.save_bot_strategy

    def _save_duel(s, **kw):
        order.append("duel")
        real_duel(s, **kw)

    def _save_bot_strategy(s, **kw):
        order.append("bot")
        real_bot(s, **kw)

    monkeypatch.setitem(workset_mod._SAVERS, StrategyKind.DUEL, _save_duel)
    monkeypatch.setattr(workset_mod, "save_bot_strategy", _save_bot_strategy)

    ws.commit()

    assert order == ["duel", "bot"]


def test_a_failed_library_write_holds_the_bot_strategy_back(tmp_path: Path, monkeypatch):
    ws, bcs, boss, _frost, _blade = _move_fixture(tmp_path)
    ws.move_to_library(StrategyKind.DUEL, boss.id)

    def _fail(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setitem(workset_mod._SAVERS, StrategyKind.DUEL, _fail)

    ws.commit()

    # The Bot Combat Strategy still holds the duel, so there is one copy of it on disk.
    assert [d.id for d in load_bot_strategy(bcs.id, tmp_path).duel_strategies] == [boss.id]
    assert ws.is_dirty


# ---------------------------------------------------------------------------
# What an assignment may link
# ---------------------------------------------------------------------------


def test_an_own_duel_may_link_own_and_library_player_strategies(tmp_path: Path):
    own_pcs = PlayerCombatStrategy(name="Own")
    library_pcs = PlayerCombatStrategy(name="Library")
    own = DuelCombatStrategy(name="Own duel")
    ws = _own_ws(tmp_path, _bcs(duel_strategies=[own], player_strategies=[own_pcs]), players=[library_pcs])

    assert [p.name for p in ws.players_for_duel(own.id)] == ["Own", "Library"]


def test_a_library_duel_may_link_library_player_strategies_only(tmp_path: Path):
    own_pcs = PlayerCombatStrategy(name="Own")
    library_pcs = PlayerCombatStrategy(name="Library")
    library_duel = DuelCombatStrategy(name="Library duel")
    ws = _own_ws(tmp_path, _bcs(player_strategies=[own_pcs]), duels=[library_duel], players=[library_pcs])

    assert [p.name for p in ws.players_for_duel(library_duel.id)] == ["Library"]


def test_usage_counts_the_live_bot_strategy(tmp_path: Path):
    library_pcs = PlayerCombatStrategy(name="Blade")
    ws = _own_ws(tmp_path, _bcs(), players=[library_pcs])

    own = ws.create(StrategyKind.DUEL, "Own", in_bot_strategy=True)
    own.player_assignments.append(PlayerAssignment(players=[1], player_combat_strategy_id=library_pcs.id))

    assert ws.usage(library_pcs.id).total == 1


# ---------------------------------------------------------------------------
# The Save confirmation
# ---------------------------------------------------------------------------


def test_shared_uses_list_other_bot_strategies_and_library_duels_never_the_active_one(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Frost Heal")
    duel = _linking("Boss Burn", pcs)
    active = _bcs(name="Mine", default_strategy_id=duel.id)
    save_bot_strategy(_bcs(name="Team", bot_id="bot-2", default_strategy_id=duel.id), tmp_path)
    ws = _own_ws(tmp_path, active, duels=[duel], players=[pcs])

    ws.get(StrategyKind.DUEL, duel.id).description = "edited"
    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "edited"

    uses = {u.name: u for u in ws.shared_uses({"bot-2": "Darkmoor"}.get)}
    assert uses["Boss Burn"].bot_strategies == ("'Team' (Darkmoor)",)
    assert uses["Frost Heal"].duels == ("'Boss Burn'",)
    assert all("Mine" not in p for u in uses.values() for p in u.bot_strategies)


def test_another_bots_own_duel_counts_as_a_use_of_a_player_strategy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Frost Heal")
    save_bot_strategy(_bcs(name="Team", duel_strategies=[_linking("Theirs", pcs)]), tmp_path)
    ws = _own_ws(tmp_path, _bcs(name="Mine"), players=[pcs])

    ws.get(StrategyKind.PLAYER, pcs.id).dsl = "edited"

    assert [u.bot_strategies for u in ws.shared_uses()] == [("'Team'",)]


def test_shared_uses_ignore_an_entry_only_the_active_bot_strategy_uses(tmp_path: Path):
    duel = DuelCombatStrategy(name="Boss Burn")
    ws = _own_ws(tmp_path, _bcs(default_strategy_id=duel.id), duels=[duel])

    ws.get(StrategyKind.DUEL, duel.id).description = "edited"

    assert ws.shared_uses() == []


def test_shared_uses_ignore_an_unchanged_entry(tmp_path: Path):
    duel = DuelCombatStrategy(name="Boss Burn")
    save_bot_strategy(_bcs(name="Team", bot_id="bot-2", default_strategy_id=duel.id), tmp_path)
    ws = _own_ws(tmp_path, _bcs(), duels=[duel])

    assert ws.shared_uses() == []


def test_in_the_library_view_every_bot_strategy_counts(tmp_path: Path):
    duel = DuelCombatStrategy(name="Boss Burn")
    save_bot_strategy(_bcs(name="Solo", default_strategy_id=duel.id), tmp_path)
    ws = _own_ws(tmp_path, None, duels=[duel], bot_id=None)

    ws.get(StrategyKind.DUEL, duel.id).description = "edited"

    assert [u.bot_strategies for u in ws.shared_uses()] == [("'Solo'",)]


def test_the_shared_use_sentence_reads_as_the_plan_has_it():
    use = SharedUse("Boss Burn", bot_strategies=("'Solo' (Atlantea)", "'Team' (Darkmoor)"))

    assert use.sentence() == (
        "'Boss Burn' is also used by 2 other Bot Combat Strategies: 'Solo' (Atlantea) and 'Team' (Darkmoor)."
    )
    assert use.sentence(other=False) == (
        "'Boss Burn' is used by 2 Bot Combat Strategies: 'Solo' (Atlantea) and 'Team' (Darkmoor)."
    )
    one = SharedUse("Boss Burn", bot_strategies=("'Solo' (Atlantea)",))
    assert one.sentence() == "'Boss Burn' is also used by 1 other Bot Combat Strategy: 'Solo' (Atlantea)."
