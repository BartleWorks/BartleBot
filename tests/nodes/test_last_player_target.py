"""Counting a target from the end of the team (`ally(last)` / `ally(-1)`).

Lives in wizsprinter (`combat_backends`, `sprinty_combat`), which is pinned by
exact commit here and has no suite of its own, so this is where it gets covered
-- the same arrangement as `test_spell_ranking.py`.

The point of the feature, from BartleBot#6: one strategy line that names the
same wizard on every client in the group. `ally(2)` only means "the hitter" on
a four-player team, and only when read by somebody who isn't the hitter.
"""

from __future__ import annotations

import pytest
from lark import Lark
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import TargetType
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import SprintyCombat

from bartlebot.nodes.combat_runner import validate_dsl


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
    "dsl, expected_type",
    [
        ("any<blade>@ally(last)", TargetType.type_ally),
        ("any<blade>@ally(-1)", TargetType.type_ally),
        ("any<damage>@enemy(last)", TargetType.type_enemy),
        ("any<damage>@enemy(-1)", TargetType.type_enemy),
    ],
)
def test_last_is_minus_one(parse, dsl, expected_type):
    """`last` is lowered to -1 at parse time so the resolver has one index form
    to handle and the two spellings cannot drift apart."""
    target = parse(dsl).target

    assert target.target_type is expected_type
    assert target.extra_data == -1


def test_deeper_negative_indices_survive(parse):
    assert parse("any<blade>@ally(-2)").target.extra_data == -2


def test_forward_indices_are_unchanged(parse):
    assert parse("any<blade>@ally(2)").target.extra_data == 2


def test_bare_ally_still_has_no_index(parse):
    assert parse("any<blade>@ally").target.extra_data is None


def test_condition_targets_take_it_too(parse):
    """`?(ally(last).health < 50%)` -- conditions read the same index grammar."""
    condition = parse("?(ally(last).health < 50%) any<heal>@ally(last)").condition

    assert condition.target.target_type is TargetType.type_ally
    assert condition.target.index == -1


def test_it_nests_inside_select(parse):
    sub_targets = parse("any<blade>@select(ally(last), self)").target.extra_data

    assert sub_targets[0].target_type is TargetType.type_ally
    assert sub_targets[0].extra_data == -1


def test_a_spell_named_last_is_still_a_spell(parse):
    """`last` is only a keyword inside an index. The Earley lexer keeps it
    contextual, so a card whose name starts with it still parses as a name."""
    assert parse("last stand").move.card.name == "last stand"


@pytest.mark.parametrize(
    "dsl",
    [
        "any<blade>@ally(last)",
        "any<blade>@ally(-1)",
        "any<damage>@enemy(last)",
        "?(ally(last).health < 50%) any<heal>@ally(last)",
    ],
)
def test_the_pre_run_validator_accepts_it(dsl):
    """The warning dialog must not flag the new syntax as malformed."""
    assert validate_dsl(dsl) is None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class _Member:
    """Stands in for a CombatMember; only identity matters here."""

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"_Member({self.name})"


P1, P2, P3, P4 = (_Member(f"P{i}") for i in range(1, 5))
E1, E2 = _Member("E1"), _Member("E2")


def _combat(*, team, me=None, enemies=()):
    """A SprintyCombat with its member lookups stubbed.

    `team` is in combat-circle order and includes the client wizard, which is
    how the game orders it -- the same order on every client, which is why one
    line can name one wizard. `me` is whichever of them is running; `allies` is
    the team minus that one.
    """
    combat = SprintyCombat.__new__(SprintyCombat)
    if me is None:
        me = team[0] if team else None

    async def get_team():
        return list(team)

    async def get_allies():
        return [m for m in team if m is not me]

    async def get_enemies():
        return list(enemies)

    combat.get_team = get_team
    combat.get_allies = get_allies
    combat.get_enemies = get_enemies
    return combat


async def test_last_ally_is_the_end_of_the_team():
    combat = _combat(team=[P1, P2, P3, P4])

    assert await combat.get_nth_ally_or_none(-1) is P4


async def test_the_last_player_resolves_to_itself():
    """The whole point: run the same line on the last wizard and it targets
    itself, instead of falling back to the wizard before it."""
    combat = _combat(team=[P1, P2, P3, P4], me=P4)

    assert await combat.get_nth_ally_or_none(-1) is P4


async def test_every_client_in_the_group_names_the_same_wizard():
    """One line, four clients, one target. This is what `ally(2)` cannot do."""
    order = [P1, P2, P3, P4]
    resolved = set()
    for me in order:
        combat = _combat(team=order, me=me)
        resolved.add(await combat.get_nth_ally_or_none(-1))

    assert resolved == {P4}


async def test_forward_indices_still_skip_the_client_wizard():
    combat = _combat(team=[P1, P2, P3, P4], me=P1)

    assert await combat.get_nth_ally_or_none(0) is P2
    assert await combat.get_nth_ally_or_none(2) is P4


async def test_a_forward_index_disagrees_on_the_last_client():
    """Why the backward index had to be team-inclusive. `ally(2)` is the fourth
    wizard for the three who aren't it; read by the fourth wizard, whose own
    ally list is one shorter, the same text names the third."""
    others = _combat(team=[P1, P2, P3, P4], me=P1)
    hitter = _combat(team=[P1, P2, P3, P4], me=P4)

    assert await others.get_nth_ally_or_none(2) is P4
    assert await hitter.get_nth_ally_or_none(2) is P3


async def test_solo_last_ally_is_yourself():
    """No allies at all, but you are still the last player on your team."""
    combat = _combat(team=[P1])

    assert await combat.get_nth_ally_or_none(-1) is P1
    assert await combat.get_nth_ally_or_none(0) is None


async def test_second_from_the_end():
    combat = _combat(team=[P1, P2, P3, P4])

    assert await combat.get_nth_ally_or_none(-2) is P3


async def test_off_the_front_of_the_team_is_no_target():
    """Out of range resolves to None, which skips the priority rather than
    wrapping around to somebody unintended."""
    combat = _combat(team=[P1, P2])

    assert await combat.get_nth_ally_or_none(-3) is None


async def test_last_enemy_is_plain_indexing():
    """Enemies have no self to include, so -1 is the last of the list."""
    combat = _combat(team=[P1], enemies=[E1, E2])

    assert await combat.get_nth_enemy_or_none(-1) is E2
    assert await combat.get_nth_enemy_or_none(0) is E1
    assert await combat.get_nth_enemy_or_none(-3) is None


async def test_no_enemies_is_no_target():
    combat = _combat(team=[P1], enemies=[])

    assert await combat.get_nth_enemy_or_none(-1) is None
