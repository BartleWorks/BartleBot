"""Tests for bartlebot.combat.combat_resolution: the four levels a fight's duel strategy comes from.

Most tests run against two real roots: a shipped one holding a copy of the repo's
``strategies/`` (so levels 4 and the shipped defaults are there to fall to), plus any
Official Bot Combat Strategy a test ships, and an empty user root. Nothing is mocked
below the store.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.combat import strategy_store
from bartlebot.combat.bot_strategy_models import BotCombatStrategy, StateAssignment
from bartlebot.combat.combat_resolution import (
    CombatResolver,
    Level,
    describe_run,
    official_strategy_for,
    ranked_officials,
    resolve_fight,
)
from bartlebot.combat.combat_warnings import check_combat
from bartlebot.paths import project_root
from bartlebot.shared.combat_defaults import (
    PREFERENCE_KEYS,
    SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
    SHIPPED_SOLO_DUEL_ID,
    SHIPPED_TEAM_DUEL_ID,
    TeamSize,
)
from bartlebot.shared.preferences import PreferenceStore
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerAssignment

_DSL = "any<damage>[any<mod_damage>] @ enemy"


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    """``(shipped, user)``: the repo's shipped strategies, and a user root with nothing in it."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    shutil.copytree(project_root() / "strategies", shipped / "strategies")
    user.mkdir()
    return shipped, user


@pytest.fixture
def prefs(tmp_path: Path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "vendor")


def _bot(*exposed: str) -> BotConfig:
    """A bot whose fights on *exposed* asked for a strategy of their own."""
    return BotConfig(
        id="bot-1",
        name="Bot",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="handle_combat")]
        + [
            StateModel(id=sid, node_type="handle_combat", label=f"Fight {sid}", params={"use_default_strategy": False})
            for sid in exposed
        ],
        transitions=[],
    )


def _duel(roots, name: str = "Duel", *, shipped: bool = False, **kwargs) -> DuelCombatStrategy:
    duel = DuelCombatStrategy(
        name=name, player_assignments=[PlayerAssignment(players=[1, 2, 3, 4], dsl=_DSL)], **kwargs
    )
    root = roots[0] if shipped else roots[1]
    strategy_store.save_duel_strategy(duel, root)
    return duel


def _bcs(roots, name: str = "Mine", *, shipped: bool = False, **kwargs) -> BotCombatStrategy:
    bcs = BotCombatStrategy(name=name, bot_id="bot-1", **kwargs)
    strategy_store.save_bot_strategy(bcs, roots[0] if shipped else roots[1])
    return bcs


def _resolver(roots, prefs, clients: int, pick: str | None = None, bot: BotConfig | None = None) -> CombatResolver:
    return CombatResolver(bot or _bot(), clients, pick, prefs=prefs, root=roots[0], user_root=roots[1])


# ---------------------------------------------------------------------------
# Levels 3 and 4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clients", "duel_id", "size"),
    [(1, SHIPPED_SOLO_DUEL_ID, "Solo"), (2, SHIPPED_TEAM_DUEL_ID, "Team"), (4, SHIPPED_TEAM_DUEL_ID, "Team")],
)
def test_no_key_stored_means_the_shipped_default(roots, prefs, clients, duel_id, size):
    res = _resolver(roots, prefs, clients).fight(None)

    assert res.level is Level.SHIPPED_DEFAULT
    assert res.duel.id == duel_id
    assert res.source_label.endswith(f"(the shipped {size} default; this bot has no Bot Combat Strategy)")


def test_a_stored_id_means_that_duel(roots, prefs):
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    res = _resolver(roots, prefs, 3).fight(None)

    assert res.level is Level.YOUR_DEFAULT
    assert res.duel.id == SHIPPED_EVERYONE_ATTACKS_DUEL_ID
    assert res.source_label == (
        "duel strategy 'Default Team Duel (Everyone Attacks)' (your Team default; this bot has no Bot Combat Strategy)"
    )


def test_the_solo_key_does_not_answer_for_a_team(roots, prefs):
    prefs.set(PREFERENCE_KEYS[TeamSize.SOLO], _duel(roots, "Mine", max_players=1).id)

    assert _resolver(roots, prefs, 2).fight(None).duel.id == SHIPPED_TEAM_DUEL_ID


def test_your_team_default_needing_four_run_with_two_falls_to_the_shipped_team_default(roots, prefs):
    four = _duel(roots, "Four Box", min_players=4)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], four.id)

    res = _resolver(roots, prefs, 2).fight(None)

    assert res.level is Level.SHIPPED_DEFAULT
    assert res.duel.id == SHIPPED_TEAM_DUEL_ID
    assert "your Team default 'Four Box' takes 4 players, and 2 are connected" in res.reasons


