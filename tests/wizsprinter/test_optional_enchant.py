"""Every enchant clause is optional -- enchant if it is in hand, cast bare if not.

Lives in wizsprinter (`combat_backends`, `sprinty_combat`), which is pinned by
exact commit here and has no suite of its own, so this is where it gets covered
-- the same arrangement as `test_spell_ranking.py` and `test_last_player_target.py`.

The bug: `[sharp]` parsed to a `NamedSpell`, which carried no `optional` flag,
so the clause read as mandatory and narrowed the card pool to cards that can
still take an enchant. `is_enchantable` is false for an item card by
definition, so `any<blade>[sharp]` -- the shipped default, and every blade line
in `bots/` -- refused to cast an item-card blade. It refused even with no
Sharpened Blade in hand, since the pool was narrowed before the enchant was
looked for.
"""

from __future__ import annotations

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (
    NamedSpell,
    SpellType,
    TemplateSpell,
)
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import (
    SprintyCombat,
    enchant_is_optional,
    is_enchantable,
)


@pytest.fixture(scope="module")
def parse():
    """One compiled grammar for the module -- Lark costs ~77 ms to build."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        config = TreeToConfig().transform(parser.parse(dsl))
        return config.infinite_rounds[0].priorities[0]

    return _parse


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dsl",
    [
        "any<blade>[sharp]",  # vaguely named, the form every bot file uses
        'any<blade>["Sharpened Blade"]',  # literal name
        "any<damage>[any<mod_damage>]",  # template, optional before this change too
    ],
)
def test_every_enchant_form_parses_optional(parse, dsl):
    assert enchant_is_optional(parse(dsl).move.enchant) is True


def test_a_second_enchant_is_optional_too(parse):
    move = parse("any<damage>[sharp][sharp]").move

    assert enchant_is_optional(move.second_enchant) is True


@pytest.mark.parametrize("dsl", ['"Fire Blade"', "any<blade>"])
def test_the_move_card_itself_is_not_optional(parse, dsl):
    """The flag only ever means "in enchant position" -- a card that fails to
    match still fails its clause."""
    assert enchant_is_optional(parse(dsl).move.card) is False


def test_a_spell_built_in_code_stays_mandatory():
    """Only the parser opts in. A config assembled in code gets what it asked
    for, which is also what a spec predating the flag reads as."""
    assert enchant_is_optional(TemplateSpell([SpellType.type_enchant])) is False
    assert enchant_is_optional(NamedSpell("sharp")) is False


def test_no_enchant_clause_is_not_a_mandatory_one():
    assert enchant_is_optional(None) is False


# ---------------------------------------------------------------------------
# What the flag decides: whether the card pool is narrowed
# ---------------------------------------------------------------------------


class FakeCard:
    """The five reads `is_enchantable` makes, and nothing else."""

    def __init__(self, name: str, *, item: bool = False, treasure: bool = False, enchanted: bool = False):
        self._name = name
        self._item = item
        self._treasure = treasure
        self._enchanted = enchanted

    async def name(self) -> str:
        return self._name

    async def is_enchanted(self) -> bool:
        return self._enchanted

    async def is_enchanted_from_item_card(self) -> bool:
        return False

    async def is_treasure_card(self) -> bool:
        return self._treasure

    async def is_item_card(self) -> bool:
        return self._item

    async def is_cloaked(self) -> bool:
        return False


@pytest.fixture
def combat_with_hand():
    """A `SprintyCombat` with a fixed hand and no client behind it.

    `try_get_spell` is the only method under test; matching and ranking are
    covered elsewhere, so the template lookup is stubbed to return the hand.
    """

    def _make(cards):
        combat = SprintyCombat.__new__(SprintyCombat)

        async def _by_template(template, enchant_bonus=0):
            return list(cards)

        combat.get_castable_cards_by_template = _by_template
        return combat

    return _make


async def test_an_item_card_is_offered_when_the_enchant_is_optional(combat_with_hand):
    """The regression: an item-card blade is the only blade in hand."""
    item_blade = FakeCard("Balanceblade", item=True)
    combat = combat_with_hand([item_blade])

    picked = await combat.try_get_spell(TemplateSpell([SpellType.type_blade]), only_enchantable=False)

    assert picked is item_blade


async def test_a_mandatory_enchant_still_narrows_the_pool(combat_with_hand):
    """The path a config built in code can still ask for."""
    combat = combat_with_hand([FakeCard("Balanceblade", item=True)])

    picked = await combat.try_get_spell(TemplateSpell([SpellType.type_blade]), only_enchantable=True)

    assert picked is None


async def test_an_enchantable_card_is_reachable_either_way(combat_with_hand):
    deck_blade = FakeCard("Balanceblade")
    combat = combat_with_hand([deck_blade])

    assert await combat.try_get_spell(TemplateSpell([SpellType.type_blade]), only_enchantable=True) is deck_blade
    assert await combat.try_get_spell(TemplateSpell([SpellType.type_blade]), only_enchantable=False) is deck_blade


@pytest.mark.parametrize(
    "card, enchantable",
    [
        (FakeCard("Balanceblade"), True),
        (FakeCard("Balanceblade", item=True), False),
        (FakeCard("Balanceblade", treasure=True), False),
        (FakeCard("Balanceblade", enchanted=True), False),
    ],
)
async def test_which_cards_a_narrowed_pool_drops(card, enchantable):
    """Why narrowing is the wrong default: it drops three kinds of card that
    are perfectly castable, not just the already-enchanted one."""
    assert await is_enchantable(card) is enchantable
