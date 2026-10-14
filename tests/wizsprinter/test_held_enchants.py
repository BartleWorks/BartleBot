"""Enchant-only slots are held until the round's card is known.

Lives in wizsprinter (`sprinty_combat`), which is pinned by exact commit here and has no
suite of its own, so this is where it gets covered -- the same arrangement as
`test_optional_enchant.py`.

Fusing an enchant into a card in hand never ends the turn, and frees a hand slot that the
next refill draws into, which is how a large deck cycles. So a priority made only of
`X @ spell(Y)` moves is held when the walk reaches it, and runs just before the round's
card is cast, unless that card attacks: an attack leaves the hand as it is and takes its
own `[...]` enchant. After running, the walk starts again from the top, since an enchant
can fuse the very card that was picked. Before this, such a slot "succeeded" without
casting anything, and the turn sat until its timer ran out.
"""

from __future__ import annotations

import logging

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter import sprinty_combat
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import (
    RESTART,
    SprintyCombat,
    card_is_attack,
    is_enchant_only,
)
from wizwalker.memory import EffectTarget, SpellEffects

from tests.wizsprinter.fakes import Card, Effect, fake_combat

_HOLD = "?(deck.remaining >= 2) any<mod_damage> @ spell(any<damage>) & sharp @ spell(any<blade>)"


