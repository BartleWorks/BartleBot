"""The shipped default duels (Stage 3 of #201): their content, ids and shape.

The files under ``strategies/`` are Official content, so what a fight with nothing
tailored runs is decided by what these assert. The default duels are only resolved by
Stage 4; until then the files ship and are found by id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.shared import combat_defaults
from bartlebot.shared.combat_defaults import (
    LAST_RESORT_DSL,
    PREFERENCE_KEYS,
    SHIPPED_DEFAULT_PLAYER_ID,
    SHIPPED_DEFAULT_SUPPORT_ID,
    SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
    SHIPPED_GUARDIAN_SUPPORT_ID,
    SHIPPED_SOLO_DUEL_ID,
    SHIPPED_TEAM_DUEL_ID,
    SHIPPED_TEAM_HITTER_ID,
    DefaultSource,
    TeamSize,
    chosen_default,
    default_duel,
    players_text,
    set_default_choice,
    team_size,
)
from bartlebot.shared.official_set import set_version
from bartlebot.shared.store_files import index_dir, slugify
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy

STRATEGIES = Path(__file__).resolve().parents[2] / "strategies"
DUELS = STRATEGIES / "duel"
PLAYERS = STRATEGIES / "player"
#: The shipped set's version, as ``strategies/official_set.json`` says it.
SHARED_SET_VERSION = set_version(STRATEGIES.parent)

#: The shared set's version before this stage changed shared Official items.
VERSION_BEFORE_DEFAULT_DUELS = 1


def _duel(duel_id: str) -> DuelCombatStrategy:
    return DuelCombatStrategy.load(index_dir(DUELS)[duel_id])


def _player(player_id: str) -> PlayerCombatStrategy:
    return PlayerCombatStrategy.load(index_dir(PLAYERS)[player_id])


def _duel_files() -> list[Path]:
    return sorted(DUELS.glob("*.json"))


def _player_files() -> list[Path]:
    return sorted(PLAYERS.glob("*.json"))


@pytest.mark.parametrize("path", _player_files(), ids=lambda p: p.name)
def test_every_shipped_player_strategy_parses(path: Path):
    strategy = PlayerCombatStrategy.load(path)
    assert validate_dsl(strategy.dsl) is None, strategy.name


@pytest.mark.parametrize("path", _duel_files(), ids=lambda p: p.name)
def test_every_dsl_written_into_a_shipped_duel_parses(path: Path):
    """A duel can carry a player's DSL inline rather than link a player strategy."""
    duel = DuelCombatStrategy.load(path)
    for assignment in duel.player_assignments:
        if assignment.dsl:
            assert validate_dsl(assignment.dsl) is None, (duel.name, assignment.players)


#: The gate on every shipped enchant-ahead slot: a freed hand slot only helps while the
#: main pile has cards to refill it from, and a deck holding one card back gains little.
ENCHANT_AHEAD_GATE = "?(deck.remaining >= 2)"


def _priorities(player_id: str) -> list[str]:
    return [p.strip() for p in _player(player_id).dsl.split(" | ")]


def _enchant_only(dsl: str) -> list[bool]:
    """Which priorities of *dsl* wizsprinter holds rather than runs where they stand."""
    from lark import Lark  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
        get_sprinty_grammar,
    )
    from wizwalker.extensions.wizsprinter.sprinty_combat import is_enchant_only  # noqa: PLC0415

    config = TreeToConfig().transform(Lark(get_sprinty_grammar()).parse(dsl))
    return [is_enchant_only(p) for p in config.infinite_rounds[0].priorities]


@pytest.mark.parametrize("path", _player_files(), ids=lambda p: p.name)
def test_every_shipped_strategy_enchants_ahead_first(path: Path):
    """The slot covers only the casts below it, so it leads."""
    strategy = PlayerCombatStrategy.load(path)
    held = _enchant_only(strategy.dsl)
    assert held[0], strategy.name
    assert strategy.dsl.startswith(ENCHANT_AHEAD_GATE), strategy.name
    assert held.count(True) == 1, strategy.name


def test_the_cheat_boss_supports_never_enchant_the_card_their_cheat_watches():
    """The No-Blades and No-Feints supports exist for bosses that cheat when a blade or a
    feint is cast. Fusing an enchant into one would hold a hand slot they never free."""
    no_blades = _priorities("0c0f0554-df96-440d-837e-21ca2004198e")[0]
    no_feints = _priorities("f0b1a45f-4b97-4b7e-aae8-e334307185ce")[0]

    assert "blade" not in no_blades
    assert "spell(any<trap>)" not in no_feints
    assert "spell(any<trap & !feint>)" in no_feints


