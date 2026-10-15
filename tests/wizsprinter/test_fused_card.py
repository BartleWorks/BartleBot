"""After a move fuses its own enchant, the card cast is the fused one.

Lives in wizsprinter (`sprinty_combat.SprintyCombat.try_execute_config`), which is pinned
by exact commit here and has no suite of its own, so this is where it gets covered -- the
same arrangement as `test_held_enchants.py`.

A fuse replaces the card it lands on: the old window goes, and the fused card takes a new
one, with the same name and template and the enchant's template id as its enchantment.
wizsprinter used to find the fused card by a name new to the hand, which a fuse never
makes, and a `stacks` move then cast the handle it picked before the fuse. That handle is
dead after it, so in game the click did nothing, or landed on another card: a support
holding three blades, three Sharpened Blades and a hit wasted every round whose blade took
its own enchant, and once clicked the hit at P1. The fakes' `Slot` handles die the same
way, which is what these tests stand on.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter import sprinty_combat
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.memory import EffectTarget, SpellEffects

from bartlebot.shared.combat_defaults import SHIPPED_DEFAULT_SUPPORT_ID
from tests.strategies.fight import (
    _INSTANT,
    SHARPENED_BLADE,
    STORM_BLADE,
    STORM_SHARK,
    Enemy,
    Fight,
    Hung,
    Turn,
    Wizard,
    _spell,
    cast,
    shipped_dsl,
)
from tests.wizsprinter.fakes import Card, Member, cast_at_a_combatant, fake_combat
from tests.wizsprinter.test_stacking import _BLADE, _ENCHANT, _HIT, STORMBLADE
from tests.wizsprinter.test_stacking import SHARPENED_BLADE as SHARP_ID

POTENT_ID = 2101  # synthetic: only its difference from SHARP_ID matters

#: The other two Sharpened Blades of that deck: each its own template, so its own enchant id.
PET_SHARPENED_BLADE = _spell("Pet - Sharpened Blade", 1011, (SpellEffects.modify_card_charm, EffectTarget.spell, 10))
TC_SHARPENED_BLADE = _spell("Sharpened Blade - Tear", 1012, (SpellEffects.modify_card_charm, EffectTarget.spell, 10))


@pytest.fixture(scope="module")
def parse():
    """One grammar per module: Lark costs ~77 ms to build."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        return TreeToConfig().transform(parser.parse(dsl)).infinite_rounds[0].priorities

    return _parse


async def _run(combat, move) -> bool:
    with patch.object(sprinty_combat, "asyncio", _INSTANT):
        return await combat.try_execute_config(move)


def _two_slots():
    return [Member("P2"), Member("P1")]


# ---------------------------------------------------------------------------
# The fight that found it
# ---------------------------------------------------------------------------


async def test_the_default_support_casts_the_blade_its_own_enchant_fused():
    """Round 3 of the run that found it: P1 already holds two Storm Blades, each with
    another Sharpened Blade, and P2 holds the last blade, the last enchant and a hit."""
    p1 = Wizard("P1", hanging=[Hung(STORM_BLADE, PET_SHARPENED_BLADE), Hung(STORM_BLADE, TC_SHARPENED_BLADE)])
    fight = Fight(
        team=(Wizard("P2"), p1),
        enemies=(Enemy("Boss", boss=True),),
        hand=(STORM_BLADE, SHARPENED_BLADE, STORM_SHARK),
        me="P2",
        round=3,
        deck=0,  # as it read: the enchant-ahead line stays off, so the cast line fuses
    )

    turn = await cast(shipped_dsl(SHIPPED_DEFAULT_SUPPORT_ID), fight)

    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade", (("Sharpened Blade", "Storm Blade"),))


# ---------------------------------------------------------------------------
# Finding the fused card
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dsl", ["any<blade & stacks>[sharp] @ ally(last)", "any<blade>[sharp] @ ally(last)"])
async def test_the_card_cast_is_the_one_the_enchant_fused_into(parse, dsl):
    hand: list = []
    blades = [Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE) for _ in range(3)]
    Card("Sharpened Blade", _ENCHANT, hand, template_id=SHARP_ID)
    hit = Card("Storm Shark", _HIT, hand, template_id=4001)
    combat = fake_combat(hand, team=_two_slots())

    assert await _run(combat, parse(dsl)[0]) is True

    cast_ones = [b for b in blades if cast_at_a_combatant(b)]
    assert len(cast_ones) == 1
    assert cast_ones[0].enchant_id == SHARP_ID
    assert not hit.casts


async def test_a_second_enchant_lands_on_the_fused_card_and_that_card_is_cast(parse):
    hand: list = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    sharp = Card("Sharpened Blade", _ENCHANT, hand, template_id=SHARP_ID)
    potent = Card("Potent Trap", _ENCHANT, hand, template_id=POTENT_ID)
    combat = fake_combat(hand, team=_two_slots())

    assert await _run(combat, parse("any<blade>[sharp][potent] @ ally(last)")[0]) is True

    assert sharp.casts == [blade] and potent.casts == [blade]
    assert cast_at_a_combatant(blade)
    assert blade.enchant_id == POTENT_ID


class _Vanishes(Card):
    """A card a fuse turns into something with neither its name nor its template."""

    def fuse(self, enchant: Card) -> None:
        super().fuse(enchant)
        self.template_id_ += 1


async def test_a_fused_card_that_cannot_be_found_is_not_cast_and_nothing_else_is(parse):
    hand: list = []
    blade = _Vanishes("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    Card("Sharpened Blade", _ENCHANT, hand, template_id=SHARP_ID)
    hit = Card("Storm Shark", _HIT, hand, template_id=4001)
    combat = fake_combat(hand, team=_two_slots())

    assert await _run(combat, parse("any<blade & stacks>[sharp] @ ally(last)")[0]) is False

    assert not cast_at_a_combatant(blade)
    assert not hit.casts


async def test_a_card_cast_bare_is_the_one_picked(parse):
    """With no enchant in hand nothing fuses, and the handle picked stays good."""
    hand: list = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    combat = fake_combat(hand, team=_two_slots())

    assert await _run(combat, parse("any<blade & stacks>[sharp] @ ally(last)")[0]) is True

    assert cast_at_a_combatant(blade) and blade.enchant_id == 0
