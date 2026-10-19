"""The shipped buff supports (Stages 3 and 6 of #218): Default Support and the two
cheat-boss supports, walked through described fights.

A buff support never heals or shields. It attacks only in round 1 of a mob fight or once P1
is dead, and otherwise buffs P1 and traps the boss, a buff that stacks before one that
doesn't, casts a global, then a negative aura on the boss when it beats the one there, and
passes rather than spend P1's buffs. ``docs/COMBAT_CONFIG.md``'s "What a support does" is
the rule these pin. Default Guardian Support is ``test_guardian.py``'s.
"""

from __future__ import annotations

import random

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import TreeToConfig, get_sprinty_grammar
from wizwalker.extensions.wizsprinter.sprinty_combat import is_enchant_only

from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.shared.combat_defaults import SHIPPED_DEFAULT_SUPPORT_ID
from tests.strategies.fight import (
    AMPLIFY,
    ATTENUATE,
    BALANCEBLADE,
    EPIC,
    FAIRY,
    FEINT,
    FIRE_PRISM,
    MASS_FEINT,
    MASS_FIRE_PRISM,
    MASS_HEX,
    POTENT_TRAP,
    SHARPENED_BLADE,
    SIRENS,
    STORM_BLADE,
    STORM_SHARK,
    STORM_TRAP,
    TOWER_SHIELD,
    TYPHOON_ATTENUATE,
    WYLDFIRE,
    Enemy,
    Fight,
    Turn,
    Wizard,
    cast,
    shipped_dsl,
    team,
)

NO_BLADES_ID = "0c0f0554-df96-440d-837e-21ca2004198e"
NO_FEINTS_ID = "f0b1a45f-4b97-4b7e-aae8-e334307185ce"
SUPPORT_IDS = [SHIPPED_DEFAULT_SUPPORT_ID, NO_BLADES_ID, NO_FEINTS_ID]

MOB = Enemy("Mob")
BOSS = Enemy("Boss", boss=True)


def support_dsl() -> str:
    return shipped_dsl(SHIPPED_DEFAULT_SUPPORT_ID)


def as_p2(*hand, enemies=(BOSS,), round=2, me: Wizard | None = None, p1: Wizard | None = None, **kwargs) -> Fight:
    """P2 of a two-player Default Team Duel, P1 in the last slot."""
    return Fight(
        team=[me or Wizard("P2"), p1 or Wizard("P1")],
        me="P2",
        enemies=list(enemies),
        hand=list(hand),
        round=round,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Round 1 of a mob fight, and after it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [2, 4])
async def test_every_support_casts_aoe_in_round_1_of_a_mob_fight(size: int):
    hand = [STORM_BLADE, SHARPENED_BLADE, FEINT, POTENT_TRAP, MASS_HEX, SIRENS]
    for me in [f"P{n}" for n in range(size, 1, -1)]:
        fight = Fight(team=team(size), me=me, enemies=[MOB, MOB], hand=hand, round=1)

        assert (await cast(support_dsl(), fight)).card == "Sirens", me


async def test_round_1_of_a_mob_fight_hits_one_mob_without_an_aoe():
    turn = await cast(support_dsl(), as_p2(STORM_BLADE, STORM_SHARK, enemies=[MOB, MOB], round=1))

    assert turn == Turn("Storm Shark", "Mob")


