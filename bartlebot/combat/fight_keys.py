"""Which stored fight belongs to which combat state: one resolver, used by every reader.

A combat profile's ``StateAssignment`` lists the fights it covers, each as a
``FightRef``: the state it was on, and the enemy it named. A state id alone is lost
whenever a node is replaced, a bot is re-authored, or a fight is deleted and made
again; the enemy is what carries the strategy across that.

An **enemy key** is a fight node's ``enemy`` param, stripped, exactly as typed,
wildcards included. It counts as a key only while exactly one exposed fight in the bot
names it, so it can never point at two fights.

:func:`place` puts every stored fight on a current state:

1. a fight whose enemy is a key goes to the fight node naming it;
2. each remaining fight goes to its ``state``, if that is still an exposed fight and
   step 1 did not take it;
3. anything left is orphaned.

Enemy matches win, so a renamed enemy and a replaced node both keep their strategy,
and only changing both at once loses it. Nothing here writes: the keys heal when the
Combat tab saves (:func:`settle`), never on load and never when a node is edited,
because the profile saves at once while the bot waits for Save.

Qt-free, like ``combat_grouping`` and ``combat_warnings`` beside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bartlebot.combat.profile_models import FightRef

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig, StateModel
    from bartlebot.combat.profile_models import BotCombatStrategy, StateAssignment


def exposed_combat_states(config: BotConfig) -> list[StateModel]:
    """Return the ``handle_combat`` states that opted out of the bot default.

    ``use_default_strategy: false`` is how a bot file says "this fight is
    special — give it its own Duel Strategy".  It is the portable half of the
    combat config: it ships with the bot and means the same thing for every
    user, while the strategy that satisfies it stays personal.

    ``use_default_duel_profile`` is the pre-v3 spelling.  The bots in this repo
    were migrated by ``scripts/migrate_bot_ids.py``, but users' own bots were
    not, so the old name is still honored on read.
    """
    return [
        s
        for s in config.states
        if s.node_type == "handle_combat"
        and not s.params.get("use_default_strategy", s.params.get("use_default_duel_profile", True))
    ]


def enemy_of(state: StateModel) -> str:
    """The enemy a fight node names, stripped; empty for one that takes the closest mob."""
    enemy = state.params.get("enemy")
    return enemy.strip() if isinstance(enemy, str) else ""


def enemy_keys(config: BotConfig) -> dict[str, str]:
    """Enemy to state id, for every enemy exactly one exposed fight names."""
    named: dict[str, list[str]] = {}
    for state in exposed_combat_states(config):
        enemy = enemy_of(state)
        if enemy:
            named.setdefault(enemy, []).append(state.id)
    return {enemy: ids[0] for enemy, ids in named.items() if len(ids) == 1}


@dataclass
class Placement:
    """Where :func:`place` put each stored fight.

    ``placed`` holds (state id, assignment, stored fight) in the order the fights are
    stored, so a reader that lists them keeps the file's order.
    """

    placed: list[tuple[str, StateAssignment, FightRef]] = field(default_factory=list)
    orphans: list[tuple[StateAssignment, FightRef]] = field(default_factory=list)

    def by_state(self) -> dict[str, StateAssignment]:
        return {state_id: assignment for state_id, assignment, _ in self.placed}


def place(assignments: list[StateAssignment], config: BotConfig) -> Placement:
    """Place every fight *assignments* store on one of *config*'s exposed fights.

    Two stored fights wanting one state is only reachable from a hand-edited file; the
    first in file order gets it, which is the one a lookup by state id always found.
    """
    keys = enemy_keys(config)
    exposed = {s.id for s in exposed_combat_states(config)}
    stored = [(assignment, fight) for assignment in assignments for fight in assignment.fights]
    target: dict[int, str] = {}
    taken: set[str] = set()

    for index, (_, fight) in enumerate(stored):
        state_id = keys.get(fight.enemy.strip()) if fight.enemy else None
        if state_id is not None and state_id not in taken:
            target[index] = state_id
            taken.add(state_id)
    for index, (_, fight) in enumerate(stored):
        if index not in target and fight.state in exposed and fight.state not in taken:
            target[index] = fight.state
            taken.add(fight.state)

    placement = Placement()
    for index, (assignment, fight) in enumerate(stored):
        if index in target:
            placement.placed.append((target[index], assignment, fight))
        else:
            placement.orphans.append((assignment, fight))
    return placement


def bind(bcs: BotCombatStrategy, config: BotConfig | None) -> dict[str, StateAssignment]:
    """Map each of *config*'s fight states to the assignment whose stored fight it is.

    With no config (a profile read with no bot open) there is nothing to place the
    fights on, so this falls back to the raw state ids they were stored with.
    """
    if config is None:
        bound: dict[str, StateAssignment] = {}
        for assignment in bcs.state_assignments:
            for state_id in assignment.state_ids:
                bound.setdefault(state_id, assignment)
        return bound
    return place(bcs.state_assignments, config).by_state()


def settle(assignments: list[StateAssignment], config: BotConfig) -> list[FightRef]:
    """Rewrite each assignment's fights as the states they are placed on, in place.

    Every placed fight becomes its current state, and keeps the enemy it was stored
    with. Returns the orphaned fights, which are dropped. For a caller that goes on to
    edit the assignments by state id, as the Combat tab does: after this, an
    assignment's ``state_ids`` are exactly the fights bound to it.
    """
    placement = place(assignments, config)
    settled: dict[int, list[FightRef]] = {id(a): [] for a in assignments}
    for state_id, assignment, fight in placement.placed:
        settled[id(assignment)].append(FightRef(state=state_id, enemy=fight.enemy))
    for assignment in assignments:
        assignment.fights = settled[id(assignment)]
    return [fight for _, fight in placement.orphans]


def heal(assignments: list[StateAssignment], config: BotConfig) -> list[FightRef]:
    """Rewrite each assignment's fights for saving, in place: the keys as they are now.

    Every fight is written as its state, plus its enemy if that enemy is a key right
    now. A fight whose state is no longer an exposed fight is dropped, as is a second
    fight on a state another already holds; both are returned.

    By state alone, not by :func:`place`: the assignments in memory were placed when
    they were loaded and have been edited by state id since, so the state is the truth
    and the stored enemy may be stale.
    """
    exposed = {s.id: s for s in exposed_combat_states(config)}
    keys = enemy_keys(config)
    seen: set[str] = set()
    dropped: list[FightRef] = []
    for assignment in assignments:
        kept: list[FightRef] = []
        for fight in assignment.fights:
            state = exposed.get(fight.state)
            if state is None or fight.state in seen:
                dropped.append(fight)
                continue
            seen.add(fight.state)
            enemy = enemy_of(state)
            kept.append(FightRef(state=fight.state, enemy=enemy if keys.get(enemy) == fight.state else None))
        assignment.fights = kept
    return dropped
