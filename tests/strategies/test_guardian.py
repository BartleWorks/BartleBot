"""Default Guardian Support (Stage 6 of #218), walked through described fights.

It protects P1 first, then itself: it heals P1 below 50% health and itself below 30%,
shields P1 while P1 holds no shield and then itself, and keeps a survival aura up. With
nothing to protect it plays Default Support's ladder. ``docs/COMBAT_CONFIG.md``'s "What the
guardian does" is the rule these pin.
"""

from __future__ import annotations

import pytest
from bartleby.shared.combat_defaults import SHIPPED_DEFAULT_SUPPORT_ID, SHIPPED_GUARDIAN_SUPPORT_ID
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import TreeToConfig, get_sprinty_grammar
from wizwalker.extensions.wizsprinter.sprinty_combat import is_enchant_only

from bartlebot.nodes.combat_runner import validate_dsl
from tests.strategies.fight import (
    AMPLIFY,
    ATTENUATE,
    FAIRY,
    FEINT,
    MASS_FEINT,
    MASS_HEX,
    MEND,
    POTENT_TRAP,
    SHARPENED_BLADE,
    SIRENS,
    STABILIZE,
    STORM_BLADE,
    STORM_SHARK,
    STORM_TRAP,
    TOWER_SHIELD,
    WYLDFIRE,
    Enemy,
    Fight,
    Turn,
    Wizard,
    cast,
    shipped_dsl,
    team,
)

MOB = Enemy("Mob")
BOSS = Enemy("Boss", boss=True)
SHIELDED = (TOWER_SHIELD.hanging(),)


def guardian_dsl() -> str:
    return shipped_dsl(SHIPPED_GUARDIAN_SUPPORT_ID)


def as_p2(*hand, enemies=(BOSS,), round=2, me: Wizard | None = None, p1: Wizard | None = None) -> Fight:
    """P2 of a two-player team, P1 in the last slot."""
    return Fight(
        team=[me or Wizard("P2"), p1 or Wizard("P1")], me="P2", enemies=list(enemies), hand=list(hand), round=round
    )


# ---------------------------------------------------------------------------
# Heals
# ---------------------------------------------------------------------------


async def test_it_heals_p1_below_50_percent():
    assert await cast(guardian_dsl(), as_p2(FAIRY, FEINT, p1=Wizard("P1", health=1499))) == Turn("Fairy", "P1")


async def test_it_does_not_heal_p1_at_50_percent():
    assert (await cast(guardian_dsl(), as_p2(FAIRY, FEINT, p1=Wizard("P1", health=1500)))).card != "Fairy"


async def test_it_heals_itself_below_30_percent():
    assert await cast(guardian_dsl(), as_p2(FAIRY, FEINT, me=Wizard("P2", health=899))) == Turn("Fairy", "P2")


async def test_it_does_not_heal_itself_at_30_percent():
    assert (await cast(guardian_dsl(), as_p2(FAIRY, FEINT, me=Wizard("P2", health=900)))).card != "Fairy"


async def test_p1_is_healed_before_itself():
    fight = as_p2(FAIRY, me=Wizard("P2", health=500), p1=Wizard("P1", health=1000))

    assert await cast(guardian_dsl(), fight) == Turn("Fairy", "P1")


async def test_a_dead_p1_is_not_healed():
    fight = as_p2(FAIRY, STORM_SHARK, p1=Wizard("P1").dead, round=3)

    assert await cast(guardian_dsl(), fight) == Turn("Storm Shark", "Boss")


# ---------------------------------------------------------------------------
# Shields
# ---------------------------------------------------------------------------


async def test_it_shields_p1_before_any_buff():
    fight = as_p2(MASS_FEINT, FEINT, STORM_BLADE, TOWER_SHIELD)

    assert await cast(guardian_dsl(), fight) == Turn("Tower Shield", "P1")


async def test_with_p1_shielded_it_shields_itself():
    fight = as_p2(FEINT, TOWER_SHIELD, p1=Wizard("P1", hanging=SHIELDED))

    assert await cast(guardian_dsl(), fight) == Turn("Tower Shield", "P2")


async def test_with_both_shielded_it_buffs():
    fight = as_p2(FEINT, TOWER_SHIELD, me=Wizard("P2", hanging=SHIELDED), p1=Wizard("P1", hanging=SHIELDED))

    assert await cast(guardian_dsl(), fight) == Turn("Feint", "Boss")


