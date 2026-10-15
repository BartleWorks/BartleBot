"""The turn trace: what each round decided and why, in the session log.

Lives in wizsprinter (`sprinty_combat.turn_logger`), which is pinned by exact commit here
and has no suite of its own, so this is where it gets covered -- the same arrangement as
`test_held_enchants.py`.

A fight's choices leave no other record: before the trace, a support that clicked a hit
and never cast it showed in the log only as a fight that finished. So the trace records
the hand each round began with, every priority walked and why a card was passed over,
every fuse with the hand on either side, which card was then cast and how it was found,
and at which slot. A combatant is named by its slot, never by its name.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter import sprinty_combat
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import SprintyCombat

from bartlebot.logging_setup import TURN_TRACE_LOGGER
from tests.strategies.fight import (
    _INSTANT,
    SHARPENED_BLADE,
    STORM_BLADE,
    STORM_SHARK,
    Enemy,
    Fight,
    Hung,
    Wizard,
    cast,
)
from tests.wizsprinter.fakes import Card, Member, fake_combat

_SUPPORT_BLADE = "any<blade & stacks>[sharp] @ ally(last) | pass"


@pytest.fixture(scope="module")
def parse():
    """One grammar per module: Lark costs ~77 ms to build."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        return TreeToConfig().transform(parser.parse(dsl)).infinite_rounds[0].priorities

    return _parse


@pytest.fixture
def trace(caplog):
    """The turn trace's messages, in order, as they are written."""
    caplog.set_level(logging.DEBUG, logger=TURN_TRACE_LOGGER)

    def _messages() -> list[str]:
        return [r.getMessage().strip() for r in caplog.records if r.name == TURN_TRACE_LOGGER]

    return _messages


def _support_fight(*hand, p1=Wizard("P1")) -> Fight:
    """P2 supporting P1, who sits last, against a boss."""
    return Fight(team=(Wizard("P2"), p1), enemies=(Enemy(boss=True),), hand=hand, me="P2", round=2)


def _keep(messages: list[str], *starts: str) -> list[str]:
    return [m for m in messages if m.startswith(starts)]


# ---------------------------------------------------------------------------
# A round, from the hand to the cast
# ---------------------------------------------------------------------------


async def test_a_round_is_traced_from_the_hand_to_the_cast(trace):
    turn = await cast(_SUPPORT_BLADE, _support_fight(STORM_BLADE, SHARPENED_BLADE, STORM_SHARK))
    assert str(turn) == "Storm Blade[Sharpened Blade] @ P1"

    messages = trace()
    assert messages[0] == (
        "round 2, the line for relative round 1; deck.remaining 20, hand 3 of None: "
        "1: Storm Blade (template 2001) | 2: Sharpened Blade (template 1001) | 3: Storm Shark (template 4001)"
    )
    assert _keep(messages, "#", "picked", "fuse", "fused", "names", "to cast", "cast") == [
        "#1:",
        "picked Storm Blade (template 2001) (slot 1) for team slot 2",
        "fuse Sharpened Blade (template 1001) (slot 2) onto Storm Blade (template 2001) (slot 1), 3 cards in hand",
        "fused after 1 click(s): 2 cards in hand",
        "fused the move's enchant. Hand before: 1: Storm Blade (template 2001) | "
        "2: Sharpened Blade (template 1001) | 3: Storm Shark (template 4001)",
        "names new to the hand: none",
        "to cast: Storm Blade (template 2001, enchant 1001) (slot 1), the fused card, found by its template and enchant",
        "cast Storm Blade (template 2001, enchant 1001) (slot 1) at team slot 2",
        "#1 ended the turn",
    ]


async def test_a_card_passed_over_says_why(trace):
    """P1 already holds a plain Storm Blade, so a second one adds nothing."""
    turn = await cast(
        "any<blade & stacks> @ ally(last) | pass",
        _support_fight(STORM_BLADE, p1=Wizard("P1", hanging=[Hung(STORM_BLADE)])),
    )
    assert turn.passed

    assert trace()[1:] == [
        "#1:",
        "skip Storm Blade (template 2001): it would not stack on team slot 2",
        "#2:",
        "pass",
        "#2 ended the turn",
    ]


async def test_a_false_condition_is_named(trace):
    await cast("?(self.health < 30%) any<blade> @ self | pass", _support_fight(STORM_BLADE))

    assert trace()[1:3] == [
        "#1:",
        "condition false: Condition(ConditionTarget(target_type=TargetType.type_self, index=None).health < 30.0%)",
    ]


