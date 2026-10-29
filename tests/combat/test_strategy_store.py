"""Tests for bartlebot.combat.strategy_store."""

from pathlib import Path

from bartleby.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy

from bartlebot.combat.bot_strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.combat.strategy_store import (
    Origin,
    StrategyKind,
    delete_bot_strategy,
    delete_duel_strategy,
    delete_player_strategy,
    list_bot_strategies,
    list_duel_strategies,
    list_player_strategies,
    load_bot_strategy,
    load_duel_strategy,
    load_player_strategy,
    origin_of,
    rekey_bot_strategy_states,
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
    unique_name,
)
from bartlebot.content import store_roots

# ---------------------------------------------------------------------------
# PlayerCombatStrategy store
# ---------------------------------------------------------------------------


def test_list_player_strategies_empty(tmp_path: Path):
    assert list_player_strategies(tmp_path) == []


def test_save_load_player_strategy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Blade", dsl="any<blade> @ self")
    save_player_strategy(pcs, tmp_path)
    loaded = load_player_strategy(pcs.id, tmp_path)
    assert loaded.id == pcs.id
    assert loaded.name == "Blade"


def test_list_player_strategies(tmp_path: Path):
    pcs1 = PlayerCombatStrategy(name="A", dsl="")
    pcs2 = PlayerCombatStrategy(name="B", dsl="")
    save_player_strategy(pcs1, tmp_path)
    save_player_strategy(pcs2, tmp_path)
    result = list_player_strategies(tmp_path)
    ids = {s.id for s in result}
    assert pcs1.id in ids
    assert pcs2.id in ids


def test_delete_player_strategy(tmp_path: Path):
    pcs = PlayerCombatStrategy(name="Del", dsl="")
    loaded_with = save_player_strategy(pcs, tmp_path)
    assert len(list_player_strategies(tmp_path)) == 1
    delete_player_strategy(pcs.id, tmp_path, expected=loaded_with)
    assert list_player_strategies(tmp_path) == []


def test_delete_nonexistent_player_strategy_is_noop(tmp_path: Path):
    delete_player_strategy("no-such-uuid", tmp_path)  # should not raise


# ---------------------------------------------------------------------------
# DuelCombatStrategy store
# ---------------------------------------------------------------------------


def test_save_load_duel_strategy(tmp_path: Path):
    dcs = DuelCombatStrategy(name="Boss Fight", combat_order=[2, 1])
    save_duel_strategy(dcs, tmp_path)
    loaded = load_duel_strategy(dcs.id, tmp_path)
    assert loaded.id == dcs.id
    assert loaded.combat_order == [2, 1]


def test_list_duel_strategies_empty(tmp_path: Path):
    assert list_duel_strategies(tmp_path) == []


def test_delete_duel_strategy(tmp_path: Path):
    dcs = DuelCombatStrategy(name="D")
    loaded_with = save_duel_strategy(dcs, tmp_path)
    delete_duel_strategy(dcs.id, tmp_path, expected=loaded_with)
    assert list_duel_strategies(tmp_path) == []


# ---------------------------------------------------------------------------
# BotCombatStrategy store
# ---------------------------------------------------------------------------


def test_save_load_bot_strategy(tmp_path: Path):
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-1")
    save_bot_strategy(bcs, tmp_path)
    loaded = load_bot_strategy(bcs.id, tmp_path)
    assert loaded.id == bcs.id
    assert loaded.bot_id == "bot-1"


def test_list_bot_strategies_filters_by_bot_id(tmp_path: Path):
    bcs_a = BotCombatStrategy(name="A", bot_id="bot-1")
    bcs_b = BotCombatStrategy(name="B", bot_id="bot-2")
    save_bot_strategy(bcs_a, tmp_path)
    save_bot_strategy(bcs_b, tmp_path)
    result = list_bot_strategies("bot-1", tmp_path)
    assert len(result) == 1
    assert result[0].id == bcs_a.id


def test_delete_bot_strategy(tmp_path: Path):
    bcs = BotCombatStrategy(name="X", bot_id="b")
    save_bot_strategy(bcs, tmp_path)
    delete_bot_strategy(bcs.id, tmp_path)
    assert list_bot_strategies("b", tmp_path) == []


