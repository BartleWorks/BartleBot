"""The fight harness: one round of a described fight through the real SprintyCombat."""

from __future__ import annotations

import time

from bartlebot.shared.combat_defaults import SHIPPED_DEFAULT_PLAYER_ID
from tests.strategies.fight import (
    BALANCEBLADE,
    EPIC,
    FAIRY,
    FEINT,
    POTENT_TRAP,
    SHARPENED_BLADE,
    SIRENS,
    STORM_BLADE,
    STORM_LORD,
    STORM_SHARK,
    STORM_TRAP,
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


def solo(*hand, **kwargs) -> Fight:
    return Fight(team=team(1), enemies=kwargs.pop("enemies", [MOB]), hand=list(hand), **kwargs)


# ---------------------------------------------------------------------------
# What a round casts
# ---------------------------------------------------------------------------


async def test_a_plain_cast():
    turn = await cast('"Storm Shark" @ enemy', solo(STORM_SHARK))

    assert turn == Turn("Storm Shark", "Mob")


async def test_an_enchanted_cast():
    turn = await cast("any<damage>[epic] @ enemy", solo(STORM_SHARK, EPIC))

    assert turn == Turn("Storm Shark", "Mob", "Epic", fuses=(("Epic", "Storm Shark"),))


async def test_a_card_enchanted_before_the_round_reports_its_enchant():
    turn = await cast("any<blade> @ self", solo(STORM_BLADE.enchanted(SHARPENED_BLADE)))

    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade")


async def test_a_pass():
    """Nothing in hand matches, so the walk ends in a pass."""
    turn = await cast("any<blade> @ self", solo(STORM_SHARK))

    assert turn.passed
    assert str(turn) == "pass"


async def test_an_explicit_pass():
    turn = await cast("pass | any<damage> @ enemy", solo(STORM_SHARK))

    assert turn.passed


async def test_a_card_my_pips_cannot_pay_for_is_skipped():
    turn = await cast("any<damage & aoe> | any<damage> @ enemy", solo(STORM_LORD, STORM_SHARK, pips=4))

    assert turn.card == "Storm Shark"


async def test_power_pips_count_twice():
    turn = await cast("any<damage & aoe> | any<damage> @ enemy", solo(STORM_LORD, STORM_SHARK, pips=1, power_pips=3))

    assert turn.card == "Storm Lord"


async def test_a_stunned_wizard_passes():
    fight = Fight(team=[Wizard("P1", stunned=True)], enemies=[MOB], hand=[STORM_SHARK])

    assert (await cast("any<damage> @ enemy", fight)).passed


# ---------------------------------------------------------------------------
# Held enchant-ahead slots
# ---------------------------------------------------------------------------

_AHEAD = "?(deck.remaining >= 2) sharp @ spell(any<blade>)"


async def test_a_held_enchant_runs_before_a_non_attack():
    turn = await cast(f"{_AHEAD} | any<blade> @ self", solo(STORM_BLADE, SHARPENED_BLADE))

    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade", fuses=(("Sharpened Blade", "Storm Blade"),))


async def test_a_held_enchant_waits_out_an_attack():
    turn = await cast(f"{_AHEAD} | any<damage> @ enemy", solo(STORM_SHARK, STORM_BLADE, SHARPENED_BLADE))

    assert turn == Turn("Storm Shark", "Mob")


async def test_a_held_enchant_needs_its_condition():
    turn = await cast(f"{_AHEAD} | any<blade> @ self", solo(STORM_BLADE, SHARPENED_BLADE, deck=1))

    assert turn == Turn("Storm Blade", "P1")


async def test_a_slot_written_twice_never_fuses_an_enchanted_card():
    """Stage 3 of #218 found it: after the first `potent` fuses, the second's vague name
    also matched `"Feint [Potent Trap]"` and cast it onto the Storm Trap. wizsprinter
    `e04c2e0` looks up a card aimed at a spell among enchants only."""
    twice = "?(deck.remaining >= 2) potent @ spell(any<trap>) & potent @ spell(any<trap>) | any<trap> @ boss"

    turn = await cast(twice, solo(FEINT, STORM_TRAP, POTENT_TRAP, enemies=[BOSS]))

    assert turn == Turn("Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),))


async def test_a_blade_slot_written_twice_never_fuses_an_enchanted_blade():
    twice = f"{_AHEAD} & sharp @ spell(any<blade>) | any<blade> @ self"

    turn = await cast(twice, solo(STORM_BLADE, BALANCEBLADE, SHARPENED_BLADE))

    assert turn.fuses == (("Sharpened Blade", "Storm Blade"),)


# ---------------------------------------------------------------------------
# Which round's line
# ---------------------------------------------------------------------------

_OPENER = "{1} any<damage & aoe>\nany<blade> @ self"


async def test_a_round_1_line_runs_in_round_1():
    turn = await cast(_OPENER, solo(STORM_BLADE, STORM_LORD))

    assert turn.card == "Storm Lord"


async def test_a_round_1_line_does_not_run_later():
    for round_ in (2, 3):
        turn = await cast(_OPENER, solo(STORM_BLADE, STORM_LORD, round=round_))

        assert turn.card == "Storm Blade", round_


async def test_a_round_1_line_is_the_whole_round():
    """Round 1 walks only the `{1}` line: nothing on it castable is a pass, not a fall
    through to the repeating line."""
    turn = await cast(_OPENER, solo(STORM_BLADE))

    assert turn.passed


async def test_repeating_lines_take_turns_after_the_round_1_line():
    dsl = '{1} any<damage & aoe>\nany<blade> @ self\n"Storm Shark" @ enemy'
    hand = (STORM_BLADE, STORM_SHARK, STORM_LORD)

    cards = [(await cast(dsl, solo(*hand, round=r))).card for r in (1, 2, 3, 4)]

    assert cards == ["Storm Lord", "Storm Blade", "Storm Shark", "Storm Blade"]


# ---------------------------------------------------------------------------
# The fight described: slots, enemies, health, what hangs
# ---------------------------------------------------------------------------


async def test_ally_last_is_p1_in_the_shipped_slot_order():
    fight = Fight(team=team(4), me="P3", enemies=[BOSS], hand=[STORM_BLADE])

    assert (await cast("any<blade> @ ally(last)", fight)).target == "P1"


async def test_a_boss_fight_and_a_mob_fight_read_apart():
    dsl = "?(any(enemies).is_boss == 1) any<trap> @ boss | any<damage & aoe>"
    hand = (FEINT, SIRENS)

    assert (await cast(dsl, solo(*hand, enemies=[MOB, BOSS]))) == Turn("Feint", "Boss")
    assert (await cast(dsl, solo(*hand, enemies=[MOB, MOB]))) == Turn("Sirens", None)


async def test_an_untargetable_boss_is_not_a_target():
    fight = solo(FEINT, enemies=[Enemy("Head", boss=True, untargetable=True)])

    assert (await cast("any<trap> @ boss", fight)).passed


async def test_health_is_read_as_a_share():
    dsl = "?(self.health < 30%) any<heal> @ self | any<damage> @ enemy"
    hurt = Fight(team=[Wizard("P1", health=800)], enemies=[MOB], hand=[FAIRY, STORM_SHARK])
    well = Fight(team=[Wizard("P1", health=1000)], enemies=[MOB], hand=[FAIRY, STORM_SHARK])

    assert (await cast(dsl, hurt)).card == "Fairy"
    assert (await cast(dsl, well)).card == "Storm Shark"


async def test_a_dead_p1_reads_dead():
    dsl = "?(ally(last).is_dead == 1) any<damage> @ enemy | any<blade> @ ally(last)"
    p2, p1 = team(2)

    alive = Fight(team=[p2, p1], me="P2", enemies=[MOB], hand=[STORM_BLADE, STORM_SHARK])
    dead = Fight(team=[p2, p1.dead], me="P2", enemies=[MOB], hand=[STORM_BLADE, STORM_SHARK])

    assert (await cast(dsl, alive)).card == "Storm Blade"
    assert (await cast(dsl, dead)).card == "Storm Shark"


async def test_what_hangs_decides_stacks():
    """A Potent Feint already on the boss: a plain Feint stacks, a Potent one doesn't."""
    dsl = "any<trap & stacks>[potent] @ boss | any<blade> @ self"
    hand = (FEINT, POTENT_TRAP, STORM_BLADE)

    plain = solo(*hand, enemies=[Enemy("Boss", boss=True, hanging=[FEINT.hanging()])])
    potent = solo(*hand, enemies=[Enemy("Boss", boss=True, hanging=[FEINT.hanging(POTENT_TRAP)])])

    assert (await cast(dsl, plain)) == Turn("Feint", "Boss", "Potent Trap", fuses=(("Potent Trap", "Feint"),))
    assert (await cast(dsl, potent)).card == "Storm Blade"


async def test_a_blade_on_p1_counts_against_another():
    dsl = "any<blade & stacks> @ ally(last) | pass"
    p2, p1 = team(2)
    bladed = Wizard("P1", hanging=[STORM_BLADE.hanging()])

    assert (await cast(dsl, Fight(team=[p2, p1], me="P2", enemies=[MOB], hand=[STORM_BLADE]))).card == "Storm Blade"
    assert (await cast(dsl, Fight(team=[p2, bladed], me="P2", enemies=[MOB], hand=[STORM_BLADE]))).passed


# ---------------------------------------------------------------------------
# Shipped strategies
# ---------------------------------------------------------------------------


#: Default Support as #206 shipped it, before Stage 3 of #218 rewrote it to the support
#: ladder. Kept here so the harness still shows #212's finding.
_SUPPORT_BEFORE_218 = (
    "?(deck.remaining >= 2) any<mod_damage> @ spell(any<damage & aoe>) & any<mod_damage> @ spell(any<damage>)"
    " & sharp @ spell(any<blade>) & sharp @ spell(any<blade>) & potent @ spell(any<trap>) & potent @ spell(any<trap>)"
    ' | ?(any(enemies).is_boss == 1) "Feint Mass - Amulet" | feint[potent] @ boss'
    " | ?(any(enemies).is_boss == 1) any<blade>[sharp] @ ally(last) | any<trap>[potent] @ boss"
    " | ?(any(enemies).is_boss == 1) any<trap>[potent] @ enemy | any<damage & aoe>[any<mod_damage>]"
    " | any<damage>[any<mod_damage>] @ enemy"
)


async def test_default_support_before_218_attacks_a_mob_fight_every_round():
    """#212's finding, which Stage 3 of #218 fixed: on Default Team Duel, P2 attacked the
    mobs in every round instead of buffing P1, though it held a blade and a Feint."""
    p2, p1 = team(2)
    hand = [STORM_BLADE, FEINT, STORM_SHARK, SIRENS]

    for round_ in (1, 2):
        fight = Fight(team=[p2, p1], me="P2", enemies=[MOB, MOB], hand=hand, round=round_)
        assert (await cast(_SUPPORT_BEFORE_218, fight)).card == "Sirens", round_


async def test_a_round_costs_under_50_ms():
    """So a stage can carry dozens of these (tests/CLAUDE.md). Default is the longest
    shipped DSL; the budget is wide of the few ms a round costs, for a loaded runner."""
    dsl = shipped_dsl(SHIPPED_DEFAULT_PLAYER_ID)
    fight = solo(STORM_BLADE, SHARPENED_BLADE, FEINT, POTENT_TRAP, STORM_SHARK, SIRENS, EPIC, enemies=[MOB, BOSS])
    await cast(dsl, fight)  # the parse, which is cached

    start = time.perf_counter()
    for _ in range(10):
        await cast(dsl, fight)

    assert (time.perf_counter() - start) / 10 < 0.05