def test_a_stored_id_the_library_lacks_falls_to_the_shipped_default(roots, prefs):
    prefs.set(PREFERENCE_KEYS[TeamSize.SOLO], "00000000-0000-0000-0000-000000000000")

    res = _resolver(roots, prefs, 1).fight(None)

    assert res.level is Level.SHIPPED_DEFAULT
    assert "your Solo default is not in the library" in res.reasons


def test_no_readable_default_is_the_last_resort(tmp_path, prefs):
    empty = tmp_path / "empty"
    res = CombatResolver(_bot(), 2, None, prefs=prefs, root=empty, user_root=empty).fight(None)

    assert res.level is Level.LAST_RESORT
    assert res.duel is None
    assert res.source_label.startswith("BartleBot built-in default (no strategy file could be read")


# ---------------------------------------------------------------------------
# Level 1: the user's pick
# ---------------------------------------------------------------------------


def test_the_pick_answers_with_its_assignment(roots, prefs):
    boss = _duel(roots, "Boss")
    bcs = _bcs(roots, state_assignments=[StateAssignment(duel_combat_strategy_id=boss.id, state_ids=["s1"])])

    res = _resolver(roots, prefs, 2, bcs.id, _bot("s1")).fight("s1")

    assert res.level is Level.PICK
    assert res.duel.id == boss.id
    assert res.source_label == "duel strategy 'Boss' (via state assignment for 's1'; your Bot Combat Strategy 'Mine')"


def test_a_bot_combat_strategys_own_default_covers_its_unassigned_fights(roots, prefs):
    general = _duel(roots, "General")
    bcs = _bcs(roots, default_strategy_id=general.id)

    res = _resolver(roots, prefs, 2, bcs.id, _bot("s1")).fight("s1")

    assert res.level is Level.PICK
    assert res.duel.id == general.id
    assert res.unassigned


def test_one_with_no_default_falls_to_level_three_for_its_unassigned_fights(roots, prefs):
    boss = _duel(roots, "Boss")
    bcs = _bcs(roots, state_assignments=[StateAssignment(duel_combat_strategy_id=boss.id, state_ids=["s1"])])
    mine = _duel(roots, "My Team")
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)

    fights = _resolver(roots, prefs, 2, bcs.id, _bot("s1", "s2")).fights()

    assert fights["s1"].level is Level.PICK
    assert fights["s2"].level is Level.YOUR_DEFAULT
    assert fights["s2"].duel.id == mine.id
    assert "'Mine' has no duel strategy for this fight and no default" in fights["s2"].reasons
    assert fights[None].level is Level.YOUR_DEFAULT


def test_the_pick_needing_four_run_with_two_is_used_and_says_so(roots, prefs):
    """Its fault is the pre-run check's to show; at run time the user's choice runs."""
    four = _duel(roots, "Four Box", min_players=4)
    bcs = _bcs(roots, default_strategy_id=four.id)

    res = _resolver(roots, prefs, 2, bcs.id).fight(None)

    assert res.level is Level.PICK
    assert res.duel.id == four.id
    assert "'Four Box' takes 4 players, and 2 are connected" in res.source_label


def test_a_pick_whose_file_is_missing_falls_through(roots, prefs):
    res = _resolver(roots, prefs, 1, "deadbeef-0000-0000-0000-000000000000").fight(None)

    assert res.level is Level.SHIPPED_DEFAULT
    assert "your Bot Combat Strategy's file is missing (id: deadbeef…)" in res.reasons


def test_a_pick_linking_a_missing_duel_falls_through(roots, prefs):
    bcs = _bcs(roots, default_strategy_id="abcdef00-0000-0000-0000-000000000000")

    res = _resolver(roots, prefs, 1, bcs.id).fight(None)

    assert res.level is Level.SHIPPED_DEFAULT
    assert "'Mine' links a duel strategy that is missing (id: abcdef00…)" in res.reasons


# ---------------------------------------------------------------------------
# Level 2: the bot's Official Bot Combat Strategy
# ---------------------------------------------------------------------------


