"""Fakes for running wizsprinter's real `SprintyCombat` without a game client.

They stand in at the memory reads, and nothing above them: a `Card` answers what a
`CombatCard` reads from its graphical spell, and a `Member`'s `Participant` what a
`CombatMember` reads from the duel. Everything wizsprinter decides from those reads runs
for real -- which card a template matches and how it ranks, which member a target or a
condition names, whether a card stacks, the held enchant slots, which round's line is
walked.

`fake_combat()` builds the `SprintyCombat`. It overrides only what reads windows rather than
memory objects: the hand, the members, which member is the client, the round number, the
deck count and the pass button. `tests/strategies/fight.py` builds on these to walk a
whole round of a described fight.
"""

from __future__ import annotations

import inspect
import itertools
from collections.abc import Sequence
from types import SimpleNamespace

from wizwalker.combat import CombatMember
from wizwalker.combat.card import CombatCard
from wizwalker.extensions.wizsprinter.sprinty_combat import SprintyCombat
from wizwalker.memory import EffectTarget, SpellEffects
from wizwalker.memory.memory_objects.enums import HangingDisposition

# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


class Effect:
    """One effect on a card in hand, as `DynamicSpellEffect` reads it."""

    def __init__(self, effect_type: SpellEffects, target: EffectTarget, param: int = 0, rounds: int = 0):
        self.type, self.target, self.param, self.rounds = effect_type, target, param, rounds

    async def effect_type(self):
        return self.type

    async def effect_target(self):
        return self.target

    async def effect_param(self):
        return self.param

    async def num_rounds(self):
        return self.rounds

    def __repr__(self):
        return f"Effect({self.type.name} @ {self.target.name}, {self.param})"


class Hanging:
    """One effect hanging on a combatant: what left it, by spell and enchant template id.

    `effect_type` and `disposition` are what a condition such as `?(self.blades > 0)`
    counts; `stacks` reads only the two ids.
    """

    def __init__(
        self,
        spell_id: int,
        enchant_id: int = 0,
        *,
        effect_type: SpellEffects = SpellEffects.modify_outgoing_damage,
        disposition: HangingDisposition = HangingDisposition.both,
        unreadable: bool = False,
    ):
        self._ids = (spell_id, enchant_id)
        self._type = effect_type
        self._disposition = disposition
        self._unreadable = unreadable

    async def spell_template_id(self):
        if self._unreadable:
            raise ValueError("unreadable")
        return self._ids[0]

    async def enchantment_spell_template_id(self):
        if self._unreadable:
            raise ValueError("unreadable")
        return self._ids[1]

    async def effect_type(self):
        return self._type

    async def disposition(self):
        return self._disposition

    def __repr__(self):
        return f"Hanging{self._ids}"


# ---------------------------------------------------------------------------
# Combatants
# ---------------------------------------------------------------------------


class _Stats:
    def __init__(self, participant: Participant):
        self._participant = participant

    async def current_hitpoints(self):
        return self._participant.health

    async def max_hitpoints(self):
        return self._participant.max_health


class Participant:
    """What a `CombatMember` reads from the duel. `team_id` and `owner_id` are set by `fake_combat()`.

    `hanging` may be an exception, which reading the effects then raises.
    """

    def __init__(
        self,
        *,
        hanging=(),
        auras=(),
        health: int = 5000,
        max_health: int | None = None,
        boss: bool = False,
        untargetable: bool = False,
        stunned: bool = False,
        player: bool = True,
        pips: int = 0,
        power_pips: int = 0,
    ):
        self.hanging = hanging if isinstance(hanging, Exception) else list(hanging)
        self.auras = list(auras)
        self.health = health
        self.max_health = max_health if max_health is not None else max(health, 1)
        self.boss = boss
        self.untargetable_ = untargetable
        self.stunned_ = stunned
        self.player = player
        self.pips = pips
        self.power_pips = power_pips
        self.team_id_ = 0
        self.owner_id = 0

    async def hanging_effects(self):
        if isinstance(self.hanging, Exception):
            raise self.hanging
        return self.hanging

    async def aura_effects(self):
        return self.auras

    async def untargetable(self):
        return self.untargetable_

    async def team_id(self):
        return self.team_id_

    async def owner_id_full(self):
        return self.owner_id

    async def boss_mob(self):
        return self.boss

    async def stunned(self):
        return 1 if self.stunned_ else 0

    async def is_player(self):
        return self.player

    async def is_minion(self):
        return False

    async def player_health(self):
        return self.health

    async def game_stats(self):
        return _Stats(self)

    async def num_pips(self):
        return self.pips

    async def num_power_pips(self):
        return self.power_pips

    async def num_shadow_pips(self):
        return 0

    async def max_hand_size(self):
        # Unknown, so `deck.remaining` reads the deck count `fake_combat()` was given rather
        # than inferring an empty pile from a hand short of seven.
        raise ValueError("not read by the fakes")