def test_default_dsl_equals_the_last_resort():
    assert _player(SHIPPED_DEFAULT_PLAYER_ID).dsl == LAST_RESORT_DSL
    assert _player(SHIPPED_DEFAULT_PLAYER_ID).name == "Default"


def test_the_last_resort_parses():
    assert validate_dsl(LAST_RESORT_DSL) is None


def test_solo_duel_accepts_one_player_only():
    duel = _duel(SHIPPED_SOLO_DUEL_ID)
    assert (duel.min_players, duel.max_players) == (1, 1)
    assert duel.resolve_dsl_for_player(1, {SHIPPED_DEFAULT_PLAYER_ID: "default"}) == "default"


@pytest.mark.parametrize("duel_id", [SHIPPED_TEAM_DUEL_ID, SHIPPED_EVERYONE_ATTACKS_DUEL_ID])
def test_team_duels_accept_two_to_four_players(duel_id: str):
    duel = _duel(duel_id)
    assert (duel.min_players, duel.max_players) == (2, 4)


def test_team_duel_has_p1_hit_and_the_rest_support_casting_p1_last():
    duel = _duel(SHIPPED_TEAM_DUEL_ID)
    pcs = {SHIPPED_TEAM_HITTER_ID: "hit", SHIPPED_DEFAULT_SUPPORT_ID: "support"}
    assert [duel.resolve_dsl_for_player(n, pcs) for n in (1, 2, 3, 4)] == ["hit", "support", "support", "support"]
    assert duel.combat_order == [4, 3, 2, 1]


