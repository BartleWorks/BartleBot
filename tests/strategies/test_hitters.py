"""The shipped hitters (Stage 4 of #218): Default, Default Team Hitter, and the AoE and
single-target hitters, walked through described fights.

A hitter heals itself below 30% health, buffs while a buff adds to its next hit -- Mass
Feint, Feint on the boss, a blade on itself, an aura while none is up -- and hits once
none can cast and the pips allow: AoE, or single-target at the boss. Short of pips it
casts a filler or a buff that won't stack, or passes, and keeps its buffs. Default Team
Hitter opens a mob fight with AoE in round 1. ``docs/COMBAT_CONFIG.md``'s "What the hitter
does" is the rule these pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import TreeToConfig, get_sprinty_grammar
from wizwalker.extensions.wizsprinter.sprinty_combat import is_enchant_only

from bartlebot.nodes.combat_runner import validate_dsl
from bartlebot.shared.combat_defaults import (
    SHIPPED_DEFAULT_PLAYER_ID,
    SHIPPED_DEFAULT_SUPPORT_ID,
    SHIPPED_TEAM_HITTER_ID,
)
from tests.strategies.fight import (
    AMPLIFY,
    BALANCEBLADE,
    EPIC,
    FAIRY,
    FEINT,
    KRAKEN,
    MASS_FEINT,
    MASS_HEX,
    POTENT_TRAP,
    SHADOW_SHRIKE,
    SHARPENED_BLADE,
    SIRENS,
    STORM_BLADE,
    STORM_SHARK,
    STORM_TRAP,
    WYLDFIRE,
    Enemy,
    Fight,
    Turn,
    Wizard,
    cast,
    shipped_dsl,
    team,
)

AOE_HITTER_ID = "615bbd5f-eb76-4f47-b0da-7e16de8a7b33"
SINGLE_TARGET_HITTER_ID = "d99506cc-6276-4e7d-8ae0-11a69788fcd6"
HITTER_IDS = [SHIPPED_DEFAULT_PLAYER_ID, SHIPPED_TEAM_HITTER_ID, AOE_HITTER_ID, SINGLE_TARGET_HITTER_ID]

MOB = Enemy("Mob")
BOSS = Enemy("Boss", boss=True)


def default_dsl() -> str:
    return shipped_dsl(SHIPPED_DEFAULT_PLAYER_ID)


def solo(*hand, enemies=(MOB, MOB), round=2, me: Wizard | None = None, **kwargs) -> Fight:
    """One wizard alone."""
    return Fight(team=[me or Wizard("P1")], enemies=list(enemies), hand=list(hand), round=round, **kwargs)


def as_p1(*hand, size=2, enemies=(MOB, MOB), round=1, **kwargs) -> Fight:
    """P1 of a team duel, in the last seat."""
    return Fight(team=team(size), enemies=list(enemies), hand=list(hand), round=round, **kwargs)


# ---------------------------------------------------------------------------
# Solo: buff first, in every fight
# ---------------------------------------------------------------------------


async def test_solo_round_1_of_a_mob_fight_buffs():
    turn = await cast(default_dsl(), solo(STORM_BLADE, SHARPENED_BLADE, SIRENS, round=1))

    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade", fuses=(("Sharpened Blade", "Storm Blade"),))


async def test_solo_on_a_boss_casts_mass_feint_first():
    turn = await cast(default_dsl(), solo(STORM_BLADE, FEINT, MASS_FEINT, SIRENS, enemies=[BOSS]))

    assert turn.card == "Feint Mass - Amulet"


async def test_solo_on_a_boss_feints_the_boss_before_a_blade():
    turn = await cast(default_dsl(), solo(STORM_BLADE, FEINT, POTENT_TRAP, SIRENS, enemies=[MOB, BOSS]))

    assert turn == Turn("Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),))


async def test_the_feint_line_ranks_a_blade_above_mass_hex():
    """`any<trap & !aoe & stacks>`, the line Stage 3 settled on, keeps Mass Hex out."""
    assert (await cast(default_dsl(), solo(MASS_HEX, STORM_BLADE, SIRENS, enemies=[BOSS]))).card == "Storm Blade"


async def test_no_trap_is_aimed_at_a_mob():
    """Today's `@ enemy` trap could land on a minion; the trap line aims at the boss."""
    assert (await cast(default_dsl(), solo(FEINT, STORM_TRAP, SIRENS))).card == "Sirens"