async def test_a_heal_comes_before_a_shield():
    fight = as_p2(TOWER_SHIELD, FAIRY, p1=Wizard("P1", health=1000))

    assert await cast(guardian_dsl(), fight) == Turn("Fairy", "P1")


async def test_p1_s_feint_drawback_is_not_a_shield():
    """Feint leaves a trap on its caster, a harmful ward, which `beneficial_wards` skips."""
    fight = as_p2(TOWER_SHIELD, p1=Wizard("P1", hanging=(FEINT.hanging(),)))

    assert await cast(guardian_dsl(), fight) == Turn("Tower Shield", "P1")


# ---------------------------------------------------------------------------
# Survival auras
# ---------------------------------------------------------------------------


async def test_it_casts_stabilize_while_no_aura_is_up():
    assert await cast(
        guardian_dsl(), as_p2(STABILIZE, FEINT, me=Wizard("P2", hanging=SHIELDED), p1=Wizard("P1", hanging=SHIELDED))
    ) == Turn("Stabilize", None)


async def test_it_casts_mend_when_it_holds_no_stabilize():
    fight = as_p2(MEND, FEINT, me=Wizard("P2", hanging=SHIELDED), p1=Wizard("P1", hanging=SHIELDED))

    assert await cast(guardian_dsl(), fight) == Turn("Mend", None)


@pytest.mark.parametrize("held", [STABILIZE, MEND])
async def test_no_survival_aura_goes_over_one_already_up(held):
    me = Wizard("P2", hanging=(*SHIELDED, held.hanging()))
    fight = as_p2(STABILIZE, MEND, FEINT, me=me, p1=Wizard("P1", hanging=SHIELDED))

    assert await cast(guardian_dsl(), fight) == Turn("Feint", "Boss")


async def test_it_casts_no_damage_aura():
    """Amplify would raise only its own hits, which a support saves P1's buffs from."""
    fight = as_p2(AMPLIFY, SIRENS, me=Wizard("P2", hanging=SHIELDED), p1=Wizard("P1", hanging=SHIELDED))

    assert (await cast(guardian_dsl(), fight)).passed


# ---------------------------------------------------------------------------
# With nothing to protect, Default Support's ladder
# ---------------------------------------------------------------------------

_SUPPORT_FIGHTS = [
    as_p2(STORM_BLADE, SHARPENED_BLADE, FEINT, POTENT_TRAP, MASS_HEX, SIRENS, enemies=[MOB, MOB], round=1),
    as_p2(STORM_BLADE, SHARPENED_BLADE, MASS_HEX, enemies=[MOB]),
    as_p2(MASS_FEINT, FEINT, STORM_BLADE),
    as_p2(FEINT, POTENT_TRAP, STORM_BLADE, SHARPENED_BLADE),
    as_p2(STORM_BLADE, enemies=[Enemy("Boss", boss=True, hanging=[FEINT.hanging(POTENT_TRAP)])]),
    as_p2(STORM_TRAP, MASS_HEX),
    as_p2(WYLDFIRE, ATTENUATE),
    as_p2(ATTENUATE, SIRENS),
    as_p2(SIRENS, STORM_SHARK),
    as_p2(STORM_BLADE, SIRENS, p1=Wizard("P1").dead, round=3),
]


@pytest.mark.parametrize("fight", _SUPPORT_FIGHTS)
async def test_with_nothing_to_protect_it_casts_what_default_support_does(fight: Fight):
    assert await cast(guardian_dsl(), fight) == await cast(shipped_dsl(SHIPPED_DEFAULT_SUPPORT_ID), fight)


async def test_round_1_of_a_mob_fight_protects_before_the_aoe():
    fight = Fight(team=team(2), me="P2", enemies=[MOB, MOB], hand=[SIRENS, TOWER_SHIELD], round=1)

    assert await cast(guardian_dsl(), fight) == Turn("Tower Shield", "P1")


# ---------------------------------------------------------------------------
# The text
# ---------------------------------------------------------------------------


def test_it_parses_and_enchants_ahead_first_on_every_line():
    dsl = guardian_dsl()
    assert validate_dsl(dsl) is None
    config = TreeToConfig().transform(Lark(get_sprinty_grammar()).parse(dsl))
    for line in list(config.specific_rounds.values()) + list(config.infinite_rounds):
        held = [is_enchant_only(p) for p in line.priorities]
        assert held[0] and held.count(True) == 1


def test_it_names_no_boss_health_and_no_is_dead():
    dsl = guardian_dsl()
    for word in ("boss.health", "boss.hp", "is_dead"):
        assert word not in dsl