def test_everyone_attacks_runs_the_team_hitter_on_all_four():
    duel = _duel(SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    pcs = {SHIPPED_TEAM_HITTER_ID: "hit"}
    assert [duel.resolve_dsl_for_player(n, pcs) for n in (1, 2, 3, 4)] == ["hit"] * 4


@pytest.mark.parametrize(
    ("duel_id", "name"),
    [
        (SHIPPED_SOLO_DUEL_ID, "Default Solo Duel"),
        (SHIPPED_TEAM_DUEL_ID, "Default Team Duel"),
        (SHIPPED_EVERYONE_ATTACKS_DUEL_ID, "Default Team Duel (Everyone Attacks)"),
    ],
)
def test_each_shipped_duel_id_resolves_to_its_file_named_after_it(duel_id: str, name: str):
    path = index_dir(DUELS)[duel_id]
    assert path.name == f"{slugify(name)}.json"
    assert DuelCombatStrategy.load(path).name == name


def test_every_link_in_the_default_duels_resolves():
    known = set(index_dir(PLAYERS))
    for duel_id in (SHIPPED_SOLO_DUEL_ID, SHIPPED_TEAM_DUEL_ID, SHIPPED_EVERYONE_ATTACKS_DUEL_ID):
        assert set(_duel(duel_id).linked_pcs_ids()) <= known


@pytest.mark.parametrize("files", [_duel_files(), _player_files()], ids=["duel", "player"])
def test_every_shipped_file_is_named_after_its_item_and_declares_a_unique_id(files: list[Path]):
    ids = [json.loads(p.read_text(encoding="utf-8"))["id"] for p in files]
    assert len(set(ids)) == len(ids)
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert path.name == f"{slugify(data['name'])}.json"


def test_the_ids_are_distinct():
    ids = [v for k, v in vars(combat_defaults).items() if k.startswith("SHIPPED_") and k.endswith("_ID")]
    assert len(ids) == 7
    assert len(set(ids)) == len(ids)


def test_shared_set_version_rose_with_the_default_duels():
    assert SHARED_SET_VERSION > VERSION_BEFORE_DEFAULT_DUELS


def test_shared_set_version_rose_with_enchanting_ahead():
    """Every shipped player strategy but one gained its enchant-ahead slot at version 3."""
    assert SHARED_SET_VERSION >= 3


def test_shared_set_version_rose_with_the_supports():
    """Default Support and the two cheat-boss supports took the support ladder at version 4
    (Stage 3 of #218); what they cast is pinned in ``tests/strategies/test_supports.py``."""
    assert SHARED_SET_VERSION >= 4


def test_shared_set_version_rose_with_the_hitters():
    """Default and the other shipped hitters took the hitter ladder, and Default Team
    Hitter joined the set, at version 5 (Stage 4 of #218); what they cast is pinned in
    ``tests/strategies/test_hitters.py``."""
    assert SHARED_SET_VERSION >= 5


def test_shared_set_version_rose_when_two_duels_left():
    """Default Team Boss Duel and Default Turn 1 AoE Mob Duel left the set at version 6
    (Stage 5 of #218)."""
    assert SHARED_SET_VERSION >= 6


def test_shared_set_version_rose_with_the_guardian():
    """Default Support stopped healing, every shipped support gained a negative aura on the
    boss and every hitter a shield, and Default Guardian Support joined the set, at version
    7 (Stage 6 of #218); what they cast is pinned in ``tests/strategies/``."""
    assert SHARED_SET_VERSION >= 7


def test_shared_set_version_rose_when_turn_1_aoe_left():
    """Default Turn 1 AoE mob fight left the set at version 8 (#212): its duel had gone at
    version 6, and every shipped team strategy now AoEs in round 1 of a mob fight. The same
    version added the No-Blades and No-Feints hitters, and Default Single-target Hitter
    started hitting a mob in a mob fight. The decks first shipped at version 8 too."""
    assert SHARED_SET_VERSION >= 8


@pytest.mark.parametrize("path", _duel_files(), ids=lambda p: p.name)
def test_no_shipped_duel_leaves_a_slot_to_chance(path: Path):
    """A shipped support aims at ``ally(last)``, which is P1 only under a combat order that
    puts P1 in the last slot (#212)."""
    from bartlebot.combat.strategy_summary import slot_order_warning  # noqa: PLC0415

    pcs_dsl = {pcs.id: pcs.dsl for pcs in map(PlayerCombatStrategy.load, _player_files())}

    assert slot_order_warning(DuelCombatStrategy.load(path), pcs_dsl) is None


def test_no_shipped_duel_links_the_guardian():
    """A team rarely needs one, so a user picks it for a slot (#224)."""
    duels = Path(__file__).resolve().parents[2] / "strategies" / "duel"
    for path in duels.glob("*.json"):
        assert SHIPPED_GUARDIAN_SUPPORT_ID not in path.read_text(encoding="utf-8"), path.name


#: Default Team Boss Duel and Default Turn 1 AoE Mob Duel, which left the Official set
#: in Stage 5 of #218: Default Team Duel covers both fight kinds.
RETIRED_DUEL_IDS = ("1bd16a10-285b-4a78-b8d2-cd16433df54e", "7cd5d91b-2d67-4599-bc25-a3169446e258")

#: Every shipped tree a link to a duel could sit in: bots, templates, subgraphs, strategies.
_SHIPPED_TREES = [STRATEGIES.parent / name for name in ("bots", "templates", "subgraphs", "strategies")]


#: Default Turn 1 AoE mob fight, which left the Official set in #212.
RETIRED_PLAYER_IDS = ("1337f552-1404-4411-9c35-eb9b4f18d149",)


def test_a_retired_player_strategy_is_no_longer_shipped():
    assert not set(RETIRED_PLAYER_IDS) & set(index_dir(PLAYERS))


def test_nothing_shipped_links_a_retired_player_strategy():
    linking = [
        str(path.relative_to(STRATEGIES.parent))
        for tree in _SHIPPED_TREES
        for path in tree.rglob("*.json")
        if any(pcs_id in path.read_text(encoding="utf-8") for pcs_id in RETIRED_PLAYER_IDS)
    ]
    assert linking == []


@pytest.mark.parametrize("duel_id", RETIRED_DUEL_IDS)
def test_a_retired_duel_is_no_longer_shipped(duel_id: str):
    assert duel_id not in index_dir(DUELS)


def test_nothing_shipped_links_a_retired_duel():
    """No shipped bot, template, subgraph, Bot Combat Strategy, duel or player strategy
    names either id, so nothing Official is left holding a link to nothing."""
    linking = [
        str(path.relative_to(STRATEGIES.parent))
        for tree in _SHIPPED_TREES
        for path in tree.rglob("*.json")
        if any(duel_id in path.read_text(encoding="utf-8") for duel_id in RETIRED_DUEL_IDS)
    ]
    assert linking == []


def test_every_link_in_every_shipped_duel_resolves():
    """The player strategies the retired duels linked stay, and every duel left links
    only player strategies the set holds."""
    known = set(index_dir(PLAYERS))
    for path in _duel_files():
        assert set(DuelCombatStrategy.load(path).linked_pcs_ids()) <= known, path.name


def test_the_team_hitter_is_default_with_a_round_1_line():
    """Its repeating line is Default's text, word for word, so a fix to one is a fix to
    both; only the round-1 line differs."""
    hitter = _player(SHIPPED_TEAM_HITTER_ID)
    repeating, round_1 = hitter.dsl.splitlines()
    assert hitter.name == "Default Team Hitter"
    assert repeating == LAST_RESORT_DSL
    assert round_1.startswith("{1} ")


def test_a_missing_default_does_not_resolve():
    """The failure path: an id no file declares is not found, never invented."""
    assert "00000000-0000-0000-0000-000000000000" not in index_dir(DUELS)


# ---------------------------------------------------------------------------
# Levels 3 and 4: default_duel (Stage 4)
# ---------------------------------------------------------------------------


def _library(*duels: DuelCombatStrategy):
    by_id = {d.id: d for d in duels}
    return by_id.get


def _prefs(tmp_path: Path):
    from bartlebot.shared.preferences import PreferenceStore  # noqa: PLC0415

    return PreferenceStore(tmp_path)


@pytest.mark.parametrize(("clients", "size"), [(1, TeamSize.SOLO), (2, TeamSize.TEAM), (4, TeamSize.TEAM)])
def test_team_size_is_solo_for_one_client_only(clients, size):
    assert team_size(clients) is size


@pytest.mark.parametrize(("players", "fits"), [(1, False), (2, True), (3, True), (4, False)])
def test_a_duel_accepts_a_team_within_its_range(players, fits):
    assert DuelCombatStrategy(name="D", min_players=2, max_players=3).accepts(players) is fits


def test_players_text_reads_as_a_range():
    assert players_text(DuelCombatStrategy(name="D", min_players=1, max_players=1)) == "1 player"
    assert players_text(DuelCombatStrategy(name="D", min_players=4, max_players=4)) == "4 players"
    assert players_text(DuelCombatStrategy(name="D", min_players=2, max_players=4)) == "2 to 4 players"


def test_no_key_means_the_shipped_duel(tmp_path):
    team = _duel(SHIPPED_TEAM_DUEL_ID)
    answer = default_duel(2, _prefs(tmp_path), _library(team))
    assert (answer.duel, answer.source, answer.reasons) == (team, DefaultSource.SHIPPED, ())


def test_a_stored_id_that_fits_is_yours(tmp_path):
    mine = DuelCombatStrategy(name="Mine", min_players=2)
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)

    answer = default_duel(3, prefs, _library(mine, _duel(SHIPPED_TEAM_DUEL_ID)))

    assert (answer.duel, answer.source) == (mine, DefaultSource.YOURS)