def test_list_bot_strategies_empty_dir(tmp_path: Path):
    assert list_bot_strategies("any-bot", tmp_path) == []


def test_list_skips_corrupt_file(tmp_path: Path):
    """Corrupt JSON files are silently skipped."""
    d = tmp_path / "bot_strategies"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{not valid json}")
    assert list_bot_strategies("any", tmp_path) == []


# ---------------------------------------------------------------------------
# Default root (Phase 0 regression guard)
# ---------------------------------------------------------------------------


def test_default_root_ignores_cwd(monkeypatch, tmp_path):
    """The bug this store used to have: an explicit root beats cwd, and cwd
    alone must never silently redirect it — that emptied the library for
    anyone who launched the app from outside the project root.
    """
    monkeypatch.setattr(store_roots, "project_root", lambda: tmp_path / "not-the-real-root")
    monkeypatch.chdir(tmp_path)  # must have no effect on the default root
    assert list_player_strategies() == []

    real_default = tmp_path / "not-the-real-root"
    save_player_strategy(PlayerCombatStrategy(name="A", dsl=""), real_default)
    assert len(list_player_strategies()) == 1


def test_explicit_root_overrides_project_root(monkeypatch, tmp_path):
    monkeypatch.setattr(store_roots, "project_root", lambda: tmp_path / "wrong")
    save_player_strategy(PlayerCombatStrategy(name="A", dsl=""), tmp_path / "explicit")
    assert len(list_player_strategies(tmp_path / "explicit")) == 1
    assert list_player_strategies() == []


# ---------------------------------------------------------------------------
# User-owned storage: shipped/user root override (issue #2)
# ---------------------------------------------------------------------------


