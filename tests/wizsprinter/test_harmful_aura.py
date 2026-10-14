"""`harmful_aura` finds an aura cast at an enemy, and `stronger` casts an aura only when it
beats the one its target already holds.

Lives in wizsprinter (`sprinty_combat.is_req_satisfied` and `SprintyCombat.would_replace`),
which is pinned by exact commit here and has no suite of its own, so this is where it gets
covered -- the same arrangement as `test_stacking.py`.

The cards are shaped as `Root.wad` stores them (read with katsuba, Stage 6 of #218):
Attenuate is `modify_incoming_damage` at `enemy_single`, +20 for four rounds; Typhoon
Attenuate is two such effects at +35, its Death and Storm halves; Stabilize is -25 incoming
on the caster, Mend +20 outgoing healing, Amplify +15 outgoing damage, and Frenzy +40
outgoing and +30 incoming, all on the caster for four rounds.
"""

from __future__ import annotations

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)

from tests.strategies.fight import (
    AMPLIFY,
    ATTENUATE,
    FEINT,
    FRENZY,
    MEND,
    STABILIZE,
    STORM_TRAP,
    TYPHOON_ATTENUATE,
    Enemy,
    Fight,
    Hung,
    Turn,
    Wizard,
    cast,
)
from tests.wizsprinter.fakes import Card, Hanging, Member, fake_combat


def _boss(*hanging: Hung) -> Enemy:
    return Enemy("Boss", boss=True, hanging=hanging)


def _fight(*hand, boss: Enemy | None = None, me: Wizard | None = None) -> Fight:
    me = me or Wizard("P1")
    return Fight(team=(me,), enemies=(boss or _boss(),), hand=hand, me=me.name, round=2)


# ---------------------------------------------------------------------------
# What each type matches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spell", [ATTENUATE, TYPHOON_ATTENUATE])
async def test_an_attenuate_is_a_harmful_aura_and_not_an_aura(spell):
    assert await cast("any<harmful_aura> @ boss | pass", _fight(spell)) == Turn(spell.name, "Boss")
    assert (await cast("any<aura> | pass", _fight(spell))).passed


@pytest.mark.parametrize("spell", [AMPLIFY, STABILIZE, MEND])
async def test_an_aura_on_the_caster_is_an_aura_and_not_a_harmful_one(spell):
    assert await cast("any<aura> | pass", _fight(spell)) == Turn(spell.name, None)
    assert (await cast("any<harmful_aura> @ boss | pass", _fight(spell))).passed


@pytest.mark.parametrize("spell", [FEINT, STORM_TRAP])
async def test_a_trap_is_not_a_harmful_aura(spell):
    """A trap lasts until a hit takes it, so it has no rounds."""
    assert (await cast("any<harmful_aura> @ boss | pass", _fight(spell))).passed


async def test_a_trap_line_never_takes_an_attenuate():
    assert (await cast("any<trap> @ boss | pass", _fight(ATTENUATE))).passed


async def test_the_inc_damage_line_ranks_typhoon_attenuate_first():
    turn = await cast("any<harmful_aura & inc_damage> @ boss | pass", _fight(ATTENUATE, TYPHOON_ATTENUATE))
    assert turn == Turn("Typhoon Attenuate", "Boss")


# ---------------------------------------------------------------------------
# stronger
# ---------------------------------------------------------------------------

LINE = "any<harmful_aura & inc_damage & stronger> @ boss | pass"


async def test_an_attenuate_goes_onto_a_boss_with_no_aura():
    assert await cast(LINE, _fight(ATTENUATE)) == Turn("Attenuate", "Boss")


async def test_an_attenuate_replaces_the_boss_s_own_frenzy():
    assert await cast(LINE, _fight(ATTENUATE, boss=_boss(FRENZY.hanging()))) == Turn("Attenuate", "Boss")


async def test_typhoon_attenuate_replaces_a_plain_one():
    turn = await cast(LINE, _fight(TYPHOON_ATTENUATE, boss=_boss(ATTENUATE.hanging())))
    assert turn == Turn("Typhoon Attenuate", "Boss")


@pytest.mark.parametrize("held", [ATTENUATE, TYPHOON_ATTENUATE])
async def test_an_attenuate_never_replaces_one_as_strong(held):
    assert (await cast(LINE, _fight(ATTENUATE, boss=_boss(held.hanging())))).passed


async def test_a_weaker_card_falls_through_to_a_stronger_one_in_hand():
    """Ranking offers Typhoon first; with Typhoon up, plain Attenuate is still weaker."""
    turn = await cast(LINE, _fight(ATTENUATE, TYPHOON_ATTENUATE, boss=_boss(ATTENUATE.hanging())))
    assert turn == Turn("Typhoon Attenuate", "Boss")


async def test_an_unreadable_aura_lets_the_card_through():
    boss = Member("Boss", auras=[Hanging(TYPHOON_ATTENUATE.template_id, param=35, unreadable=True)], boss=True)
    hand: list = []
    card = Card("Attenuate", ATTENUATE.effects, hand, template_id=ATTENUATE.template_id)
    sprinty = fake_combat(hand, team=[Member("P1")], enemies=[boss])
    assert await sprinty.would_replace(card, boss) is True


async def test_a_card_that_leaves_no_aura_passes_stronger():
    assert await cast("any<trap & stronger> @ boss | pass", _fight(STORM_TRAP)) == Turn("Storm Trap", "Boss")


async def test_stronger_reads_the_caster_for_an_aura_on_the_caster():
    """Stabilize goes up over nothing, and not over Stabilize; a harmful aura on me is
    worse than none, so Stabilize replaces it."""
    line = "any<aura & stronger> | pass"
    assert await cast(line, _fight(STABILIZE)) == Turn("Stabilize", None)
    assert (await cast(line, _fight(STABILIZE, me=Wizard("P1", hanging=(STABILIZE.hanging(),))))).passed
    cursed = Wizard("P1", hanging=(ATTENUATE.hanging(),))
    assert await cast(line, _fight(STABILIZE, me=cursed)) == Turn("Stabilize", None)


# ---------------------------------------------------------------------------
# stacks reads the target
# ---------------------------------------------------------------------------


async def test_stacks_reads_the_boss_for_an_attenuate():
    line = "any<harmful_aura & stacks> @ boss | pass"
    assert await cast(line, _fight(ATTENUATE)) == Turn("Attenuate", "Boss")
    assert (await cast(line, _fight(ATTENUATE, boss=_boss(ATTENUATE.hanging())))).passed
    held_by_me = Wizard("P1", hanging=(ATTENUATE.hanging(),))
    assert await cast(line, _fight(ATTENUATE, me=held_by_me)) == Turn("Attenuate", "Boss")


# ---------------------------------------------------------------------------
# The grammar
# ---------------------------------------------------------------------------


def _parse(dsl: str):
    return TreeToConfig().transform(Lark(get_sprinty_grammar(), parser="earley", lexer="dynamic").parse(dsl))


def test_both_keywords_parse():
    _parse("any<harmful_aura & inc_damage & stronger> @ boss")


def test_stronger_cannot_be_negated():
    with pytest.raises(Exception, match="negated"):
        _parse("any<harmful_aura & !stronger> @ boss")


def test_harmful_aura_can_be_negated():
    _parse("any<trap & !harmful_aura> @ boss")


def test_stronger_sits_beside_stacks():
    _parse("any<aura & stacks & stronger> | pass")