def test_a_stored_id_that_does_not_fit_falls_to_the_shipped_duel_and_says_why(tmp_path):
    mine = DuelCombatStrategy(name="Four Box", min_players=4)
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)
    team = _duel(SHIPPED_TEAM_DUEL_ID)

    answer = default_duel(2, prefs, _library(mine, team))

    assert (answer.duel, answer.source) == (team, DefaultSource.SHIPPED)
    assert answer.reasons == ("your Team default 'Four Box' takes 4 players, and 2 are connected",)


def test_an_ambient_team_fight_falls_to_everyone_attacks(tmp_path):
    """Nothing orders who joins a fight no node started, so no slot is the hitter's."""
    everyone = _duel(SHIPPED_EVERYONE_ATTACKS_DUEL_ID)
    answer = default_duel(3, _prefs(tmp_path), _library(_duel(SHIPPED_TEAM_DUEL_ID), everyone), ambient=True)
    assert (answer.duel, answer.source) == (everyone, DefaultSource.SHIPPED)


def test_an_ambient_solo_fight_keeps_the_solo_duel(tmp_path):
    solo = _duel(SHIPPED_SOLO_DUEL_ID)
    answer = default_duel(1, _prefs(tmp_path), _library(solo), ambient=True)
    assert answer.duel == solo