class Member(CombatMember):
    """A combatant. The real `CombatMember` methods run, reading the fake `Participant`."""

    def __init__(self, name: str, *hanging, auras=(), health: int = 5000, max_health: int | None = None, **kwargs):
        super().__init__(None, None)
        self.name_ = name
        self.participant = Participant(hanging=hanging, auras=auras, health=health, max_health=max_health, **kwargs)

    async def get_participant(self):
        return self.participant

    async def name(self):
        return self.name_

    def __repr__(self):
        return f"Member({self.name_})"


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class _Rank:
    def __init__(self, pips: int):
        self._pips = pips

    async def spell_rank(self):
        return self._pips

    async def shadow_pips(self):
        return 0

    async def is_xpip_spell(self):
        return False


class _GraphicalSpell:
    def __init__(self, card: Card):
        self._card = card

    async def enchantment(self):
        return self._card.enchant_id

    async def pip_cost(self):
        return _Rank(self._card.pips)


class _Window:
    """A card's window in the hand. Its address is what tells two handles to one slot apart."""

    _addresses = itertools.count(1)

    def __init__(self) -> None:
        self.base_address = next(self._addresses)


class Card(CombatCard):
    """A card in hand, appended to `hand`. Cast at a card, it fuses into it; cast at
    anything else, it is the round's cast. Either way it leaves the hand.

    As in game, a fused card keeps its name and template, carries the enchant's template id
    as its enchantment, and takes a new window: a `Slot` handed out before the fuse is dead
    after it. `fuses=False` makes the game refuse an enchant on it, by doing nothing. Cards
    sharing a `log` record every cast there, in order.
    """

    def __init__(
        self,
        name: str,
        effects: Sequence[Effect],
        hand: list | None = None,
        *,
        template_id: int = 0,
        pips: int = 0,
        enchant_id: int = 0,
        castable: bool = True,
        item: bool = False,
        treasure: bool = False,
        fuses: bool = True,
        log: list | None = None,
    ):
        super().__init__(None, None)
        self.base_name = name
        self._name = name
        self._effects = list(effects)
        self._hand = hand if hand is not None else []
        self.template_id_ = template_id
        self.pips = pips
        self.enchant_id = enchant_id
        self.enchanted_by: Card | None = None
        self.castable = castable
        self.item = item
        self.treasure = treasure
        self.fuses = fuses
        self.casts = []
        self._log = log
        self.window = _Window()
        self._hand.append(self)

    async def name(self):
        return self._name

    async def template_id(self):
        return self.template_id_

    async def get_graphical_spell(self):
        return _GraphicalSpell(self)

    async def get_spell_effects(self):
        return self._effects

    async def is_castable(self):
        return self.castable

    async def is_enchanted(self):
        return self.enchant_id != 0

    async def is_enchanted_from_item_card(self):
        return False

    async def is_treasure_card(self):
        return self.treasure

    async def is_item_card(self):
        return self.item

    async def is_cloaked(self):
        return False

    def fuse(self, enchant: Card) -> None:
        self.enchant_id = enchant.template_id_
        self.enchanted_by = enchant
        self.window = _Window()

    async def cast(self, target, *, sleep_time=None, debug_paint=False):
        if isinstance(target, Slot):
            target = target.card
        self.casts.append(target)
        if self._log is not None:
            self._log.append((self, target))
        if isinstance(target, Card):
            if target.fuses:
                self._hand.remove(self)
                target.fuse(self)
        else:
            self._hand.remove(self)

    def __repr__(self):
        return f"Card({self._name})"


