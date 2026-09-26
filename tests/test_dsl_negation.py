"""`!` inside `any<…>`: a negated spell type, or an excluded card name (#113).

Lives in wizsprinter (`combat_config_parser`, `sprinty_combat`), which is pinned by
exact commit here and has no suite of its own, so this is where it gets covered --
the same arrangement as `test_optional_enchant.py`.

The bug: `any<trap>` matched Feint, whose +70% on the enemy is a trap by every rule
the evaluator has, so Default No-Feints Support cast Feint from its trap clauses.
"""

from __future__ import annotations

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (
    ExcludeNameSpec,
    NotTypeSpec,
    SpellType,
)
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import does_card_contain_reqs
from wizwalker.memory import EffectTarget, SpellEffects


@pytest.fixture(scope="module")
def parse():
    """One compiled grammar for the module -- Lark costs ~77 ms to build."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        config = TreeToConfig().transform(parser.parse(dsl))
        return config.infinite_rounds[0].priorities[0].move.card

    return _parse


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _describe(requirements) -> list:
    """Requirements as comparable values: the specs define no __eq__, on purpose."""
    out = []
    for r in requirements:
        if isinstance(r, NotTypeSpec):
            out.append(("not", r.type))
        elif isinstance(r, ExcludeNameSpec):
            out.append(("exclude", r.name, r.is_literal))
        else:
            out.append(r)
    return out


def test_a_type_keyword_after_the_bang_is_a_type_negation(parse):
    """`trap` also lexes as a name. The type reading has to win."""
    assert _describe(parse("any<aoe & !trap>").requirements) == [SpellType.type_aoe, ("not", SpellType.type_trap)]


def test_any_other_word_after_the_bang_is_a_name(parse):
    assert _describe(parse("any<trap & !feint>").requirements) == [SpellType.type_trap, ("exclude", "feint", False)]


def test_a_quoted_name_after_the_bang_is_exact(parse):
    assert _describe(parse('any<trap & !"Feint">').requirements) == [SpellType.type_trap, ("exclude", "Feint", True)]


def test_heal_self_negates_as_one_type(parse):
    assert _describe(parse("any<!heal self>").requirements) == [("not", SpellType.type_heal_self)]


def test_a_negation_is_not_the_type_it_negates(parse):
    """Everything downstream asks `SpellType.type_aoe in requirements`: `!aoe` must not answer yes."""
    requirements = parse("any<trap & !aoe>").requirements

    assert SpellType.type_aoe not in requirements


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class FakeEffect:
    def __init__(self, effect_type, target, param: int, rounds: int = 0):
        self._type, self._target, self._param, self._rounds = effect_type, target, param, rounds

    async def effect_type(self):
        return self._type

    async def effect_target(self):
        return self._target

    async def effect_param(self):
        return self._param

    async def num_rounds(self):
        return self._rounds


class FakeCard:
    def __init__(self, name: str, *effects: FakeEffect):
        self._name = name
        self._effects = list(effects)

    async def name(self) -> str:
        return self._name

    async def get_spell_effects(self):
        return self._effects


def _trap(target=EffectTarget.enemy_single, param=30) -> FakeEffect:
    return FakeEffect(SpellEffects.modify_incoming_damage, target, param)


FEINT = FakeCard("Feint", _trap(param=70), _trap(EffectTarget.self, 30))
MASS_FEINT = FakeCard("Feint Mass - Amulet", _trap(EffectTarget.enemy_team, 60))
STORM_TRAP = FakeCard("Storm Trap", _trap())
MASS_STORM_TRAP = FakeCard("Mass Storm Trap", _trap(EffectTarget.enemy_team))
FIRE_BLADE = FakeCard("Fire Blade", FakeEffect(SpellEffects.modify_outgoing_damage, EffectTarget.friendly_single, 35))


async def _matches(parse, dsl: str, card: FakeCard) -> bool:
    return await does_card_contain_reqs(card, parse(dsl))


async def test_the_bug_a_plain_trap_template_matches_feint(parse):
    assert await _matches(parse, "any<trap>", FEINT) is True


async def test_an_excluded_name_rejects_feint_and_keeps_a_plain_trap(parse):
    assert await _matches(parse, "any<trap & !feint>", FEINT) is False
    assert await _matches(parse, "any<trap & !feint>", STORM_TRAP) is True


async def test_an_unquoted_exclusion_is_a_case_insensitive_substring(parse):
    """So `!feint` also rules out Mass Feint, which the no-feints default allows elsewhere."""
    assert await _matches(parse, "any<trap>", MASS_FEINT) is True
    assert await _matches(parse, "any<trap & !feint>", MASS_FEINT) is False
    assert await _matches(parse, "any<trap & !FEINT>", MASS_FEINT) is False


async def test_a_quoted_exclusion_is_exact(parse):
    assert await _matches(parse, 'any<trap & !"Feint">', FEINT) is False
    assert await _matches(parse, 'any<trap & !"Feint">', MASS_FEINT) is True
    assert await _matches(parse, 'any<trap & !"feint">', FEINT) is True


async def test_a_negated_type_rejects_a_card_of_that_type(parse):
    assert await _matches(parse, "any<!blade>", FIRE_BLADE) is False
    assert await _matches(parse, "any<!trap>", FIRE_BLADE) is True


async def test_a_negated_type_reaches_aoe_effects_too(parse):
    """`aoe` beside `trap` narrows the trap to a single-target effect; a negation is
    never narrowed, so `!trap` still rules out a mass trap there."""
    assert await _matches(parse, "any<aoe>", MASS_STORM_TRAP) is True
    assert await _matches(parse, "any<aoe & !trap>", MASS_STORM_TRAP) is False


async def test_not_aoe_keeps_a_single_target_trap_and_drops_a_mass_one(parse):
    assert await _matches(parse, "any<trap & !aoe>", STORM_TRAP) is True
    assert await _matches(parse, "any<trap>", MASS_STORM_TRAP) is True
    assert await _matches(parse, "any<trap & !aoe>", MASS_STORM_TRAP) is False