async def test_held_enchants_are_traced_when_they_run(trace):
    await cast(
        "sharp @ spell(any<blade>) | any<blade> @ ally(last) | pass",
        _support_fight(STORM_BLADE, SHARPENED_BLADE),
    )

    assert _keep(trace(), "#1", "it does", "held", "enchant ") == [
        "#1: enchant only; held",
        "it does not attack: running 1 held enchant slot(s), then walking again",
        "held enchant slot 1 of 1:",
        "enchant Sharpened Blade (template 1001) onto Storm Blade (template 2001)",
        "held enchants run; walking again. Hand: 1: Storm Blade (template 2001, enchant 1001)",
        "#1: enchant only; already run this round",
    ]


# ---------------------------------------------------------------------------
# Where the card to cast comes from after a fuse
# ---------------------------------------------------------------------------


class _Renamed(Card):
    """A card a fuse renames, as wizsprinter once took every fuse to."""

    def fuse(self, enchant: Card) -> None:
        super().fuse(enchant)
        self._name = f"{self.base_name} [{enchant.base_name}]"
        self.template_id_ += 1


async def test_a_fused_card_that_is_renamed_is_found_by_its_new_name(parse, trace):
    """Its template moved with its name, so only the names still find it."""
    from tests.wizsprinter.test_stacking import _BLADE, _ENCHANT, STORMBLADE
    from tests.wizsprinter.test_stacking import SHARPENED_BLADE as SHARP_ID

    hand: list = []
    blade = _Renamed("Storm Blade", _BLADE, hand, template_id=STORMBLADE)
    Card("Sharpened Blade", _ENCHANT, hand, template_id=SHARP_ID)
    combat = fake_combat(hand, team=[Member("P2"), Member("P1")])

    with patch.object(sprinty_combat, "asyncio", _INSTANT):
        assert await combat.try_execute_config(parse(_SUPPORT_BLADE)[0]) is True

    assert len(blade.casts) == 1 and not isinstance(blade.casts[0], Card)
    assert _keep(trace(), "names", "to cast") == [
        "names new to the hand: ['Storm Blade [Sharpened Blade]']",
        f"to cast: Storm Blade [Sharpened Blade] (template {STORMBLADE + 1}, enchant {SHARP_ID}) (slot 1), "
        "the fused card, found by its new name 'Storm Blade [Sharpened Blade]'",
    ]


# ---------------------------------------------------------------------------
# Slots, tags, and staying out of the way
# ---------------------------------------------------------------------------


async def test_a_combatant_is_named_by_its_slot_never_its_name(trace):
    """Wizard names are synthetic, from tools/name_key_samples.json."""
    fight = Fight(
        team=(Wizard("Luke SkullHunter"), Wizard("Luke CycloneHunter")),
        enemies=(Enemy("Rattlebones", boss=True),),
        hand=(STORM_BLADE, STORM_SHARK),
        me="Luke SkullHunter",
        round=2,
    )
    await cast("any<blade> @ ally(last) | pass", fight)
    await cast("any<damage> @ boss | pass", fight)
    await cast("any<blade> @ self | pass", fight)

    text = "\n".join(trace())
    assert "at team slot 2" in text
    assert "at enemy slot 1 (boss)" in text
    assert "at self" in text
    assert "Luke" not in text and "Rattlebones" not in text


def test_the_tag_starts_every_line(caplog):
    caplog.set_level(logging.DEBUG, logger=TURN_TRACE_LOGGER)
    combat = SprintyCombat(SimpleNamespace(), SimpleNamespace(cast_time=0), log_tag="[P2]")

    combat.trace("round %d", 3)

    assert [r.getMessage() for r in caplog.records] == ["[P2] round 3"]


async def test_the_trace_is_silent_unless_enabled(caplog):
    caplog.set_level(logging.INFO, logger=TURN_TRACE_LOGGER)

    await cast(_SUPPORT_BLADE, _support_fight(STORM_BLADE, SHARPENED_BLADE))

    assert not [r for r in caplog.records if r.name == TURN_TRACE_LOGGER]
    assert SprintyCombat(SimpleNamespace(), SimpleNamespace(cast_time=0)).tracing is False


async def test_an_unreadable_card_or_combatant_is_described_without_raising():
    class _Unreadable:
        async def name(self):
            raise ValueError("Graphical spell not found; probably reading too fast")

        async def get_participant(self):
            raise ValueError("gone")

    combat = fake_combat([], team=[Member("P2"), Member("P1")])

    assert await SprintyCombat.describe_card(_Unreadable()) == "<unreadable card>"
    assert await combat.describe_target(_Unreadable()) == "<unreadable combatant>"
