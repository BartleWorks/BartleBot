"""The `stacks` card-template keyword: a buff only when it adds to what already hangs.

Lives in wizsprinter (`sprinty_combat.SprintyCombat.would_stack`), which is pinned by
exact commit here and has no suite of its own, so this is where it gets covered -- the
same arrangement as `test_held_enchants.py`.

Two effects with the same spell template id and the same enchant template id don't
stack, whoever cast them; the same spell with another enchant, or none, does. The ids
below are synthetic: only their equality matters.
"""

from __future__ import annotations

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import SpellType
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.memory import EffectTarget, SpellEffects

from tests.wizsprinter.fakes import Card, Effect, Hanging, Member, fake_combat

STORMBLADE = 1001
SHARPENED_BLADE = 2001  # trained
SHARPENED_BLADE_ITEM = 2002  # the item card: another template, so another id
FEINT = 3001
MASS_HEX = 3101


@pytest.fixture(scope="module")
def parse():
    """One grammar per module: Lark costs ~77 ms to build."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        return TreeToConfig().transform(parser.parse(dsl)).infinite_rounds[0].priorities

    return _parse


# ---------------------------------------------------------------------------
# Cards (the fakes are in fakes.py)
# ---------------------------------------------------------------------------


_BLADE = [Effect(SpellEffects.modify_outgoing_damage, EffectTarget.friendly_single, 35)]
# Feint: a trap on its target, and a smaller one on its own caster.
_FEINT = [
    Effect(SpellEffects.modify_incoming_damage, EffectTarget.enemy_single, 70),
    Effect(SpellEffects.modify_incoming_damage, EffectTarget.self, 30),
]
_MASS_TRAP = [Effect(SpellEffects.modify_incoming_damage, EffectTarget.enemy_team, 30)]
_ENCHANT = [Effect(SpellEffects.modify_card_damage, EffectTarget.spell, 0)]
_HIT = [Effect(SpellEffects.damage, EffectTarget.enemy_single, 500)]


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


def test_stacks_parses_as_a_template_keyword(parse):
    move = parse("any<blade & stacks>[sharp] @ ally(last)")[0].move
    assert SpellType.type_stacks in move.card.requirements
    assert SpellType.type_blade in move.card.requirements


def test_stacks_cannot_be_negated(parse):
    with pytest.raises(Exception, match="Only a spell type can be negated"):
        parse("any<blade & !stacks> @ self")


# ---------------------------------------------------------------------------
# Spell and enchant ids
# ---------------------------------------------------------------------------


async def test_a_plain_blade_does_not_stack_on_a_plain_one():
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    p1 = Member("P1", Hanging(STORMBLADE))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is False


async def test_a_plain_blade_stacks_on_a_sharpened_one():
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    p1 = Member("P1", Hanging(STORMBLADE, SHARPENED_BLADE))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_blades_sharpened_by_the_trained_and_the_item_card_enchant_stack():
    hand = []
    blade = Card("Sharpened Storm Blade", _BLADE, hand, template_id=STORMBLADE, enchant_id=SHARPENED_BLADE)
    p1 = Member("P1", Hanging(STORMBLADE, SHARPENED_BLADE_ITEM))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_a_card_already_enchanted_counts_with_its_own_enchant():
    hand = []
    blade = Card("Sharpened Storm Blade", _BLADE, hand, template_id=STORMBLADE, enchant_id=SHARPENED_BLADE)
    sharp = Card("Sharpened Blade", _ENCHANT, hand, template_id=SHARPENED_BLADE_ITEM)
    p1 = Member("P1", Hanging(STORMBLADE, SHARPENED_BLADE))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    # The move's enchant can't fuse into a card that already carries one.
    assert await combat.would_stack(blade, p1, sharp) is False


async def test_the_moves_own_enchant_counts(parse):
    """`any<blade & stacks>[sharp]` passes a plain Storm Blade onto a P1 holding a plain one,
    since what lands is a Sharpened Storm Blade."""
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    sharp = Card("Sharpened Blade", _ENCHANT, hand, template_id=SHARPENED_BLADE)
    p1 = Member("P1", Hanging(STORMBLADE))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is False
    assert await combat.try_execute_config(parse("any<blade & stacks>[sharp] @ ally(last)")[0]) is True

    assert sharp.casts == [blade]
    assert blade.casts == [p1]
    assert blade.enchant_id == SHARPENED_BLADE


async def test_the_caster_does_not_matter():
    """P3's Feint adds nothing to a boss holding P4's identical one. The trap Feint leaves
    on its own caster is a drawback, and never what decides."""
    hand = []
    feint = Card("Feint", _FEINT, hand, template_id=FEINT)
    boss = Member("Boss", Hanging(FEINT), boss=True)
    combat = fake_combat(hand, team=[Member("P3"), Member("P1")], enemies=[boss])

    assert await combat.would_stack(feint, boss) is False
    assert await combat.would_stack(feint, Member("Boss2")) is True


# ---------------------------------------------------------------------------
# Mass cards
# ---------------------------------------------------------------------------


async def test_a_mass_trap_stacks_while_one_living_enemy_lacks_it():
    hand = []
    mass_hex = Card("Mass Hex", _MASS_TRAP, hand, template_id=MASS_HEX)
    enemies = [Member("A", Hanging(MASS_HEX)), Member("B")]
    combat = fake_combat(hand, team=[Member("P1")], enemies=enemies)

    assert await combat.would_stack(mass_hex, None) is True


async def test_a_mass_trap_does_not_stack_when_every_living_enemy_has_it():
    hand = []
    mass_hex = Card("Mass Hex", _MASS_TRAP, hand, template_id=MASS_HEX)
    # The one without it is dead, so it is not where the trap lands.
    enemies = [Member("A", Hanging(MASS_HEX)), Member("B", Hanging(MASS_HEX)), Member("C", health=0)]
    combat = fake_combat(hand, team=[Member("P1")], enemies=enemies)

    assert await combat.would_stack(mass_hex, None) is False


# ---------------------------------------------------------------------------
# In a priority list
# ---------------------------------------------------------------------------


async def test_a_line_whose_only_card_fails_falls_through_to_the_next(parse):
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    feint = Card("Feint", _FEINT, hand, template_id=FEINT)
    p1 = Member("P1", Hanging(STORMBLADE))
    boss = Member("Boss", boss=True)
    combat = fake_combat(hand, team=[Member("P2"), p1], enemies=[boss])

    await combat._walk_priorities(parse("any<blade & stacks> @ ally(last) | any<trap & stacks> @ boss"))

    assert blade.casts == []
    assert feint.casts == [boss]
    assert combat.passes == 0


async def test_a_line_whose_only_card_fails_casts_nothing(parse):
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    p1 = Member("P1", Hanging(STORMBLADE))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.try_execute_config(parse("any<blade & stacks> @ ally(last)")[0]) is False
    assert blade.casts == []


async def test_the_same_line_without_stacks_casts(parse):
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    p1 = Member("P1", Hanging(STORMBLADE))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.try_execute_config(parse("any<blade> @ ally(last)")[0]) is True
    assert blade.casts == [p1]


# ---------------------------------------------------------------------------
# What can't be read
# ---------------------------------------------------------------------------


async def test_an_unreadable_hanging_effect_matches_nothing():
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    p1 = Member("P1", Hanging(STORMBLADE, unreadable=True))
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_an_unreadable_effect_list_holds_nothing_back():
    hand = []
    blade = Card("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    p1 = Member("P1")
    p1.participant.hanging = ValueError("unreadable")
    combat = fake_combat(hand, team=[Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_a_card_that_leaves_nothing_hanging_passes():
    hand = []
    hit = Card("Storm Shark", _HIT, hand, template_id=4001)
    boss = Member("Boss", Hanging(4001), boss=True)
    combat = fake_combat(hand, team=[Member("P1")], enemies=[boss])

    assert await combat.would_stack(hit, boss) is True
