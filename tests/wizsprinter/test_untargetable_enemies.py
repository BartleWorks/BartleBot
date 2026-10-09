"""Enemies that are dead or untargetable are not valid targets (#192).

Lives in wizsprinter (`sprinty_combat`), which is pinned by exact commit here and has no
suite of its own, so this is where it gets covered -- the same arrangement as
`test_last_player_target.py`.

The Heretical Academy dragon has four heads and only one is targetable at a time. The
game flags the others `m_untargetable`, and a killed head stays in the circle at 0 health,
reading *targetable*, until the game removes it. `tools/probe_untargetable.py` has the
live run these rules come from.
"""

from __future__ import annotations

from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (
    AggregationMode,
    ConditionTarget,
    TargetData,
    TargetType,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import SprintyCombat


class _Participant:
    def __init__(self, untargetable: bool | Exception):
        self._untargetable = untargetable

    async def untargetable(self) -> bool:
        if isinstance(self._untargetable, Exception):
            raise self._untargetable
        return self._untargetable


class _Member:
    """A CombatMember with just the reads the filter makes."""

    def __init__(self, name: str, *, health: int | Exception = 12000, untargetable: bool | Exception = False):
        self.name_ = name
        self._health = health
        self._participant = _Participant(untargetable)

    async def health(self) -> int:
        if isinstance(self._health, Exception):
            raise self._health
        return self._health

    async def get_participant(self) -> _Participant:
        return self._participant

    async def name(self) -> str:
        return self.name_

    def __repr__(self) -> str:
        return f"_Member({self.name_})"


def _combat(*, enemies=(), boss=None):
    combat = SprintyCombat.__new__(SprintyCombat)

    async def get_enemies():
        return list(enemies)

    async def get_boss_or_none():
        return boss

    async def get_members():
        return list(enemies)

    combat.get_enemies = get_enemies
    combat.get_boss_or_none = get_boss_or_none
    combat.get_members = get_members
    return combat


def _heads(*, targetable: int):
    """Four dragon heads, only `targetable` (0-based) castable."""
    return [_Member(f"Head{i}", untargetable=i != targetable) for i in range(4)]


# ---------------------------------------------------------------------------
# enemy(n)
# ---------------------------------------------------------------------------


async def test_enemy_zero_is_the_head_that_can_be_hit():
    heads = _heads(targetable=2)

    assert await _combat(enemies=heads).get_nth_enemy_or_none(0) is heads[2]


async def test_enemy_zero_follows_the_phase():
    heads = _heads(targetable=0)
    combat = _combat(enemies=heads)
    assert await combat.get_nth_enemy_or_none(0) is heads[0]

    heads[0]._health = 0
    heads[0]._participant._untargetable = False  # a dead head reads targetable
    heads[3]._participant._untargetable = False
    assert await combat.get_nth_enemy_or_none(0) is heads[3]


async def test_a_dead_enemy_is_not_a_target_even_though_it_reads_targetable():
    corpse = _Member("Corpse", health=0)
    live = _Member("Live")

    assert await _combat(enemies=[corpse, live]).get_nth_enemy_or_none(0) is live


async def test_indices_count_through_valid_enemies_only():
    a, hidden, b = _Member("A"), _Member("Hidden", untargetable=True), _Member("B")
    combat = _combat(enemies=[a, hidden, b])

    assert await combat.get_nth_enemy_or_none(1) is b
    assert await combat.get_nth_enemy_or_none(-1) is b
    assert await combat.get_nth_enemy_or_none(2) is None


async def test_no_valid_enemy_is_no_target():
    combat = _combat(enemies=[_Member("Hidden", untargetable=True), _Member("Corpse", health=0)])

    assert await combat.get_nth_enemy_or_none(0) is None


async def test_unreadable_flags_keep_the_old_behaviour():
    """A failed read must not remove a target: fail open, as before this existed."""
    broken = _Member("Broken", health=RuntimeError("gone"), untargetable=RuntimeError("gone"))

    assert await _combat(enemies=[broken]).get_nth_enemy_or_none(0) is broken


# ---------------------------------------------------------------------------
# Cast targets
# ---------------------------------------------------------------------------


async def test_enemies_target_is_the_castable_ones():
    heads = _heads(targetable=1)

    resolved = await _combat(enemies=heads).try_get_config_target(TargetData(TargetType.type_enemies))

    assert resolved == [heads[1]]


async def test_enemies_target_with_none_castable_is_false():
    combat = _combat(enemies=[_Member("Hidden", untargetable=True)])

    assert await combat.try_get_config_target(TargetData(TargetType.type_enemies)) is False


async def test_an_untargetable_boss_is_no_target_so_the_next_priority_runs():
    boss = _Member("Boss", untargetable=True)
    combat = _combat(boss=boss)

    assert await combat.try_get_config_target(TargetData(TargetType.type_boss)) is False


async def test_a_dead_boss_is_no_target():
    combat = _combat(boss=_Member("Boss", health=0))

    assert await combat.try_get_config_target(TargetData(TargetType.type_boss)) is False


async def test_a_castable_boss_is_the_target():
    boss = _Member("Boss")

    assert await _combat(boss=boss).try_get_config_target(TargetData(TargetType.type_boss)) is boss


async def test_a_name_shared_by_every_head_picks_the_castable_one():
    """All four heads are called Balaur, so a named target had to skip the hidden ones."""
    heads = [_Member("Balaur", untargetable=i != 2) for i in range(4)]
    combat = _combat(enemies=heads)

    assert await combat.get_member_named("Balaur") is heads[2]
    assert await combat.get_member_vaguely_named("balaur") is heads[2]


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


async def test_single_enemy_condition_reads_the_enemy_the_cast_would_hit():
    heads = _heads(targetable=3)
    combat = _combat(enemies=heads)

    resolved = await combat.resolve_condition_target(ConditionTarget(TargetType.type_enemy, 0))

    assert resolved is heads[3]


async def test_aggregates_count_untargetable_enemies_but_not_dead_ones():
    """`any/all/avg(enemies)` describe the fight, not a cast."""
    hidden, castable, corpse = _Member("Hidden", untargetable=True), _Member("Live"), _Member("Corpse", health=0)
    combat = _combat(enemies=[hidden, castable, corpse])

    resolved = await combat.resolve_condition_target(
        ConditionTarget(TargetType.type_enemies, aggregation=AggregationMode.agg_avg)
    )

    assert resolved == [hidden, castable]