def test_an_official_bot_combat_strategy_is_used_without_a_pick(roots, prefs):
    duel = _duel(roots, "Official Duel", shipped=True)
    official = _bcs(roots, "Author Set", shipped=True, default_strategy_id=duel.id)

    res = _resolver(roots, prefs, 2).fight(None)

    assert res.level is Level.OFFICIAL
    assert res.owner.id == official.id
    assert res.source_label == (
        "duel strategy 'Official Duel' (via default strategy; Official Bot Combat Strategy 'Author Set'; "
        "this bot has no Bot Combat Strategy)"
    )


def test_the_pick_comes_before_the_official(roots, prefs):
    official_duel = _duel(roots, "Official Duel", shipped=True)
    _bcs(roots, "Author Set", shipped=True, default_strategy_id=official_duel.id)
    mine = _bcs(roots, default_strategy_id=_duel(roots, "Mine Duel").id)

    assert _resolver(roots, prefs, 2, mine.id).fight(None).level is Level.PICK


def test_a_pick_with_no_default_falls_past_the_official_to_level_three(roots, prefs):
    """Its gaps are not filled from the author's strategy: one owner per run."""
    _bcs(roots, "Author Set", shipped=True, default_strategy_id=_duel(roots, "Official Duel", shipped=True).id)
    boss = _duel(roots, "Boss")
    mine = _bcs(roots, state_assignments=[StateAssignment(duel_combat_strategy_id=boss.id, state_ids=["s1"])])
    team = _duel(roots, "My Team")
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], team.id)

    fights = _resolver(roots, prefs, 2, mine.id, _bot("s1", "s2")).fights()

    assert fights["s1"].level is Level.PICK
    assert fights["s2"].level is Level.YOUR_DEFAULT
    assert fights[None].level is Level.YOUR_DEFAULT


def test_a_pick_linking_a_missing_duel_falls_past_the_official(roots, prefs):
    _bcs(roots, "Author Set", shipped=True, default_strategy_id=_duel(roots, "Official Duel", shipped=True).id)
    mine = _bcs(roots, default_strategy_id="abcdef00-0000-0000-0000-000000000000")

    assert _resolver(roots, prefs, 2, mine.id).fight(None).level is Level.SHIPPED_DEFAULT


def test_a_pick_whose_file_is_missing_falls_to_the_official(roots, prefs):
    """No Bot Combat Strategy loaded, so there is nothing of the user's to mix with."""
    _bcs(roots, "Author Set", shipped=True, default_strategy_id=_duel(roots, "Official Duel", shipped=True).id)

    res = _resolver(roots, prefs, 2, "deadbeef-0000-0000-0000-000000000000").fight(None)

    assert res.level is Level.OFFICIAL


def test_an_official_duel_needing_four_run_with_two_falls_to_level_three_and_says_why(roots, prefs):
    four = _duel(roots, "Four Box", shipped=True, min_players=4)
    fits = _duel(roots, "Any", shipped=True)
    # Its default fits two, so it ranks; its fight on s1 needs four.
    _bcs(
        roots,
        "Author Set",
        shipped=True,
        default_strategy_id=fits.id,
        state_assignments=[StateAssignment(duel_combat_strategy_id=four.id, state_ids=["s1"])],
    )
    mine = _duel(roots, "My Team")
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)

    res = _resolver(roots, prefs, 2, bot=_bot("s1")).fight("s1")

    assert res.level is Level.YOUR_DEFAULT
    assert res.duel.id == mine.id
    assert "Official 'Four Box' takes 4 players, and 2 are connected" in res.source_label


def test_the_official_with_the_most_fitting_fights_wins(roots):
    solo = _duel(roots, "Solo", shipped=True, max_players=1)
    team = _duel(roots, "Team", shipped=True, min_players=2)
    solo_bcs = _bcs(roots, "Solo Set", shipped=True, default_strategy_id=solo.id)
    team_bcs = _bcs(roots, "Team Set", shipped=True, default_strategy_id=team.id)
    bot = _bot()

    assert official_strategy_for(bot, 1, root=roots[0], user_root=roots[1]).id == solo_bcs.id
    assert official_strategy_for(bot, 3, root=roots[0], user_root=roots[1]).id == team_bcs.id


def test_a_users_copy_of_an_official_is_not_the_official(roots, prefs):
    """A user copy shadowing a shipped id is the user's; level 2 reads it as it ships."""
    duel = _duel(roots, "Shipped", shipped=True)
    official = _bcs(roots, "Author Set", shipped=True, default_strategy_id=duel.id)
    strategy_store.save_bot_strategy(official.model_copy(update={"default_strategy_id": None}), roots[1])

    assert _resolver(roots, prefs, 2).fight(None).duel.id == duel.id