@pytest.fixture(scope="module")
def priorities():
    """The priorities of a one-line DSL. One grammar per module: Lark costs ~77 ms to build."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        return TreeToConfig().transform(parser.parse(dsl)).infinite_rounds[0].priorities

    return _parse


# ---------------------------------------------------------------------------
# Which slots are held
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dsl, held",
    [
        ("sharp @ spell(any<blade>)", True),
        (_HOLD, True),
        ('"Sharpened Blade" @ spell("Storm Blade")', True),
        ("sharp @ spell(any<blade>) & any<blade> @ self", False),  # Deimos's form: ends in a cast
        ("discard @ spell(any<heal>)", False),
        ("any<blade>[sharp] @ self", False),
        ("pass", False),
    ],
)
def test_which_slots_are_enchant_only(priorities, dsl, held):
    assert is_enchant_only(priorities(dsl)[0]) is held


# ---------------------------------------------------------------------------
# A fake hand, for the real try_execute_config
# ---------------------------------------------------------------------------


_ATTACK = [Effect(SpellEffects.damage, EffectTarget.enemy_single, 500)]
_BLADE = [Effect(SpellEffects.modify_outgoing_damage, EffectTarget.self, 35)]
_ENCHANT = [Effect(SpellEffects.modify_card_outgoing_damage, EffectTarget.spell)]


async def test_a_non_attack_with_enchants_held_restarts_before_casting(priorities):
    hand = []
    blade = Card("Storm Blade", _BLADE, hand)
    combat = fake_combat(hand, held=priorities(_HOLD))

    result = await combat.try_execute_config(priorities('"Storm Blade"')[0])

    assert result is RESTART
    assert blade.casts == []


async def test_an_attack_drops_the_held_enchants_and_casts(priorities):
    hand = []
    shark = Card("Storm Shark", _ATTACK, hand)
    combat = fake_combat(hand, held=priorities(_HOLD))

    result = await combat.try_execute_config(priorities('"Storm Shark"')[0])

    assert result is True
    assert shark.casts == [None]
    assert combat.held_enchants == []


async def test_a_pass_with_enchants_held_restarts_first(priorities):
    combat = fake_combat([], held=priorities(_HOLD))

    assert await combat.try_execute_config(priorities("pass")[0]) is RESTART
    assert combat.passes == 0


async def test_a_pass_with_nothing_held_passes(priorities):
    combat = fake_combat([])

    assert await combat.try_execute_config(priorities("pass")[0]) is True
    assert combat.passes == 1


async def test_an_enchant_aimed_at_a_card_is_not_the_rounds_cast(priorities):
    """The enchant moves of a Deimos-style chain run while slots are held, without
    restarting the walk: casting one onto a card is not what ends the turn."""
    hand = []
    sharp = Card("Sharpened Blade", _ENCHANT, hand)
    blade = Card("Storm Blade", _BLADE, hand)
    combat = fake_combat(hand, held=priorities(_HOLD))

    result = await combat.try_execute_config(priorities("sharp @ spell(any<blade>)")[0])

    assert result is True
    assert sharp.casts == [blade]
    assert len(combat.held_enchants) == 1


async def test_a_restart_inside_a_chain_stops_the_chain(priorities):
    hand = []
    Card("Storm Blade", _BLADE, hand)
    combat = fake_combat(hand, held=priorities(_HOLD))

    assert await combat.try_execute_config(priorities('pass & "Storm Blade"')[0]) is RESTART


@pytest.mark.parametrize(
    "effects, attacks",
    [
        (_ATTACK, True),
        ([Effect(SpellEffects.damage_over_time, EffectTarget.enemy_team)], True),
        ([Effect(SpellEffects.steal_health, EffectTarget.enemy_single)], True),
        (_BLADE, False),
        ([Effect(SpellEffects.modify_incoming_damage, EffectTarget.enemy_single)], False),  # a trap
        ([Effect(SpellEffects.heal, EffectTarget.self)], False),
    ],
)
async def test_which_cards_count_as_an_attack(effects, attacks):
    assert await card_is_attack(Card("card", effects, [])) is attacks


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def _walker(*, castable: set[str], attacks: set[str], condition_holds: bool = True):
    """A combat whose try_execute_config keeps the contract the real one is tested for above,
    over a hand of named cards, and records what the walk did in order."""
    combat = SprintyCombat.__new__(SprintyCombat)
    combat.held_enchants = []
    log = []

    async def try_execute_config(move_config, willcasted=False):
        if is_enchant_only(move_config):
            log.append("enchants")
            return True
        name = move_config.move.card.name
        if name not in castable:
            return False
        if combat.held_enchants:
            if name in attacks:
                combat.held_enchants = []
            else:
                log.append(f"restart at {name}")
                return RESTART
        log.append(f"cast {name}")
        return True

    async def evaluate_condition(condition):
        return condition_holds

    async def pass_button():
        log.append("pass")

    combat.try_execute_config = try_execute_config
    combat.evaluate_condition = evaluate_condition
    combat.pass_button = pass_button
    return combat, log


_ROUND = f'{_HOLD} | "Storm Blade" @ self | "Storm Shark" @ enemy'


async def test_enchants_run_before_a_non_attack_and_then_it_casts(priorities):
    combat, log = _walker(castable={"Storm Blade"}, attacks=set())

    await combat._walk_priorities(priorities(_ROUND))

    assert log == ["restart at Storm Blade", "enchants", "cast Storm Blade"]


async def test_an_attack_round_never_enchants(priorities):
    combat, log = _walker(castable={"Storm Shark"}, attacks={"Storm Shark"})

    await combat._walk_priorities(priorities(_ROUND))

    assert log == ["cast Storm Shark"]


async def test_a_slot_whose_condition_fails_is_never_held(priorities):
    """A small deck: nothing to cycle into, so the hand is left alone."""
    combat, log = _walker(castable={"Storm Blade"}, attacks=set(), condition_holds=False)

    await combat._walk_priorities(priorities(_ROUND))

    assert log == ["cast Storm Blade"]


async def test_enchants_run_before_a_forced_pass(priorities):
    combat, log = _walker(castable=set(), attacks=set())

    await combat._walk_priorities(priorities(_ROUND))

    assert log == ["enchants", "pass"]


async def test_nothing_held_and_nothing_castable_passes(priorities):
    """An enchant-only slot no longer ends the walk on its own; before, it "succeeded"
    without casting and the turn sat until its timer ran out."""
    combat, log = _walker(castable=set(), attacks=set(), condition_holds=False)

    await combat._walk_priorities(priorities(_ROUND))

    assert log == ["pass"]


async def test_a_slot_below_the_cast_is_never_reached(priorities):
    combat, log = _walker(castable={"Storm Blade"}, attacks=set())

    await combat._walk_priorities(priorities(f'"Storm Blade" @ self | {_HOLD}'))

    assert log == ["cast Storm Blade"]


# ---------------------------------------------------------------------------
# A refused enchant
# ---------------------------------------------------------------------------


@pytest.fixture
def short_timeout(monkeypatch):
    monkeypatch.setattr(sprinty_combat, "ENCHANT_TIMEOUT", 0.05)


@pytest.mark.parametrize("recast", [True, False])
async def test_a_fused_enchant_returns_once_the_hand_shrinks(short_timeout, recast):
    hand = []
    sharp = Card("Sharpened Blade", _ENCHANT, hand)
    blade = Card("Storm Blade", _BLADE, hand)

    assert await fake_combat(hand)._fuse_enchant(sharp, blade, recast=recast) is True
    assert sharp.casts == [blade]


@pytest.mark.parametrize("recast", [True, False])
async def test_a_refused_enchant_gives_up_instead_of_hanging(short_timeout, caplog, recast):
    """The game refuses an enchant on a card it does not fit by doing nothing, and the
    waits used to poll for the fuse with no limit."""
    hand = []
    sharp = Card("Sharpened Blade", _ENCHANT, hand)
    blade = Card("Storm Blade", _BLADE, hand, fuses=False)

    with caplog.at_level(logging.WARNING):
        assert await fake_combat(hand)._fuse_enchant(sharp, blade, recast=recast) is False

    assert "Sharpened Blade onto Storm Blade did not fuse" in caplog.text
    assert (len(sharp.casts) > 1) is recast
