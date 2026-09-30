"""Tests for bartlebot.strategy_summary.

Pure functions over strategy models — no QApplication, same as
combat_warnings and combat_grouping next door.  Covers each summarize_*
over an empty config, a full one, a linked one, a missing-reference one,
and a broken-DSL one, plus the school counting that spans a multi-player
assignment and a linked preset.
"""

from __future__ import annotations

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    DuelConfig,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.strategy_summary import (
    Health,
    HealthStatus,
    UsageCount,
    find_references,
    format_players,
    summarize_bot,
    summarize_deck,
    summarize_duel,
    summarize_player,
)

_VALID_DSL = "any<blade>[sharp]@ally(2)"
_BROKEN_DSL = "@@@ not a dsl"


# ---------------------------------------------------------------------------
# format_players
# ---------------------------------------------------------------------------


def test_format_players_single():
    assert format_players([2]) == "P2"


def test_format_players_contiguous_range():
    assert format_players([1, 2, 3]) == "P1–P3"


def test_format_players_non_contiguous():
    assert format_players([1, 3]) == "P1, P3"


def test_format_players_empty():
    assert format_players([]) == "no players"


# ---------------------------------------------------------------------------
# summarize_duel
# ---------------------------------------------------------------------------


def test_summarize_empty_duel_config_is_empty_health():
    summary = summarize_duel(DuelConfig(), [])

    assert summary.health.status is HealthStatus.EMPTY
    assert summary.lines == []
    assert summary.facets == ["Any players", "Simultaneous"]


def test_summarize_duel_with_inline_assignments():
    config = DuelConfig(
        min_players=2,
        max_players=2,
        combat_order=[1, 2],
        description="Boss fight — P1 tanks, P2 heals",
        player_assignments=[
            PlayerAssignment(players=[1], dsl=_VALID_DSL, schools=["Storm"]),
            PlayerAssignment(players=[2], dsl="", schools=["Life"]),
        ],
    )

    summary = summarize_duel(config, [])

    assert summary.description == "Boss fight — P1 tanks, P2 heals"
    assert summary.facets == ["2 players", "P2 ← P1"]
    assert summary.schools == {"Storm": 1, "Life": 1}
    assert summary.health.status is HealthStatus.OK
    assert [line.players for line in summary.lines] == ["P1", "P2"]
    assert summary.lines[0].label == _VALID_DSL
    assert summary.lines[1].label == "(default)"


def test_summarize_duel_school_requirement_covers_a_multi_player_assignment():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1, 2], dsl=_VALID_DSL, schools=["Storm"])])

    assert summarize_duel(config, []).schools == {"Storm": 2}


def test_summarize_duel_resolves_school_through_a_linked_preset():
    pcs = PlayerCombatStrategy(name="Storm DPS", dsl=_VALID_DSL, schools=["Storm"])
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)])

    summary = summarize_duel(config, [pcs])

    assert summary.schools == {"Storm": 1}
    assert summary.lines[0].label == "Storm DPS"
    assert summary.lines[0].is_linked is True


def test_summarize_duel_linked_assignment_with_missing_preset():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id="missing-id")])

    summary = summarize_duel(config, [])

    assert summary.lines[0].label == "(unknown)"
    # A missing preset resolves no DSL, which validate_dsl treats as empty/valid.
    assert summary.health.status is HealthStatus.OK


def test_summarize_duel_flags_a_broken_dsl():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN_DSL)])

    summary = summarize_duel(config, [])

    assert summary.health.status is HealthStatus.INVALID_DSL
    assert summary.health.reason


def test_summarize_duel_skips_an_assignment_with_no_players():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[], dsl=_VALID_DSL)])

    assert summarize_duel(config, []).lines == []


def test_summarize_duel_takes_an_explicit_name():
    assert summarize_duel(DuelConfig(), [], name="Frenzy AoE").name == "Frenzy AoE"


# ---------------------------------------------------------------------------
# summarize_player
# ---------------------------------------------------------------------------


def test_summarize_empty_player_strategy():
    summary = summarize_player(PlayerCombatStrategy(name="Blank"))

    assert summary.health.status is HealthStatus.EMPTY
    assert summary.lines[0].label == "(empty DSL)"
    assert summary.facets == []


def test_summarize_player_strategy_with_school_and_dsl():
    pcs = PlayerCombatStrategy(name="Storm DPS", dsl=_VALID_DSL, schools=["Storm"], description="Blade + hit")

    summary = summarize_player(pcs)

    assert summary.facets == ["Storm"]
    assert summary.description == "Blade + hit"
    assert summary.lines[0].label == _VALID_DSL
    assert summary.health.status is HealthStatus.OK


def test_summarize_player_strategy_flags_broken_dsl():
    summary = summarize_player(PlayerCombatStrategy(name="Broken", dsl=_BROKEN_DSL))

    assert summary.health.status is HealthStatus.INVALID_DSL


# ---------------------------------------------------------------------------
# summarize_bot
# ---------------------------------------------------------------------------