def test_your_team_default_still_answers_an_ambient_fight(tmp_path):
    """Only the shipped default changes: a duel the user chose is theirs to run."""
    mine = DuelCombatStrategy(name="Mine", min_players=2)
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], mine.id)

    answer = default_duel(2, prefs, _library(mine, _duel(SHIPPED_EVERYONE_ATTACKS_DUEL_ID)), ambient=True)

    assert (answer.duel, answer.source) == (mine, DefaultSource.YOURS)


def test_the_shipped_duel_stored_by_its_id_reads_as_recommended(tmp_path):
    """BartleBuddy or a hand edit can store it; BartleBot's own writes never do."""
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], SHIPPED_TEAM_DUEL_ID)

    assert chosen_default(prefs, TeamSize.TEAM) is None


def test_the_shipped_duel_stored_by_its_id_is_shipped_in_an_ambient_fight(tmp_path):
    """Read as the user's, it would answer at level 3 and skip the ambient swap (epic #201 review, R5)."""
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.TEAM], SHIPPED_TEAM_DUEL_ID)
    everyone = _duel(SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    answer = default_duel(2, prefs, _library(_duel(SHIPPED_TEAM_DUEL_ID), everyone), ambient=True)

    assert (answer.duel, answer.source) == (everyone, DefaultSource.SHIPPED)


def test_nothing_readable_answers_nothing(tmp_path):
    answer = default_duel(1, _prefs(tmp_path), _library())
    assert (answer.duel, answer.source) == (None, None)


def test_default_duel_writes_nothing(tmp_path):
    default_duel(2, _prefs(tmp_path), _library(_duel(SHIPPED_TEAM_DUEL_ID)))
    assert list(tmp_path.iterdir()) == []


def test_a_choice_is_stored_and_read_back(tmp_path):
    prefs = _prefs(tmp_path)

    assert set_default_choice(prefs, TeamSize.TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    assert chosen_default(_prefs(tmp_path), TeamSize.TEAM) == SHIPPED_EVERYONE_ATTACKS_DUEL_ID
    assert chosen_default(prefs, TeamSize.SOLO) is None


@pytest.mark.parametrize("recommended", [None, SHIPPED_TEAM_DUEL_ID])
def test_the_recommended_choice_is_stored_as_no_key(tmp_path, recommended):
    """So a later fix to the shipped duel reaches everyone who kept it (epic #201)."""
    prefs = _prefs(tmp_path)
    prefs.set(PREFERENCE_KEYS[TeamSize.SOLO], "kept")
    set_default_choice(prefs, TeamSize.TEAM, SHIPPED_EVERYONE_ATTACKS_DUEL_ID)

    assert set_default_choice(prefs, TeamSize.TEAM, recommended)

    record = json.loads((tmp_path / "preferences.json").read_text(encoding="utf-8"))
    assert PREFERENCE_KEYS[TeamSize.TEAM] not in record
    assert record[PREFERENCE_KEYS[TeamSize.SOLO]] == "kept"


# ---------------------------------------------------------------------------
# The shipped decks (#212)
# ---------------------------------------------------------------------------


def _deck_files() -> list[Path]:
    return sorted((STRATEGIES / "decks").glob("*.json"))


@pytest.mark.parametrize("path", _deck_files(), ids=lambda p: p.name)
def test_every_shipped_deck_is_named_a_deck_and_says_what_it_is_for(path: Path):
    from bartlebot.shared.strategy_models import DeckPreset  # noqa: PLC0415

    deck = DeckPreset.load(path)

    assert deck.name.startswith("Default ") and deck.name.endswith(" Deck"), deck.name
    assert "Pairs with" in deck.description, deck.name


def test_the_shipped_decks_read_as_official(tmp_path: Path):
    """A deck in the install directory and none in the user's is Official, as a strategy is."""
    from bartlebot.combat.strategy_store import StrategyKind  # noqa: PLC0415
    from bartlebot.combat.strategy_workset import StrategyWorkingSet  # noqa: PLC0415
    from bartlebot.content.store_roots import Origin  # noqa: PLC0415

    ws = StrategyWorkingSet.from_store(root=STRATEGIES.parent, user_root=tmp_path)
    ids = list(index_dir(STRATEGIES / "decks"))

    assert ids
    assert {ws.status(StrategyKind.DECK, deck_id) for deck_id in ids} == {Origin.SHIPPED}