def test_user_copy_shadows_shipped_on_read(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    original = PlayerCombatStrategy(name="Shipped Blade", dsl="any<blade> @ self")
    save_player_strategy(original, shipped)  # write straight into "shipped" for the test

    edited = original.model_copy(update={"name": "My Blade"})
    save_player_strategy(edited, root=shipped, user_root=user)  # goes to user, same id

    loaded = load_player_strategy(original.id, root=shipped, user_root=user)
    assert loaded.name == "My Blade"

    # Listing sees one entry — the user copy, not both.
    result = list_player_strategies(root=shipped, user_root=user)
    assert len(result) == 1
    assert result[0].name == "My Blade"


def test_write_goes_only_to_user_root(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    dcs = DuelCombatStrategy(name="New")
    save_duel_strategy(dcs, root=shipped, user_root=user)

    assert not (shipped / "strategies" / "duel").exists()
    assert (user / "strategies" / "duel" / "new.json").exists()


def test_copy_on_write_leaves_shipped_file_untouched(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    original = BotCombatStrategy(name="Farm", bot_id="bot-1")
    save_bot_strategy(original, shipped)

    edited = original.model_copy(update={"name": "My Farm"})
    save_bot_strategy(edited, root=shipped, user_root=user)

    still_shipped = load_bot_strategy(original.id, shipped)  # single-root read: shipped only
    assert still_shipped.name == "Farm"

    resolved = load_bot_strategy(original.id, root=shipped, user_root=user)
    assert resolved.name == "My Farm"


def test_delete_reverts_user_copy_to_shipped(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    original = DuelCombatStrategy(name="Shipped")
    save_duel_strategy(original, shipped)

    edited = original.model_copy(update={"name": "Edited"})
    loaded_with = save_duel_strategy(edited, root=shipped, user_root=user)
    assert load_duel_strategy(original.id, root=shipped, user_root=user).name == "Edited"

    delete_duel_strategy(
        original.id, root=shipped, user_root=user, expected=loaded_with
    )  # only the user copy is deleted
    assert load_duel_strategy(original.id, root=shipped, user_root=user).name == "Shipped"


def test_delete_never_touches_shipped_file(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    original = PlayerCombatStrategy(name="Shipped")
    save_player_strategy(original, shipped)

    delete_player_strategy(original.id, root=shipped, user_root=user)  # no user copy exists yet

    assert (shipped / "strategies" / "player" / "shipped.json").exists()


# ---------------------------------------------------------------------------
# unique_name — one naming rule for Duplicate and + New, across all four kinds
# ---------------------------------------------------------------------------


def test_unique_name_returns_the_base_when_it_is_free():
    assert unique_name("Storm AoE", []) == "Storm AoE"
    assert unique_name("Storm AoE", ["Fire AoE"]) == "Storm AoE"


def test_unique_name_starts_at_two():
    assert unique_name("Storm AoE", ["Storm AoE"]) == "Storm AoE (2)"


def test_unique_name_skips_the_gap_it_finds():
    taken = ["Storm AoE", "Storm AoE (2)", "Storm AoE (3)"]
    assert unique_name("Storm AoE", taken) == "Storm AoE (4)"


def test_duplicating_a_duplicate_counts_from_the_stem():
    """ "Storm AoE (2)" duplicated is (3), not "Storm AoE (2) (2)"."""
    assert unique_name("Storm AoE (2)", ["Storm AoE", "Storm AoE (2)"]) == "Storm AoE (3)"


def test_unique_name_is_case_sensitive():
    """These are user-visible labels; two names differing only in case are two names."""
    assert unique_name("Storm AoE", ["storm aoe"]) == "Storm AoE"


def test_unique_name_leaves_a_parenthesised_name_that_is_not_a_suffix_alone():
    assert unique_name("Boss (Hard)", []) == "Boss (Hard)"
    assert unique_name("Boss (Hard)", ["Boss (Hard)"]) == "Boss (Hard) (2)"


# ---------------------------------------------------------------------------
# origin_of — what makes Delete honest
# ---------------------------------------------------------------------------


def test_origin_of_shipped_only(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    dcs = DuelCombatStrategy(name="Shipped")
    save_duel_strategy(dcs, shipped)
    assert origin_of(StrategyKind.DUEL, dcs.id, shipped, user) is Origin.SHIPPED


def test_origin_of_user_copy_over_shipped(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    dcs = DuelCombatStrategy(name="Shipped")
    save_duel_strategy(dcs, shipped)
    save_duel_strategy(dcs.model_copy(update={"name": "Mine"}), root=shipped, user_root=user)
    assert origin_of(StrategyKind.DUEL, dcs.id, shipped, user) is Origin.SHIPPED_OVERRIDE


def test_origin_of_user_only(tmp_path: Path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    pcs = PlayerCombatStrategy(name="Mine")
    save_player_strategy(pcs, root=shipped, user_root=user)
    assert origin_of(StrategyKind.PLAYER, pcs.id, shipped, user) is Origin.USER


def test_origin_of_missing(tmp_path: Path):
    assert origin_of(StrategyKind.DECK, "no-such-uuid", tmp_path) is Origin.MISSING


def test_origin_in_a_single_root_sandbox_is_always_user(tmp_path: Path):
    """Nothing is shipped when both roots are one directory, so nothing is an override."""
    pcs = PlayerCombatStrategy(name="A")
    save_player_strategy(pcs, tmp_path)
    assert origin_of(StrategyKind.PLAYER, pcs.id, tmp_path) is Origin.USER


# ---------------------------------------------------------------------------
# rekey_bot_strategy_states
# ---------------------------------------------------------------------------


def test_rekeying_renames_a_fights_state_and_leaves_its_enemy(tmp_path: Path):
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="bot-1",
        state_assignments=[
            StateAssignment(fights=[FightRef(state="state_9", enemy="Lamia"), FightRef(state="state_2")]),
        ],
    )
    save_bot_strategy(bcs, tmp_path)

    assert rekey_bot_strategy_states("bot-1", {"state_9": "region_1_state_9"}, tmp_path) == 1

    saved = load_bot_strategy(bcs.id, tmp_path)
    assert saved.state_assignments[0].fights == [
        FightRef(state="region_1_state_9", enemy="Lamia"),
        FightRef(state="state_2"),
    ]


def test_rekeying_saves_nothing_when_no_fight_is_renamed(tmp_path: Path):
    bcs = BotCombatStrategy(name="Farm", bot_id="bot-1", state_assignments=[StateAssignment(state_ids=["state_2"])])
    save_bot_strategy(bcs, tmp_path)

    assert rekey_bot_strategy_states("bot-1", {"state_9": "state_3"}, tmp_path) == 0
