"""The `stacks` card-template keyword: a buff only when it adds to what already hangs.

Lives in wizsprinter (`sprinty_combat.SprintyCombat.would_stack`), which is pinned by
exact commit here and has no suite of its own, so this is where it gets covered -- the
same arrangement as `test_held_enchants.py`.

Two effects with the same spell template id and the same enchant template id don't
stack, whoever cast them; the same spell with another enchant, or none, does. The ids
below are synthetic: only their equality matters.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import NamedSpell, SpellType
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import SprintyCombat
from wizwalker.memory import EffectTarget, SpellEffects

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
# Fakes
# ---------------------------------------------------------------------------


class _Effect:
    """One effect on a card in hand."""

    def __init__(self, effect_type, target, param, rounds=0):
        self._type, self._target, self._param, self._rounds = effect_type, target, param, rounds

    async def effect_type(self):
        return self._type

    async def effect_target(self):
        return self._target

    async def effect_param(self):
        return self._param

    async def num_rounds(self):
        return self._rounds


_BLADE = [_Effect(SpellEffects.modify_outgoing_damage, EffectTarget.friendly_single, 35)]
# Feint: a trap on its target, and a smaller one on its own caster.
_FEINT = [
    _Effect(SpellEffects.modify_incoming_damage, EffectTarget.enemy_single, 70),
    _Effect(SpellEffects.modify_incoming_damage, EffectTarget.self, 30),
]
_MASS_TRAP = [_Effect(SpellEffects.modify_incoming_damage, EffectTarget.enemy_team, 30)]
_ENCHANT = [_Effect(SpellEffects.modify_card_damage, EffectTarget.spell, 0)]
_HIT = [_Effect(SpellEffects.damage, EffectTarget.enemy_single, 500)]


class _Hanging:
    """One effect hanging on a combatant."""

    def __init__(self, spell_id: int, enchant_id: int = 0, *, unreadable: bool = False):
        self._ids = (spell_id, enchant_id)
        self._unreadable = unreadable

    async def spell_template_id(self):
        if self._unreadable:
            raise ValueError("unreadable")
        return self._ids[0]

    async def enchantment_spell_template_id(self):
        if self._unreadable:
            raise ValueError("unreadable")
        return self._ids[1]


class _Participant:
    def __init__(self, hanging):
        self.hanging = hanging

    async def hanging_effects(self):
        if isinstance(self.hanging, Exception):
            raise self.hanging
        return self.hanging

    async def aura_effects(self):
        return []

    async def untargetable(self):
        return False


class _Member:
    def __init__(self, name: str, *hanging, health: int = 5000):
        self.name_ = name
        self._health = health
        self._participant = _Participant(list(hanging))

    async def health(self):
        return self._health

    async def get_participant(self):
        return self._participant

    def __repr__(self):
        return f"_Member({self.name_})"


class _GraphicalSpell:
    def __init__(self, card):
        self._card = card

    async def enchantment(self):
        return self._card.enchant_id


class _Card:
    def __init__(self, name: str, template_id: int, effects, hand: list, *, kind: str = "", enchant_id: int = 0):
        self._name = name
        self._template_id = template_id
        self._effects = effects
        self._hand = hand
        self.kind = kind
        self.enchant_id = enchant_id
        self.casts = []
        hand.append(self)

    async def name(self):
        return self._name

    async def template_id(self):
        return self._template_id

    async def get_graphical_spell(self):
        return _GraphicalSpell(self)

    async def get_spell_effects(self):
        return self._effects

    async def is_enchanted(self):
        return self.enchant_id != 0

    async def is_enchanted_from_item_card(self):
        return False

    async def is_treasure_card(self):
        return False

    async def is_item_card(self):
        return False

    async def is_cloaked(self):
        return False

    async def is_castable(self):
        return True

    async def cast(self, target, sleep_time=None):
        self.casts.append(target)
        self._hand.remove(self)
        if isinstance(target, _Card):  # an enchant fuses into the card
            target._name = f"Sharpened {target._name}"
            target.enchant_id = self._template_id

    def __repr__(self):
        return f"_Card({self._name})"


def _combat(hand: list, *, team: list, enemies: list = (), boss=None):
    """A SprintyCombat over fakes. The caster is `team[0]`; `ally(last)` is `team[-1]`."""
    combat = SprintyCombat.__new__(SprintyCombat)
    combat.config = SimpleNamespace(cast_time=0)
    combat.held_enchants = []
    combat.cur_card_count = 0
    combat.passes = 0

    async def try_get_spell(spell, multi=False, **_):
        if isinstance(spell, NamedSpell):
            if spell.name in ("pass", "none", "willcast", "discard"):
                return spell.name
            for card in hand:
                name = await card.name()
                if (name == spell.name) if spell.is_literal else (spell.name.lower() in name.lower()):
                    return card
            return None
        kinds = {r.name.removeprefix("type_") for r in spell.requirements if isinstance(r, SpellType)}
        matches = [card for card in hand if card.kind in kinds]
        if not matches:
            return None
        return matches if multi else matches[0]

    async def get_cards():
        return list(hand)

    async def pass_button():
        combat.passes += 1

    async def get_client_member():
        return team[0]

    async def get_team():
        return list(team)

    async def get_allies():
        return list(team[1:])

    async def get_enemies():
        return list(enemies)

    async def get_boss_or_none():
        return boss

    combat.try_get_spell = try_get_spell
    combat.get_cards = get_cards
    combat.pass_button = pass_button
    combat.get_client_member = get_client_member
    combat.get_team = get_team
    combat.get_allies = get_allies
    combat.get_enemies = get_enemies
    combat.get_boss_or_none = get_boss_or_none
    return combat


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
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand)
    p1 = _Member("P1", _Hanging(STORMBLADE))
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is False


async def test_a_plain_blade_stacks_on_a_sharpened_one():
    hand = []
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand)
    p1 = _Member("P1", _Hanging(STORMBLADE, SHARPENED_BLADE))
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_blades_sharpened_by_the_trained_and_the_item_card_enchant_stack():
    hand = []
    blade = _Card("Sharpened Storm Blade", STORMBLADE, _BLADE, hand, enchant_id=SHARPENED_BLADE)
    p1 = _Member("P1", _Hanging(STORMBLADE, SHARPENED_BLADE_ITEM))
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_a_card_already_enchanted_counts_with_its_own_enchant():
    hand = []
    blade = _Card("Sharpened Storm Blade", STORMBLADE, _BLADE, hand, enchant_id=SHARPENED_BLADE)
    sharp = _Card("Sharpened Blade", SHARPENED_BLADE_ITEM, _ENCHANT, hand)
    p1 = _Member("P1", _Hanging(STORMBLADE, SHARPENED_BLADE))
    combat = _combat(hand, team=[_Member("P2"), p1])

    # The move's enchant can't fuse into a card that already carries one.
    assert await combat.would_stack(blade, p1, sharp) is False


async def test_the_moves_own_enchant_counts(parse):
    """`any<blade & stacks>[sharp]` passes a plain Storm Blade onto a P1 holding a plain one,
    since what lands is a Sharpened Storm Blade."""
    hand = []
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand, kind="blade")
    sharp = _Card("Sharpened Blade", SHARPENED_BLADE, _ENCHANT, hand)
    p1 = _Member("P1", _Hanging(STORMBLADE))
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is False
    assert await combat.try_execute_config(parse("any<blade & stacks>[sharp] @ ally(last)")[0]) is True

    assert sharp.casts == [blade]
    assert blade.casts == [p1]
    assert blade.enchant_id == SHARPENED_BLADE


async def test_the_caster_does_not_matter():
    """P3's Feint adds nothing to a boss holding P4's identical one. The trap Feint leaves
    on its own caster is a drawback, and never what decides."""
    hand = []
    feint = _Card("Feint", FEINT, _FEINT, hand)
    boss = _Member("Boss", _Hanging(FEINT))
    combat = _combat(hand, team=[_Member("P3"), _Member("P1")], enemies=[boss], boss=boss)

    assert await combat.would_stack(feint, boss) is False
    assert await combat.would_stack(feint, _Member("Boss2")) is True


# ---------------------------------------------------------------------------
# Mass cards
# ---------------------------------------------------------------------------


async def test_a_mass_trap_stacks_while_one_living_enemy_lacks_it():
    hand = []
    mass_hex = _Card("Mass Hex", MASS_HEX, _MASS_TRAP, hand)
    enemies = [_Member("A", _Hanging(MASS_HEX)), _Member("B")]
    combat = _combat(hand, team=[_Member("P1")], enemies=enemies)

    assert await combat.would_stack(mass_hex, None) is True


async def test_a_mass_trap_does_not_stack_when_every_living_enemy_has_it():
    hand = []
    mass_hex = _Card("Mass Hex", MASS_HEX, _MASS_TRAP, hand)
    # The one without it is dead, so it is not where the trap lands.
    enemies = [_Member("A", _Hanging(MASS_HEX)), _Member("B", _Hanging(MASS_HEX)), _Member("C", health=0)]
    combat = _combat(hand, team=[_Member("P1")], enemies=enemies)

    assert await combat.would_stack(mass_hex, None) is False


# ---------------------------------------------------------------------------
# In a priority list
# ---------------------------------------------------------------------------


async def test_a_line_whose_only_card_fails_falls_through_to_the_next(parse):
    hand = []
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand, kind="blade")
    feint = _Card("Feint", FEINT, _FEINT, hand, kind="trap")
    p1 = _Member("P1", _Hanging(STORMBLADE))
    boss = _Member("Boss")
    combat = _combat(hand, team=[_Member("P2"), p1], enemies=[boss], boss=boss)

    await combat._walk_priorities(parse("any<blade & stacks> @ ally(last) | any<trap & stacks> @ boss"))

    assert blade.casts == []
    assert feint.casts == [boss]
    assert combat.passes == 0


async def test_a_line_whose_only_card_fails_casts_nothing(parse):
    hand = []
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand, kind="blade")
    p1 = _Member("P1", _Hanging(STORMBLADE))
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.try_execute_config(parse("any<blade & stacks> @ ally(last)")[0]) is False
    assert blade.casts == []


async def test_the_same_line_without_stacks_casts(parse):
    hand = []
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand, kind="blade")
    p1 = _Member("P1", _Hanging(STORMBLADE))
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.try_execute_config(parse("any<blade> @ ally(last)")[0]) is True
    assert blade.casts == [p1]


# ---------------------------------------------------------------------------
# What can't be read
# ---------------------------------------------------------------------------


async def test_an_unreadable_hanging_effect_matches_nothing():
    hand = []
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand)
    p1 = _Member("P1", _Hanging(STORMBLADE, unreadable=True))
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_an_unreadable_effect_list_holds_nothing_back():
    hand = []
    blade = _Card("Storm Blade", STORMBLADE, _BLADE, hand)
    p1 = _Member("P1")
    p1._participant.hanging = ValueError("unreadable")
    combat = _combat(hand, team=[_Member("P2"), p1])

    assert await combat.would_stack(blade, p1) is True


async def test_a_card_that_leaves_nothing_hanging_passes():
    hand = []
    hit = _Card("Storm Shark", 4001, _HIT, hand)
    boss = _Member("Boss", _Hanging(4001))
    combat = _combat(hand, team=[_Member("P1")], enemies=[boss], boss=boss)

    assert await combat.would_stack(hit, boss) is True
