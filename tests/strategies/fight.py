"""Walk a strategy's DSL through one round of a described fight, and say what it cast.

A `Fight` describes one planning phase as one client sees it: the team in seat order and
which of them is me, the enemies, the round, my hand and my pips, and what already hangs
on each combatant by spell and enchant id. `cast(dsl, fight)` runs wizsprinter's real
`SprintyCombat.handle_round` over it -- which picks the round's line, `{N}` lines
included, and walks its priorities through the real `try_execute_config` -- against the
fakes in `tests/wizsprinter/fakes.py`, and returns the `Turn`: the card cast, at whom,
with which enchant, the enchants fused into cards in hand on the way, or a pass.

    turn = await cast("any<blade>[sharp] @ ally(last)", Fight(
        team=team(2), me="P2", enemies=[Enemy("Boss", boss=True)], hand=[STORM_BLADE, SHARPENED_BLADE]))
    assert turn == Turn("Storm Blade", "P1", "Sharpened Blade", fuses=(("Sharpened Blade", "Storm Blade"),))

Read a shipped player strategy's DSL with `shipped_dsl(id)`.

The spells below are synthetic: real names, so a DSL that names a card finds it, but
template ids that only need to differ from each other. Their effects are the shape
wizsprinter matches templates against, not the game's exact numbers.

What the harness takes as given: the client has played every round of this fight so far
without a fizzle, so a later round's line is the one wizsprinter would reach; the main
pile holds `Fight.deck` cards, whatever the hand's size; and a card is castable when its
pips fit `pips + 2 * power_pips`. An enchanted card is named `"<spell> [<enchant>]"`, an
assumption about the game noted on the Default Strategies plan (#218).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import cache, lru_cache
from types import SimpleNamespace
from unittest.mock import patch

from wizwalker.extensions.wizsprinter import sprinty_combat
from wizwalker.memory import EffectTarget, SpellEffects
from wizwalker.memory.memory_objects.enums import HangingDisposition

from bartlebot.combat.strategy_store import load_player_strategy
from bartlebot.nodes.combat_runner import _str_provider_class
from bartlebot.paths import project_root
from tests.wizsprinter.fakes import Card, Effect, Hanging, Member, fake_combat

# ---------------------------------------------------------------------------
# Spells
# ---------------------------------------------------------------------------

_OUTGOING = {SpellEffects.modify_outgoing_damage, SpellEffects.modify_outgoing_damage_flat}
_HANGS = _OUTGOING | {SpellEffects.modify_incoming_damage, SpellEffects.modify_incoming_damage_flat}


@dataclass(frozen=True)
class Spell:
    """A spell a card in hand can carry. `item` and `treasure` cards take no enchant."""

    name: str
    template_id: int
    effects: tuple[Effect, ...]
    pips: int = 0
    item: bool = False
    treasure: bool = False

    def enchanted(self, enchant: Spell) -> InHand:
        """This spell in hand with `enchant` already fused into it."""
        return InHand(self, enchant)

    def hanging(self, enchant: Spell | None = None) -> Hung:
        """What this spell leaves on a combatant, cast with `enchant` fused in or none."""
        return Hung(self, enchant)

    @property
    def is_aura(self) -> bool:
        return any(e.target is EffectTarget.self and e.rounds > 0 for e in self.effects)


@dataclass(frozen=True)
class InHand:
    spell: Spell
    enchant: Spell | None = None


@dataclass(frozen=True)
class Hung:
    spell: Spell
    enchant: Spell | None = None

    def fake(self) -> Hanging:
        effect = next((e for e in self.spell.effects if e.type in _HANGS), self.spell.effects[0])
        up = (effect.param > 0) is (effect.type in _OUTGOING)
        return Hanging(
            self.spell.template_id,
            self.enchant.template_id if self.enchant else 0,
            effect_type=effect.type,
            disposition=HangingDisposition.beneficial if up else HangingDisposition.harmful,
        )


def _spell(name, template_id, *effects, pips=0, item=False, treasure=False) -> Spell:
    return Spell(name, template_id, tuple(Effect(*e) for e in effects), pips, item, treasure)


T = EffectTarget
E = SpellEffects

# Enchants
SHARPENED_BLADE = _spell("Sharpened Blade", 1001, (E.modify_card_charm, T.spell, 10))
POTENT_TRAP = _spell("Potent Trap", 1002, (E.modify_card_ward, T.spell, 10))
EPIC = _spell("Epic", 1003, (E.modify_card_damage, T.spell, 275))
# Blades, traps and the rest of the buffs
STORM_BLADE = _spell("Storm Blade", 2001, (E.modify_outgoing_damage, T.friendly_single, 35))
BALANCEBLADE = _spell("Balanceblade", 2002, (E.modify_outgoing_damage, T.friendly_team, 25), pips=1)
FEINT = _spell(
    "Feint", 2101, (E.modify_incoming_damage, T.enemy_single, 70), (E.modify_incoming_damage, T.self, 30), pips=1
)
STORM_TRAP = _spell("Storm Trap", 2102, (E.modify_incoming_damage, T.enemy_single, 25))
MASS_FEINT = _spell(
    "Feint Mass - Amulet",
    2103,
    (E.modify_incoming_damage, T.enemy_team, 60),
    (E.modify_incoming_damage, T.self, 30),
    pips=2,
    item=True,
)
MASS_HEX = _spell("Mass Hex", 2104, (E.modify_incoming_damage, T.enemy_team, 30), pips=2, item=True)
AMPLIFY = _spell("Amplify", 2201, (E.modify_outgoing_damage, T.self, 15, 4), pips=1)
WYLDFIRE = _spell("Wyldfire", 2301, (E.modify_outgoing_damage, T.target_global, 25), pips=2)
SHADOW_SHRIKE = _spell("Shadow Shrike", 2401, (E.shadow_self, T.self, 0))
# Heals and hits
FAIRY = _spell("Fairy", 3001, (E.heal, T.friendly_single, 400), pips=3)
STORM_SHARK = _spell("Storm Shark", 4001, (E.damage, T.enemy_single, 300), pips=1)
KRAKEN = _spell("Kraken", 4002, (E.damage, T.enemy_single, 500), pips=3)
SIRENS = _spell("Sirens", 4101, (E.damage, T.enemy_team, 400), pips=5)
STORM_LORD = _spell("Storm Lord", 4102, (E.damage, T.enemy_team, 700), pips=7)


# ---------------------------------------------------------------------------
# The fight
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Wizard:
    name: str
    health: int = 3000
    max_health: int = 3000
    hanging: Sequence[Hung] = ()
    stunned: bool = False

    @property
    def dead(self) -> Wizard:
        """This wizard at 0 health."""
        return replace(self, health=0)


@dataclass(frozen=True)
class Enemy:
    name: str = "Mob"
    boss: bool = False
    health: int = 10000
    max_health: int = 10000
    untargetable: bool = False
    hanging: Sequence[Hung] = ()


def team(size: int) -> tuple[Wizard, ...]:
    """`size` wizards seated under combat order 4, 3, 2, 1, as the shipped team duels seat
    them: P1 in the last seat, so `ally(last)` is P1."""
    return tuple(Wizard(f"P{n}") for n in range(size, 0, -1))


@dataclass(frozen=True)
class Fight:
    """One planning phase, as the client `me` sees it. `team` is in seat order."""

    team: Sequence[Wizard]
    enemies: Sequence[Enemy]
    hand: Sequence[Spell | InHand]
    me: str = "P1"
    round: int = 1
    pips: int = 7
    power_pips: int = 0
    deck: int = 20


@dataclass(frozen=True)
class Turn:
    """What one round cast. `card` is None for a pass; `target` is a combatant's name, a
    tuple of names, or None for a card cast with no target (an AoE). `fuses` are the
    enchants cast onto cards in hand on the way, as (enchant, card), in order."""

    card: str | None
    target: str | tuple[str, ...] | None = None
    enchant: str | None = None
    fuses: tuple[tuple[str, str], ...] = field(default=())

    @property
    def passed(self) -> bool:
        return self.card is None

    def __str__(self) -> str:
        if self.passed:
            return "pass"
        enchant = f"[{self.enchant}]" if self.enchant else ""
        return f"{self.card}{enchant} @ {self.target}"


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _provider(dsl: str):
    """The DSL parsed the way a fight parses it, once: a parse costs more than a round."""
    return _str_provider_class()(dsl, cast_time=0)


@cache
def shipped_dsl(strategy_id: str) -> str:
    """A shipped player strategy's DSL, from the repo's root alone: no user copy shadows it."""
    return load_player_strategy(strategy_id, root=project_root()).dsl


async def _no_wait(_delay=0, *_):
    await asyncio.sleep(0)


# wizsprinter's waits for a fuse to show. The fakes fuse at once, and a held enchant's
# fuse otherwise costs a fixed 0.1 s.
_INSTANT = SimpleNamespace(sleep=_no_wait, get_running_loop=asyncio.get_running_loop)


def _member(combatant: Wizard | Enemy) -> Member:
    hung = list(combatant.hanging)
    return Member(
        combatant.name,
        *[h.fake() for h in hung if not h.spell.is_aura],
        auras=[h.fake() for h in hung if h.spell.is_aura],
        health=combatant.health,
        max_health=combatant.max_health,
        boss=getattr(combatant, "boss", False),
        untargetable=getattr(combatant, "untargetable", False),
        stunned=getattr(combatant, "stunned", False),
    )


def _name(target) -> str | tuple[str, ...] | None:
    if target is None:
        return None
    if isinstance(target, list):
        return tuple(member.name_ for member in target)
    return target.name_


async def cast(dsl: str, fight: Fight) -> Turn:
    """Run one round of `fight` with `dsl` as my strategy, and return what it cast."""
    provider = _provider(dsl)
    log: list = []
    hand: list = []
    budget = fight.pips + 2 * fight.power_pips
    for entry in fight.hand:
        spell, enchant = (entry.spell, entry.enchant) if isinstance(entry, InHand) else (entry, None)
        card = Card(
            spell.name,
            spell.effects,
            hand,
            template_id=spell.template_id,
            pips=spell.pips,
            castable=spell.pips <= budget,
            item=spell.item,
            treasure=spell.treasure,
            log=log,
        )
        if enchant is not None:
            card.fuse(Card(enchant.name, enchant.effects, template_id=enchant.template_id))

    names = [wizard.name for wizard in fight.team]
    members = [_member(wizard) for wizard in fight.team]
    me = names.index(fight.me)
    members[me].participant.pips = fight.pips
    members[me].participant.power_pips = fight.power_pips
    sprinty = fake_combat(
        hand,
        team=members,
        enemies=[_member(enemy) for enemy in fight.enemies],
        me=me,
        provider=provider,
        round_number=fight.round,
        deck=fight.deck,
    )
    # As if this client had played every round so far: each earlier `{N}` line moved the
    # repeating lines on by one, and none of its casts fizzled.
    sprinty.had_first_round = fight.round > 1
    sprinty.was_pass = True
    sprinty.rel_round_offset = -sum(1 for r in provider.config.specific_rounds if r < fight.round)

    with patch.object(sprinty_combat, "asyncio", _INSTANT):
        await sprinty.handle_round()

    fuses = tuple((card.base_name, target.base_name) for card, target in log if isinstance(target, Card))
    for card, target in log:
        if not isinstance(target, Card):
            enchant = card.enchanted_by.base_name if card.enchanted_by else None
            return Turn(card.base_name, _name(target), enchant, fuses)
    assert sprinty.passes, "the round neither cast nor passed"
    return Turn(None, fuses=fuses)
