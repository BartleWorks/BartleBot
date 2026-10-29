"""Which player strategies aim at a slot, and when a duel leaves that slot to chance (#212)."""

from __future__ import annotations

from bartleby.shared.strategy_models import DuelConfig, PlayerAssignment

from bartlebot.combat.slot_order import slot_targets, unfixed_slot_uses

_SUPPORT = "any<blade>[sharp] @ ally(last) | pass"


def test_a_strategy_aiming_at_the_last_slot_names_it():
    assert slot_targets(_SUPPORT) == ("ally(last)",)


def test_each_slot_is_named_once_in_first_use_order():
    dsl = "?(ally(last).health == 0) any<damage> @ enemy | any<ward> @ ally(0) | any<blade> @ ALLY( last )"

    assert slot_targets(dsl) == ("ally(last)", "ally(0)")


def test_ally_alone_is_no_slot():
    assert slot_targets("any<heal> @ ally | any<blade> @ self | any<damage> @ enemy(1)") == ()


def test_a_quoted_name_is_no_slot():
    assert slot_targets('"ally(last)" @ boss') == ()


def test_an_empty_dsl_names_no_slot():
    assert slot_targets("") == ()


def test_a_duel_with_no_combat_order_leaves_the_slot_to_chance():
    config = DuelConfig(
        max_players=4,
        player_assignments=[
            PlayerAssignment(players=[1], dsl="any<damage> @ boss"),
            PlayerAssignment(players=[4, 2, 3], dsl=_SUPPORT),
        ],
    )

    assert unfixed_slot_uses(config, {}) == [([2, 3, 4], ("ally(last)",))]


def test_a_linked_strategy_is_read_through_its_dsl():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[2], player_combat_strategy_id="pcs")])

    assert unfixed_slot_uses(config, {"pcs": _SUPPORT}) == [([2], ("ally(last)",))]


def test_a_linked_strategy_that_is_missing_is_left_to_its_own_fault():
    config = DuelConfig(player_assignments=[PlayerAssignment(players=[2], player_combat_strategy_id="gone")])

    assert unfixed_slot_uses(config, {}) == []


def test_a_combat_order_fixes_every_slot():
    config = DuelConfig(combat_order=[4, 3, 2, 1], player_assignments=[PlayerAssignment(players=[2], dsl=_SUPPORT)])

    assert unfixed_slot_uses(config, {}) == []


def test_a_duel_for_one_player_has_only_one_slot():
    config = DuelConfig(max_players=1, player_assignments=[PlayerAssignment(players=[1], dsl=_SUPPORT)])

    assert unfixed_slot_uses(config, {}) == []