class Slot(CombatCard):
    """A handle to a card in hand, as `get_cards()` gives one in game: the card's window.

    Live until a fuse replaces that window. Read after, it raises as wizwalker does for a
    window whose spell is gone, and a click on it does nothing.
    """

    def __init__(self, card: Card) -> None:
        super().__init__(None, card.window)
        self.card = card

    @property
    def alive(self) -> bool:
        return self.card.window is self._spell_window and self.card in self.card._hand

    async def cast(self, target, *, sleep_time=None, debug_paint=False):
        if self.alive:
            await self.card.cast(target, sleep_time=sleep_time, debug_paint=debug_paint)

    def __repr__(self):
        return f"Slot({self.card!r}{'' if self.alive else ', dead'})"


def _read_through(name: str):
    async def read(self, *args, **kwargs):
        if not self.alive:
            raise ValueError("Graphical spell not found; probably reading too fast")
        return await getattr(self.card, name)(*args, **kwargs)

    read.__name__ = name
    return read


# Every read a Card answers, a Slot answers through it while its window lasts.
for _name in [n for n, v in vars(Card).items() if inspect.iscoroutinefunction(v) and n != "cast"]:
    setattr(Slot, _name, _read_through(_name))


def cast_at_a_combatant(card: Card) -> bool:
    """Whether the card was cast as the round's card, rather than only onto another card."""
    return any(not isinstance(target, Card) for target in card.casts)


# ---------------------------------------------------------------------------
# The combat
# ---------------------------------------------------------------------------


class _MouseHandler:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def fake_combat(
    hand: list,
    *,
    team: Sequence[Member] = (),
    enemies: Sequence[Member] = (),
    me: int = 0,
    held=(),
    provider=None,
    round_number: int = 1,
    deck: int = 20,
) -> SprintyCombat:
    """A `SprintyCombat` over a hand of `Card`s, with `team[me]` as the client.

    `team` is in seat order, so `ally(last)` is `team[-1]`; with no team the client is a
    lone `P1`. `held` are enchant slots already held this walk. `provider` is the combat
    config backend `handle_round` asks for the round's line. `deck` is the main pile's
    count. A pass is counted in `passes`; once a card is cast at a combatant the hand
    reads empty, as it does in game once the turn is over.
    """
    team = list(team) or [Member("P1")]
    for owner, member in enumerate(team, start=1):
        member.participant.team_id_, member.participant.owner_id = 0, owner
    for owner, member in enumerate(enemies, start=len(team) + 1):
        member.participant.team_id_, member.participant.owner_id = 1, owner
        member.participant.player = False
    members = team + list(enemies)

    client = SimpleNamespace(mouse_handler=_MouseHandler())
    sprinty = SprintyCombat(client, provider or SimpleNamespace(cast_time=0))
    sprinty.held_enchants = list(held)
    sprinty.passes = 0

    async def get_cards():
        if any(cast_at_a_combatant(card) for card in hand):
            return []
        # As SprintyCombat.get_cards orders them: enchanted cards first. Each read hands out
        # fresh handles, as the game's does.
        return [Slot(c) for c in hand if c.enchant_id] + [Slot(c) for c in hand if not c.enchant_id]

    async def get_members():
        return list(members)

    async def get_client_member(*_, **__):
        return team[me]

    async def get_card_counts():
        return deck, deck + len(hand)

    async def round_number_():
        return round_number

    async def pass_button():
        sprinty.was_pass = True
        sprinty.passes += 1

    sprinty.get_cards = get_cards
    sprinty.get_members = get_members
    sprinty.get_client_member = get_client_member
    sprinty.get_card_counts = get_card_counts
    sprinty.round_number = round_number_
    sprinty.pass_button = pass_button
    return sprinty