async def test_below_30_percent_it_heals_itself():
    fight = solo(FAIRY, STORM_BLADE, SIRENS, me=Wizard("P1", health=899))

    assert await cast(default_dsl(), fight) == Turn("Fairy", "P1")


async def test_at_30_percent_it_does_not_heal():
    fight = solo(FAIRY, STORM_BLADE, SIRENS, me=Wizard("P1", health=900))

    assert (await cast(default_dsl(), fight)).card == "Storm Blade"


# ---------------------------------------------------------------------------
# It keeps buffing while a buff adds to the next hit
# ---------------------------------------------------------------------------


async def test_a_blade_that_stacks_is_cast_with_a_blade_already_up():
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    turn = await cast(default_dsl(), solo(BALANCEBLADE, SIRENS, me=me))

    assert turn.card == "Balanceblade"


async def test_a_sharpened_blade_stacks_on_a_plain_one():
    """The line's own `[sharp]` counts, so the same blade enchanted adds to the hit."""
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    turn = await cast(default_dsl(), solo(STORM_BLADE, SHARPENED_BLADE, SIRENS, me=me))

    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade", fuses=(("Sharpened Blade", "Storm Blade"),))


async def test_a_feint_already_on_the_boss_is_not_cast_again_before_the_hit():
    boss = Enemy("Boss", boss=True, hanging=[FEINT.hanging()])

    assert (await cast(default_dsl(), solo(FEINT, SIRENS, enemies=[boss]))).card == "Sirens"


async def test_the_aura_is_cast_while_none_is_up():
    assert (await cast(default_dsl(), solo(AMPLIFY, SIRENS))).card == "Amplify"


async def test_the_aura_is_not_cast_while_one_is_up():
    me = Wizard("P1", hanging=[AMPLIFY.hanging()])

    assert (await cast(default_dsl(), solo(AMPLIFY, SIRENS, me=me))).card == "Sirens"


# ---------------------------------------------------------------------------
# The hit, and what it does short of pips
# ---------------------------------------------------------------------------


async def test_with_no_buff_that_stacks_and_the_pips_it_casts_aoe():
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    turn = await cast(default_dsl(), solo(STORM_BLADE, SIRENS, STORM_SHARK, EPIC, me=me))

    assert turn == Turn("Sirens", None, "Epic", fuses=(("Epic", "Sirens"),))


@pytest.mark.parametrize("enemies", [[MOB], [MOB, MOB]], ids=["one-mob", "mobs"])
async def test_short_of_pips_for_aoe_in_a_mob_fight_it_never_hits_one_mob(enemies):
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    turn = await cast(default_dsl(), solo(SIRENS, STORM_SHARK, KRAKEN, me=me, enemies=enemies, pips=3))

    assert turn.passed


async def test_short_of_pips_for_aoe_it_casts_a_filler():
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    assert (await cast(default_dsl(), solo(SIRENS, STORM_SHARK, WYLDFIRE, me=me, pips=3))).card == "Wyldfire"


async def test_a_shadow_spell_is_a_filler():
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    assert (await cast(default_dsl(), solo(SIRENS, SHADOW_SHRIKE, me=me, pips=3))).card == "Shadow Shrike"


async def test_short_of_pips_for_aoe_it_casts_a_buff_that_wont_stack():
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    assert await cast(default_dsl(), solo(SIRENS, STORM_BLADE, me=me, pips=3)) == Turn("Storm Blade", "P1")


async def test_short_of_pips_for_aoe_on_a_boss_it_hits_the_boss():
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    turn = await cast(default_dsl(), solo(SIRENS, KRAKEN, me=me, enemies=[MOB, BOSS], pips=3))

    assert turn == Turn("Kraken", "Boss")


async def test_a_boss_fight_with_the_pips_casts_aoe_before_a_single_hit():
    assert (await cast(default_dsl(), solo(KRAKEN, SIRENS, enemies=[MOB, BOSS]))).card == "Sirens"


async def test_holding_nothing_castable_it_passes():
    assert (await cast(default_dsl(), solo(SIRENS, pips=2))).passed


# ---------------------------------------------------------------------------
# Default Team Hitter: round 1 of a team mob fight
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [2, 4])
async def test_the_team_hitter_casts_aoe_in_round_1_of_a_mob_fight(size: int):
    fight = as_p1(STORM_BLADE, SHARPENED_BLADE, FEINT, MASS_FEINT, SIRENS, size=size)

    assert (await cast(shipped_dsl(SHIPPED_TEAM_HITTER_ID), fight)) == Turn("Sirens", None)


