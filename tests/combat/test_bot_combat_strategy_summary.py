"""The Bot Combat Strategy's side of the strategy summaries: its own summary, and what it uses.

The summaries are core's (``bartleby.runtime.combat.strategy_summary``); a Bot Combat Strategy
reaches them as a ``StrategyOwner``, through ``bartlebot.combat.bot_strategy_owner``.
"""

from __future__ import annotations

from bartleby.runtime.combat.strategy_summary import HealthStatus, UsageCount, find_references
from bartleby.shared.strategy_models import DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy

from bartlebot.combat.bot_combat_strategy import summarize_bot
from bartlebot.combat.bot_strategy_models import BotCombatStrategy, StateAssignment
from bartlebot.combat.bot_strategy_owner import BotStrategyOwner


def _owner(bcs: BotCombatStrategy) -> BotStrategyOwner:
    return BotStrategyOwner(bcs)


# ---------------------------------------------------------------------------
# summarize_bot
# ---------------------------------------------------------------------------


def test_summarize_bot_with_nothing_assigned_says_so():
    bcs = BotCombatStrategy(name="Test", bot_id="bot-1")

    summary = summarize_bot(bcs)

    assert summary.facets == ["Nothing assigned"]
    assert summary.health.status is HealthStatus.EMPTY


def test_summarize_bot_with_only_a_default_is_not_empty():
    bcs = BotCombatStrategy(name="Test", bot_id="bot-1", default_strategy_id="dcs-1")

    summary = summarize_bot(bcs)

    assert summary.facets == ["Default strategy only"]
    assert summary.health.status is HealthStatus.OK


def test_summarize_bot_counts_state_overrides():
    bcs = BotCombatStrategy(
        name="Test",
        bot_id="bot-1",
        state_assignments=[StateAssignment(state_ids=["s1", "s2"]), StateAssignment(state_ids=["s3"])],
    )

    assert summarize_bot(bcs).facets == ["3 state overrides"]


# ---------------------------------------------------------------------------
# What a Bot Combat Strategy uses
# ---------------------------------------------------------------------------


def test_find_references_counts_a_bot_default():
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(name="Farm", bot_id="b", default_strategy_id=dcs.id)
    assert find_references(dcs.id, [_owner(bcs)], []) == UsageCount(
        owners=1, owner_names=("'Farm'",), nouns=BotStrategyOwner.nouns
    )


def test_find_references_counts_a_state_override():
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="b",
        state_assignments=[StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)],
    )
    assert find_references(dcs.id, [_owner(bcs)], []).owners == 1


def test_a_bot_naming_the_same_strategy_twice_counts_once():
    """The user is being told how many things change, not how many fields do."""
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(
        name="Farm",
        bot_id="b",
        default_strategy_id=dcs.id,
        state_assignments=[StateAssignment(state_ids=["s1"], duel_combat_strategy_id=dcs.id)],
    )
    assert find_references(dcs.id, [_owner(bcs)], []).owners == 1


def test_find_references_counts_a_link_from_a_bots_own_duel():
    """#118: a library player strategy used only by one bot's own duel is not unused."""
    pcs = PlayerCombatStrategy(name="Blade")
    own = DuelCombatStrategy(
        name="Own", player_assignments=[PlayerAssignment(players=[2, 3], player_combat_strategy_id=pcs.id)]
    )
    bcs = BotCombatStrategy(name="Solo", bot_id="b", duel_strategies=[own])

    usage = find_references(pcs.id, [_owner(bcs)], [])

    assert usage.total == 1
    assert usage.phrase() == "Used by 'Own — P2–P3'"


def test_a_bot_strategy_reads_as_one_in_a_usage_phrase():
    dcs = DuelCombatStrategy(name="Boss")
    bcs = BotCombatStrategy(name="Farm", bot_id="b", default_strategy_id=dcs.id)
    assert find_references(dcs.id, [_owner(bcs)], []).phrase() == "Used by the Bot Combat Strategy 'Farm'"