async def test_round_1_of_a_boss_fight_buffs():
    """A `{1}` line is the whole of round 1, so it carries the ladder after its AoE."""
    turn = await cast(support_dsl(), as_p2(FEINT, POTENT_TRAP, SIRENS, enemies=[MOB, BOSS], round=1))

    assert turn == Turn("Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),))


async def test_a_mob_fight_after_round_1_blades_p1_before_a_mass_trap():
    turn = await cast(support_dsl(), as_p2(STORM_BLADE, MASS_HEX, SIRENS, enemies=[MOB, MOB]))

    assert turn == Turn("Storm Blade", "P1")


async def test_a_mob_fight_after_round_1_casts_a_mass_trap_with_no_blade():
    turn = await cast(support_dsl(), as_p2(MASS_HEX, SIRENS, enemies=[MOB, MOB], round=3))

    assert turn.card == "Mass Hex"


# ---------------------------------------------------------------------------
# A boss fight: the buffs, in order
# ---------------------------------------------------------------------------


async def test_a_boss_fight_casts_mass_feint_first():
    turn = await cast(support_dsl(), as_p2(STORM_BLADE, FEINT, POTENT_TRAP, MASS_FEINT))

    assert turn.card == "Feint Mass - Amulet"


async def test_a_boss_fight_feints_the_boss_before_a_blade():
    turn = await cast(support_dsl(), as_p2(STORM_BLADE, FEINT, POTENT_TRAP, MASS_HEX))

    assert turn == Turn("Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),))


async def test_a_boss_already_holding_that_feint_gets_a_blade_on_p1_instead():
    boss = Enemy("Boss", boss=True, hanging=[FEINT.hanging(POTENT_TRAP)])

    turn = await cast(support_dsl(), as_p2(FEINT, POTENT_TRAP, STORM_BLADE, SHARPENED_BLADE, enemies=[boss]))

    # The enchant-ahead slot fuses the Potent Trap into the Feint, since the round's card
    # is not an attack.
    fuses = (("Sharpened Blade", "Storm Blade"), ("Potent Trap", "Feint"))
    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade", fuses=fuses)


async def test_a_boss_fight_blades_p1_before_a_mass_trap():
    turn = await cast(support_dsl(), as_p2(MASS_HEX, STORM_BLADE))

    assert turn == Turn("Storm Blade", "P1")


async def test_a_single_trap_on_the_boss_ranks_with_feint():
    """Not in the plan: the Feint line is `any<trap & !aoe & stacks>`, so with no Feint
    in hand it takes the best single trap, ahead of the blade (Stage 3's As built)."""
    turn = await cast(support_dsl(), as_p2(MASS_HEX, STORM_BLADE, STORM_TRAP))

    assert turn.card == "Storm Trap"


# ---------------------------------------------------------------------------
# A buff that won't stack, a global, and nothing that helps
# ---------------------------------------------------------------------------


async def test_a_blade_that_wont_stack_is_still_cast():
    p1 = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    assert await cast(support_dsl(), as_p2(STORM_BLADE, SIRENS, p1=p1)) == Turn("Storm Blade", "P1")


async def test_a_feint_that_wont_stack_is_still_cast():
    boss = Enemy("Boss", boss=True, hanging=[FEINT.hanging()])

    assert await cast(support_dsl(), as_p2(FEINT, SIRENS, enemies=[boss])) == Turn("Feint", "Boss")


async def test_a_buff_that_stacks_beats_one_that_wont():
    """The Feint on the boss is already there; the blade on P1 is not."""
    boss = Enemy("Boss", boss=True, hanging=[FEINT.hanging()])

    assert (await cast(support_dsl(), as_p2(FEINT, STORM_BLADE, enemies=[boss]))).card == "Storm Blade"


@pytest.mark.parametrize("enemies", [[BOSS], [MOB, MOB]], ids=["boss", "mobs"])
async def test_a_global_is_the_last_buff(enemies):
    assert (await cast(support_dsl(), as_p2(WYLDFIRE, SIRENS, STORM_SHARK, enemies=enemies))).card == "Wyldfire"


@pytest.mark.parametrize(
    ("enemies", "round_"),
    [([BOSS], 1), ([BOSS], 2), ([MOB, BOSS], 3), ([MOB, MOB], 2), ([MOB], 4)],
    ids=["boss-round-1", "boss-round-2", "boss-and-mob", "mobs-round-2", "mob-round-4"],
)
async def test_holding_only_damage_passes(enemies, round_):
    assert (await cast(support_dsl(), as_p2(SIRENS, STORM_SHARK, enemies=enemies, round=round_))).passed


# ---------------------------------------------------------------------------
# P1 dead, and no heals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_attacks_once_p1_is_dead(support_id: str):
    fight = as_p2(STORM_BLADE, FEINT, MASS_HEX, FAIRY, SIRENS, p1=Wizard("P1").dead, round=3)

    assert await cast(shipped_dsl(support_id), fight) == Turn("Sirens", None)


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_with_p1_dead_and_no_aoe_hits_one_enemy(support_id: str):
    fight = as_p2(STORM_TRAP, STORM_SHARK, p1=Wizard("P1").dead, round=3)

    assert await cast(shipped_dsl(support_id), fight) == Turn("Storm Shark", "Boss")


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
@pytest.mark.parametrize("health", [100, 899, 1500])
async def test_a_buff_support_never_heals(support_id: str, health: int):
    """At any health, itself or P1: healing is Default Guardian Support's job."""
    fight = as_p2(FAIRY, STORM_TRAP, me=Wizard("P2", health=health), p1=Wizard("P1", health=health))

    assert await cast(shipped_dsl(support_id), fight) == Turn("Storm Trap", "Boss")


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_buff_support_never_shields(support_id: str):
    assert (await cast(shipped_dsl(support_id), as_p2(TOWER_SHIELD, SIRENS))).passed


# ---------------------------------------------------------------------------
# Prisms (#274)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_casts_a_prism_on_the_boss(support_id: str):
    assert await cast(shipped_dsl(support_id), as_p2(FIRE_PRISM, SIRENS)) == Turn("Fire Prism", "Boss")


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_casts_a_mass_prism_in_a_mob_fight(support_id: str):
    fight = as_p2(MASS_FIRE_PRISM, SIRENS, enemies=[MOB, MOB])

    assert (await cast(shipped_dsl(support_id), fight)).card == "Mass Fire Prism"


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_casts_a_prism_after_the_global_and_before_attenuate(support_id: str):
    assert (await cast(shipped_dsl(support_id), as_p2(FIRE_PRISM, WYLDFIRE))).card == "Wyldfire"
    assert (await cast(shipped_dsl(support_id), as_p2(ATTENUATE, FIRE_PRISM))).card == "Fire Prism"


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_casts_a_trap_before_a_prism(support_id: str):
    assert (await cast(shipped_dsl(support_id), as_p2(FIRE_PRISM, STORM_TRAP))).card == "Storm Trap"


# ---------------------------------------------------------------------------
# A negative aura on the boss
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_with_only_attenuate_casts_it_on_the_boss(support_id: str):
    assert await cast(shipped_dsl(support_id), as_p2(ATTENUATE, SIRENS)) == Turn("Attenuate", "Boss")


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_casts_the_global_before_attenuate(support_id: str):
    assert await cast(shipped_dsl(support_id), as_p2(ATTENUATE, WYLDFIRE)) == Turn("Wyldfire", None)


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_casts_a_trap_before_attenuate(support_id: str):
    assert await cast(shipped_dsl(support_id), as_p2(ATTENUATE, STORM_TRAP)) == Turn("Storm Trap", "Boss")


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
async def test_a_support_passes_when_a_stronger_attenuate_is_up(support_id: str):
    boss = Enemy("Boss", boss=True, hanging=[TYPHOON_ATTENUATE.hanging()])

    assert (await cast(shipped_dsl(support_id), as_p2(ATTENUATE, SIRENS, enemies=[boss]))).passed


async def test_a_support_replaces_attenuate_with_typhoon_attenuate():
    boss = Enemy("Boss", boss=True, hanging=[ATTENUATE.hanging()])
    fight = as_p2(ATTENUATE, TYPHOON_ATTENUATE, enemies=[boss])

    assert await cast(support_dsl(), fight) == Turn("Typhoon Attenuate", "Boss")


async def test_a_support_casts_no_attenuate_in_a_mob_fight():
    """It aims at the boss, so a mob fight with no boss passes it by."""
    assert (await cast(support_dsl(), as_p2(ATTENUATE, enemies=[MOB, MOB]))).passed


# ---------------------------------------------------------------------------
# The cheat-boss supports
# ---------------------------------------------------------------------------


async def test_the_no_blades_support_feints_the_boss():
    turn = await cast(shipped_dsl(NO_BLADES_ID), as_p2(STORM_BLADE, SHARPENED_BLADE, FEINT, POTENT_TRAP))

    assert turn == Turn("Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),))


async def test_the_no_blades_support_passes_holding_only_a_blade():
    assert (await cast(shipped_dsl(NO_BLADES_ID), as_p2(STORM_BLADE, SHARPENED_BLADE, SIRENS))).passed


async def test_the_no_feints_support_blades_p1():
    turn = await cast(shipped_dsl(NO_FEINTS_ID), as_p2(FEINT, MASS_FEINT, STORM_BLADE, SHARPENED_BLADE))

    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade", fuses=(("Sharpened Blade", "Storm Blade"),))


async def test_the_no_feints_support_traps_the_boss_with_anything_but_a_feint():
    turn = await cast(shipped_dsl(NO_FEINTS_ID), as_p2(FEINT, STORM_TRAP, POTENT_TRAP))

    assert turn == Turn("Storm Trap", "Boss", "Potent Trap", fuses=(("Potent Trap", "Storm Trap"),))


_POOL = [
    STORM_BLADE,
    BALANCEBLADE,
    SHARPENED_BLADE,
    FEINT,
    MASS_FEINT,
    POTENT_TRAP,
    STORM_TRAP,
    MASS_HEX,
    AMPLIFY,
    WYLDFIRE,
    FAIRY,
    SIRENS,
    STORM_SHARK,
    EPIC,
]


def _sweep() -> list[Fight]:
    """Every situation the supports read, over a fixed sample of hands."""
    rng = random.Random(221)
    hands = [rng.sample(_POOL, rng.randint(1, 7)) for _ in range(40)] + [_POOL[:7], _POOL[7:]]
    hung_boss = Enemy("Boss", boss=True, hanging=[FEINT.hanging(POTENT_TRAP), STORM_TRAP.hanging()])
    bladed_p1 = Wizard("P1", hanging=[STORM_BLADE.hanging(SHARPENED_BLADE), BALANCEBLADE.hanging()])
    situations = [
        {"enemies": [BOSS], "round": 1},
        {"enemies": [MOB, BOSS], "round": 2},
        {"enemies": [hung_boss], "round": 3, "p1": bladed_p1},
        {"enemies": [MOB, MOB], "round": 1},
        {"enemies": [MOB], "round": 2},
        {"enemies": [BOSS], "round": 2, "p1": Wizard("P1").dead},
        {"enemies": [BOSS], "round": 2, "me": Wizard("P2", health=500), "p1": Wizard("P1", health=500)},
    ]
    return [as_p2(*hand, **situation) for hand in hands for situation in situations]


_BLADES = {STORM_BLADE.name, BALANCEBLADE.name}


async def test_the_no_blades_support_never_casts_or_fuses_a_blade():
    for fight in _sweep():
        turn = await cast(shipped_dsl(NO_BLADES_ID), fight)

        assert turn.card not in _BLADES, (turn, fight.hand)
        assert turn.enchant != SHARPENED_BLADE.name, (turn, fight.hand)
        assert all(enchant != SHARPENED_BLADE.name and card not in _BLADES for enchant, card in turn.fuses)


async def test_the_no_feints_support_never_casts_or_fuses_into_a_feint():
    for fight in _sweep():
        turn = await cast(shipped_dsl(NO_FEINTS_ID), fight)

        assert "Feint" not in (turn.card or ""), (turn, fight.hand)
        assert all("Feint" not in card for _enchant, card in turn.fuses), (turn, fight.hand)


# ---------------------------------------------------------------------------
# The text
# ---------------------------------------------------------------------------


def _round_lines(dsl: str):
    config = TreeToConfig().transform(Lark(get_sprinty_grammar()).parse(dsl))
    return list(config.specific_rounds.values()) + list(config.infinite_rounds)


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
def test_every_support_parses_and_enchants_ahead_first_on_every_line(support_id: str):
    dsl = shipped_dsl(support_id)
    assert validate_dsl(dsl) is None

    for line in _round_lines(dsl):
        held = [is_enchant_only(p) for p in line.priorities]
        assert held[0] and held.count(True) == 1


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
def test_no_support_reads_the_boss_by_its_health(support_id: str):
    dsl = shipped_dsl(support_id)
    assert "boss.health" not in dsl
    assert "boss.hp" not in dsl


@pytest.mark.parametrize("support_id", SUPPORT_IDS)
def test_no_support_reads_is_dead(support_id: str):
    """`is_dead` reads the game stats' hit points, which wizsprinter's own targeting
    distrusts; `health` reads the participant's, so P1's death is `ally(last).health == 0`."""
    dsl = shipped_dsl(support_id)
    assert "is_dead" not in dsl
    assert "?(ally(last).health == 0)" in dsl


def test_only_default_support_has_a_round_1_line():
    """The cheat-boss duels assign only boss fights, which have no round-1 AoE."""
    assert "{1}" in shipped_dsl(SHIPPED_DEFAULT_SUPPORT_ID)
    assert "{1}" not in shipped_dsl(NO_BLADES_ID)
    assert "{1}" not in shipped_dsl(NO_FEINTS_ID)