async def test_solo_round_1_default_does_not_cast_aoe():
    turn = await cast(default_dsl(), solo(STORM_BLADE, SHARPENED_BLADE, FEINT, MASS_FEINT, SIRENS, round=1))

    assert turn.card != "Sirens"


async def test_the_team_hitter_buffs_in_round_1_of_a_boss_fight():
    """A `{1}` line is the whole of round 1, so it carries the ladder after its AoE."""
    fight = as_p1(FEINT, POTENT_TRAP, SIRENS, enemies=[MOB, BOSS])

    assert await cast(shipped_dsl(SHIPPED_TEAM_HITTER_ID), fight) == Turn(
        "Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),)
    )


async def test_the_team_hitter_buffs_after_round_1_of_a_mob_fight():
    fight = as_p1(STORM_BLADE, SIRENS, round=2)

    assert (await cast(shipped_dsl(SHIPPED_TEAM_HITTER_ID), fight)) == Turn("Storm Blade", "P1")


async def test_the_team_hitter_heals_before_its_round_1_aoe():
    p2, _p1 = team(2)
    fight = Fight(team=[p2, Wizard("P1", health=500)], enemies=[MOB, MOB], hand=[FAIRY, SIRENS], round=1)

    assert await cast(shipped_dsl(SHIPPED_TEAM_HITTER_ID), fight) == Turn("Fairy", "P1")


# ---------------------------------------------------------------------------
# The AoE and single-target hitters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hitter_id", HITTER_IDS)
async def test_every_hitter_heals_itself_below_30_percent(hitter_id: str):
    fight = solo(FAIRY, STORM_BLADE, SIRENS, me=Wizard("P1", health=899), enemies=[BOSS])

    assert await cast(shipped_dsl(hitter_id), fight) == Turn("Fairy", "P1")


@pytest.mark.parametrize("hitter_id", HITTER_IDS)
async def test_every_hitter_casts_mass_feint_first(hitter_id: str):
    fight = solo(STORM_BLADE, FEINT, MASS_FEINT, SIRENS, KRAKEN, enemies=[BOSS])

    assert (await cast(shipped_dsl(hitter_id), fight)).card == "Feint Mass - Amulet"


@pytest.mark.parametrize("hitter_id", HITTER_IDS)
async def test_every_hitter_casts_a_blade_that_stacks_before_its_hit(hitter_id: str):
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    turn = await cast(shipped_dsl(hitter_id), solo(BALANCEBLADE, SIRENS, KRAKEN, me=me, enemies=[BOSS]))

    assert turn.card == "Balanceblade"


async def test_the_aoe_hitter_hits_once_its_blade_wont_stack():
    me = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    turn = await cast(shipped_dsl(AOE_HITTER_ID), solo(STORM_BLADE, SIRENS, KRAKEN, me=me, enemies=[BOSS]))

    assert turn.card == "Sirens"


async def test_the_single_target_hitter_feints_the_boss_then_hits_it():
    dsl = shipped_dsl(SINGLE_TARGET_HITTER_ID)
    feinted = Enemy("Boss", boss=True, hanging=[FEINT.hanging()])

    assert (await cast(dsl, solo(FEINT, SIRENS, KRAKEN, enemies=[MOB, BOSS]))) == Turn("Feint", "Boss")
    assert (await cast(dsl, solo(FEINT, SIRENS, KRAKEN, enemies=[MOB, feinted]))) == Turn("Kraken", "Boss")


# ---------------------------------------------------------------------------
# Every card kind has a line
# ---------------------------------------------------------------------------


_BLADED = Wizard("P1", hanging=[STORM_BLADE.hanging()])