def test_summarize_bot_with_no_overrides():
    bcs = BotCombatStrategy(name="Test", bot_id="bot-1")

    summary = summarize_bot(bcs)

    assert summary.facets == ["Default strategy only"]
    assert summary.health.status is HealthStatus.EMPTY


def test_summarize_bot_with_a_default_is_not_empty():
    bcs = BotCombatStrategy(name="Test", bot_id="bot-1", default_strategy_id="dcs-1")

    assert summarize_bot(bcs).health.status is HealthStatus.OK


def test_summarize_bot_counts_state_overrides():
    bcs = BotCombatStrategy(
        name="Test",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=["s1", "s2"]), StateAssignment(state_ids=["s3"])],
    )

    assert summarize_bot(bcs).facets == ["3 state overrides"]


# ---------------------------------------------------------------------------
# summarize_deck
# ---------------------------------------------------------------------------


def test_summarize_empty_deck_preset():
    summary = summarize_deck(DeckPreset(name="Blank"))

    assert summary.facets == ["empty"]
    assert summary.health.status is HealthStatus.EMPTY


def test_summarize_deck_preset_with_cards():
    preset = DeckPreset(name="Fire Deck", deck=DeckData(normal={"Fire Cat": 4, "Sunbird": 1}))

    summary = summarize_deck(preset)

    assert summary.facets == ["5 cards"]
    assert summary.health.status is HealthStatus.OK


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_is_ok_true_only_when_not_invalid():
    assert Health(HealthStatus.OK).is_ok
    assert Health(HealthStatus.EMPTY).is_ok
    assert not Health(HealthStatus.INVALID_DSL, "boom").is_ok


# ---------------------------------------------------------------------------
# find_references — what breaks if this is removed
# ---------------------------------------------------------------------------


def test_find_references_counts_a_bot_default():
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(name="Farm", bot_id="b", default_strategy_id=dcs.id)
    assert find_references(dcs.id, [bcs], []) == UsageCount(bots=1, names=("profile 'Farm'",))


def test_find_references_counts_a_state_override():
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="b",
        state_assignments=[StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)],
    )
    assert find_references(dcs.id, [bcs], []).bots == 1


def test_a_bot_naming_the_same_strategy_twice_counts_once():
    """The user is being told how many things change, not how many fields do."""
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="b",
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)],
    )
    assert find_references(dcs.id, [bcs], []).bots == 1


def test_find_references_counts_a_player_strategy_in_a_duel():
    pcs = PlayerCombatStrategy(name="Blade")
    dcs = DuelCombatStrategy(
        name="Boss",
        player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)],
    )
    assert find_references(pcs.id, [], [dcs]) == UsageCount(duels=1, names=("'Boss — P1'",))


def test_find_references_of_something_unused():
    assert find_references("orphan-id", [], []) == UsageCount()


def test_usage_phrase_reads_as_a_sentence():
    assert UsageCount().phrase() == "Not used yet"
    assert UsageCount(bots=1).phrase() == "Used by 1 bot strategy"
    assert UsageCount(duels=2).phrase() == "Used by 2 duel strategies"
    assert UsageCount(bots=3, duels=1).phrase() == "Used by 3 bot strategies and 1 duel strategy"


def test_an_assignment_line_carries_its_slots_as_numbers():
    """A range like P1–P3 cannot wear three accounts' colors, so the members travel too."""
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[3, 1, 2], dsl="x")])

    line = summarize_duel(config, []).lines[0]

    assert line.players == "P1–P3"
    assert line.slots == (1, 2, 3)


def test_a_duel_summary_carries_its_combat_order():
    config = DuelConfig(min_players=2, max_players=2, combat_order=[2, 1])

    summary = summarize_duel(config, [])

    assert summary.combat_order == (2, 1)
    # Stored first-to-last; the facet reads right to left, as the duel is laid out.
    assert summary.facets[-1] == "P1 ← P2"


def test_a_simultaneous_duel_has_no_combat_order():
    assert summarize_duel(DuelConfig(), []).combat_order == ()


def test_find_references_counts_a_link_from_a_profiles_own_duel():
    """#118: a library player strategy used only by a profile duel is not unused."""
    pcs = PlayerCombatStrategy(name="Blade")
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[2, 3], player_combat_strategy_id=pcs.id)]
    )
    bcs = BotCombatStrategy(name="Solo", bot_id="b", duel_strategies=[own])

    usage = find_references(pcs.id, [bcs], [])

    assert usage.total == 1
    assert usage.phrase() == "Used by 'Own — P2–P3'"


def test_the_usage_phrase_names_the_duels():
    usage = UsageCount(duels=2, names=("'Boss — P2'", "'Trash — P1'"))
    assert usage.phrase() == "Used by 'Boss — P2' and 'Trash — P1'"


def test_the_usage_phrase_counts_past_three_names():
    usage = UsageCount(duels=5, names=tuple(f"'D{i}'" for i in range(5)))
    assert usage.phrase() == "Used by 'D0', 'D1', 'D2' and 2 more"