def test_ranking_finds_a_tie(roots):
    """The rule the shipped-content test below holds every shipped bot to."""
    duel = _duel(roots, "Any", shipped=True)
    _bcs(roots, "One", shipped=True, default_strategy_id=duel.id)
    _bcs(roots, "Two", shipped=True, default_strategy_id=duel.id)

    ranked = ranked_officials(_bot(), 2, root=roots[0], user_root=roots[1])

    assert [score for score, _ in ranked] == [1, 1]
    assert _tied(ranked)


def _tied(ranked) -> bool:
    return len(ranked) > 1 and ranked[0][0] == ranked[1][0]


def _shipped_bots() -> list[BotConfig]:
    return [BotConfig.load(path) for path in sorted((project_root() / "bots").rglob("*.json"))]


def test_no_two_shipped_bot_combat_strategies_for_one_bot_tie():
    """Level 2 never needs a tie-break. Holds vacuously until Official Bot Combat Strategies ship."""
    shipped = strategy_store.list_shipped(strategy_store.StrategyKind.BOT)
    owners = {bcs.bot_id for bcs in shipped}
    for bot in (b for b in _shipped_bots() if b.id in owners):
        candidates = [bcs for bcs in shipped if bcs.bot_id == bot.id]
        for clients in range(1, 5):
            ranked = ranked_officials(bot, clients, candidates)
            assert not _tied(ranked), f"{bot.name}: {[b.name for _, b in ranked[:2]]} tie with {clients} clients"


# ---------------------------------------------------------------------------
# A fresh install
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clients", [1, 2, 4])
def test_on_a_fresh_install_every_shipped_bot_starts_with_no_fault_on_the_shipped_default(clients):
    expected = SHIPPED_SOLO_DUEL_ID if clients == 1 else SHIPPED_TEAM_DUEL_ID
    bots = _shipped_bots()
    assert bots, "no shipped bot, so this checks nothing"
    for bot in bots:
        check = check_combat(None, clients, bot)
        assert check.faults == [], f"{bot.name}: {check.faults}"
        for fight, res in CombatResolver(bot, clients, None).fights().items():
            assert res.level is Level.SHIPPED_DEFAULT, f"{bot.name} {fight}: {res.source_label}"
            assert res.duel.id == expected


# ---------------------------------------------------------------------------
# The label, the run line, and writing nothing
# ---------------------------------------------------------------------------


def test_the_run_line_names_one_source_for_a_bot_with_none_tailored(roots, prefs):
    line = describe_run(_resolver(roots, prefs, 2, bot=_bot("s1", "s2")))

    assert line == (
        "Combat: duel strategy 'Default Team Duel' (the shipped Team default; this bot has no Bot Combat Strategy) "
        "for every fight"
    )


def test_the_run_line_names_each_source(roots, prefs):
    boss = _duel(roots, "Boss")
    bcs = _bcs(roots, state_assignments=[StateAssignment(duel_combat_strategy_id=boss.id, state_ids=["s1"])])

    line = describe_run(_resolver(roots, prefs, 1, bcs.id, _bot("s1")))

    assert line == (
        "Combat: duel strategy 'Default Solo Duel' (the shipped Solo default; 'Mine' has no duel strategy for this "
        "fight and no default) for the other fights; duel strategy 'Boss' (your Bot Combat Strategy 'Mine') "
        "for 'Fight s1'"
    )


def _snapshot(*dirs: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for d in dirs if d.exists() for p in d.rglob("*") if p.is_file()}


def test_resolution_writes_no_file(roots, prefs, tmp_path):
    official_duel = _duel(roots, "Official Duel", shipped=True)
    _bcs(roots, "Author Set", shipped=True, default_strategy_id=official_duel.id)
    mine = _bcs(roots, default_strategy_id=_duel(roots, "Mine Duel", min_players=4).id)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], _duel(roots, "My Team").id)
    before = _snapshot(tmp_path)

    for pick in (None, mine.id, "deadbeef-0000-0000-0000-000000000000"):
        for clients in (1, 2, 4):
            _resolver(roots, prefs, clients, pick, _bot("s1")).fights()
            check_combat(pick, clients, _bot("s1"), root=roots[0], user_root=roots[1], prefs=prefs)

    assert _snapshot(tmp_path) == before


def test_resolve_fight_is_one_fight_of_a_resolver(roots, prefs):
    res = resolve_fight(_bot(), None, 2, None, prefs=prefs, root=roots[0], user_root=roots[1])
    assert res.level is Level.SHIPPED_DEFAULT