#: For each card kind in goal 3 of #218, a shipped strategy and a fight in which that
#: card is the right one, and the card it casts.
_KINDS = {
    "mass feint": (SHIPPED_DEFAULT_PLAYER_ID, solo(MASS_FEINT, SIRENS, enemies=[BOSS]), "Feint Mass - Amulet"),
    "feint": (SHIPPED_DEFAULT_PLAYER_ID, solo(FEINT, SIRENS, enemies=[BOSS]), "Feint"),
    "other trap": (SHIPPED_DEFAULT_SUPPORT_ID, Fight(team(2), [MOB, MOB], [MASS_HEX], me="P2", round=2), "Mass Hex"),
    "blade": (SHIPPED_DEFAULT_PLAYER_ID, solo(STORM_BLADE, SIRENS), "Storm Blade"),
    "aura": (SHIPPED_DEFAULT_PLAYER_ID, solo(AMPLIFY, SIRENS), "Amplify"),
    "global": (SHIPPED_DEFAULT_PLAYER_ID, solo(WYLDFIRE, SIRENS, me=_BLADED, pips=3), "Wyldfire"),
    "shadow": (SHIPPED_DEFAULT_PLAYER_ID, solo(SHADOW_SHRIKE, SIRENS, me=_BLADED, pips=3), "Shadow Shrike"),
    "heal": (SHIPPED_DEFAULT_PLAYER_ID, solo(FAIRY, SIRENS, me=Wizard("P1", health=500)), "Fairy"),
    "aoe": (SHIPPED_DEFAULT_PLAYER_ID, solo(SIRENS, KRAKEN), "Sirens"),
    "single-target": (SHIPPED_DEFAULT_PLAYER_ID, solo(KRAKEN, enemies=[BOSS]), "Kraken"),
}


@pytest.mark.parametrize("kind", list(_KINDS))
async def test_every_card_kind_is_cast_where_it_is_the_right_card(kind: str):
    strategy_id, fight, card = _KINDS[kind]

    assert (await cast(shipped_dsl(strategy_id), fight)).card == card


#: What a line for each card kind in goal 3 reads like, in some shipped player strategy.
_KIND_TEXT = {
    "mass feint": '"Feint Mass - Amulet"',
    "feint": "any<trap & !aoe & stacks>[potent] @ boss",
    "other trap": "any<trap & aoe",
    "blade": "any<blade",
    "aura": "any<aura",
    "global": "any<global>",
    "shadow": "any<shadow>",
    "heal": "any<heal>",
    "aoe": "any<damage & aoe>",
    "single-target": "any<damage>[any<mod_damage>] @ boss",
}

_PLAYERS = Path(__file__).resolve().parents[2] / "strategies" / "player"


@pytest.mark.parametrize("kind", list(_KIND_TEXT))
def test_the_shipped_player_strategies_have_a_line_for_every_card_kind(kind: str):
    dsls = [json.loads(p.read_text(encoding="utf-8"))["dsl"] for p in sorted(_PLAYERS.glob("*.json"))]

    assert any(_KIND_TEXT[kind] in dsl for dsl in dsls), kind


# ---------------------------------------------------------------------------
# The text
# ---------------------------------------------------------------------------


def _round_lines(dsl: str):
    config = TreeToConfig().transform(Lark(get_sprinty_grammar()).parse(dsl))
    return list(config.specific_rounds.values()) + list(config.infinite_rounds)


@pytest.mark.parametrize("hitter_id", HITTER_IDS)
def test_every_hitter_parses_and_enchants_ahead_first_on_every_line(hitter_id: str):
    dsl = shipped_dsl(hitter_id)
    assert validate_dsl(dsl) is None

    for line in _round_lines(dsl):
        held = [is_enchant_only(p) for p in line.priorities]
        assert held[0] and held.count(True) == 1


@pytest.mark.parametrize("hitter_id", HITTER_IDS)
def test_no_hitter_reads_the_boss_by_its_health(hitter_id: str):
    dsl = shipped_dsl(hitter_id)
    assert "boss.health" not in dsl
    assert "boss.hp" not in dsl


def test_only_the_team_hitter_has_a_round_1_line():
    """The round-1 AoE belongs to team fights; a solo wizard buffs first."""
    assert "{1}" in shipped_dsl(SHIPPED_TEAM_HITTER_ID)
    for hitter_id in (SHIPPED_DEFAULT_PLAYER_ID, AOE_HITTER_ID, SINGLE_TARGET_HITTER_ID):
        assert "{1}" not in shipped_dsl(hitter_id)


async def test_default_never_fuses_an_enchanted_card_onto_another():
    """The enchant-ahead slot is written twice; wizsprinter `e04c2e0` keeps the second
    lookup to enchants (Stage 4 of #218)."""
    turn = await cast(default_dsl(), solo(FEINT, STORM_TRAP, POTENT_TRAP, enemies=[BOSS]))

    assert turn == Turn("Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),))
