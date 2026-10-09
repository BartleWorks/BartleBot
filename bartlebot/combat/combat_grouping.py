"""Grouping of per-fight combat bindings for display and editing.

One Duel Strategy is shown once, however many fights use it.  That is the whole
idea here, and it is a *display* rule with an editing rule attached: because the
group is the unit on screen, editing it edits every fight in it — which is the
intended behavior, not an accident to be defended against.

The previous arrangement drew one panel per exposed combat state and handed
several of them the *same* ``StateAssignment`` object.  Mirror Lake rendered
four identical panels backed by one object: editing any of them silently edited
all four, and the other three kept displaying the stale summary until the tab
was rebuilt from disk.  Collapsing to one panel per group removes the class of
bug entirely — there is no sibling widget left to disagree with.

Everything here is pure and Qt-free so it can be tested without a QApplication,
matching :mod:`bartlebot.combat.combat_warnings` next door.

Grouping key
------------
Linked bindings group by ``duel_combat_strategy_id``; unlinked ones never group
with anything, each being one fight on the profile's default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bartlebot.combat.fight_keys import settle
from bartlebot.combat.profile_models import FightRef, StateAssignment

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig, StateModel


@dataclass
class BindingGroup:
    """One Duel Strategy and every exposed fight bound to it."""

    assignment: StateAssignment
    states: list[StateModel] = field(default_factory=list)

    @property
    def is_configured(self) -> bool:
        """True if this group actually says something.

        Matches ``resolve_combat`` and ``_unassigned_state_warnings``: an
        unlinked assignment is indistinguishable from no assignment at all, and
        falls back the same way.
        """
        return self.assignment.is_linked

    @property
    def state_ids(self) -> list[str]:
        return [s.id for s in self.states]


def merge_linked(assignments: list[StateAssignment]) -> list[StateAssignment]:
    """Collapse assignments sharing a Duel Strategy onto one object.

    Files written before grouping existed can hold several separate
    ``StateAssignment`` entries all pointing at the same library strategy —
    ``bot_strategies/f77a6a91-….json`` has two — because the old editor created
    one per fight.  They mean exactly what a single grouped assignment means, so
    they are folded together on read.

    The *first* occurrence survives and absorbs the others' fights, so
    ordering follows the file.  Unlinked assignments are passed through untouched:
    they have no shared identity to merge on.

    Mutates the surviving assignments' ``fights`` in place — callers pass
    freshly loaded models, and the merged result is what should be written back
    on the next save.
    """
    first_by_dcs: dict[str, StateAssignment] = {}
    merged: list[StateAssignment] = []

    for assignment in assignments:
        dcs_id = assignment.duel_combat_strategy_id
        if dcs_id is None:
            merged.append(assignment)
            continue
        first = first_by_dcs.get(dcs_id)
        if first is None:
            first_by_dcs[dcs_id] = assignment
            merged.append(assignment)
            continue
        for fight in assignment.fights:
            if fight.state not in first.state_ids:
                first.fights.append(fight)

    return merged


def split_unconfigured(assignments: list[StateAssignment]) -> list[StateAssignment]:
    """Give every fight in a *saying-nothing* group its own binding.

    Groups exist to share a config.  One that holds no config shares nothing, so
    keeping several fights in it only costs them the thing decision 1 preserved:
    an unassigned fight is configured with one click on its own row, and a fight
    buried in a shared empty group would have to be dug out through "Applies
    to…" first.

    Reachable by clearing a multi-fight group's chip — the whole group unlinks
    together — and from a file holding a grouped assignment that was unlinked.
    Costs nothing on disk either way: ``_save_bcs`` already drops bindings that
    are not linked.
    """
    split: list[StateAssignment] = []
    for assignment in assignments:
        if not assignment.is_linked and len(assignment.state_ids) > 1:
            split.extend(StateAssignment(fights=[fight]) for fight in assignment.fights)
        else:
            split.append(assignment)
    return split


def build_bindings(assignments: list[StateAssignment], config: BotConfig | None = None) -> dict[str, StateAssignment]:
    """Map every bound state id to its assignment, one shared object per group.

    Replaces the ``for sid in assignment.state_ids`` seeding loop that was
    duplicated across four call sites in ``CombatTab``.

    Given the bot's *config*, the stored fights are first placed on its current
    states — by enemy, then by state id, see :mod:`bartlebot.combat.fight_keys` — and each
    assignment's fights are rewritten in memory to the states they landed on, the
    orphaned ones dropped. That is what a freshly loaded profile needs. Without one,
    the fights are taken at the state ids they hold, which is right for assignments
    already placed and since edited by state id, and for a profile with no bot open.
    A state id held by more than one assignment (only reachable from a hand-edited
    file) resolves to the first, as ``fight_keys.bind`` does.

    Merging and splitting are both applied here so the two invariants the
    sidebar relies on hold everywhere it is called: one row per strategy, and
    one row per unassigned fight.
    """
    groups = split_unconfigured(merge_linked(assignments))
    if config is not None:
        settle(groups, config)
    bindings: dict[str, StateAssignment] = {}
    for assignment in groups:
        for state_id in assignment.state_ids:
            bindings.setdefault(state_id, assignment)
    return bindings


def group_bindings(bindings: dict[str, StateAssignment], exposed: list[StateModel]) -> list[BindingGroup]:
    """Return one group per distinct binding, in display order.

    Configured groups come first, ordered by their earliest member's position in
    the FSM; unassigned fights follow individually, also in FSM order.  They stay
    *individual* here even though the sidebar draws them as pills on the one
    default card: each is its own ``StateAssignment``, so giving one fight a
    strategy is still one click on that fight's own name and touches nothing else.
    Merging them into a single group would make that click configure every
    unassigned fight at once.

    States absent from ``bindings`` are skipped.  Callers seed a placeholder for
    every exposed state before grouping, so this only bites on a caller that
    forgot — better a missing row than a crash in a sidebar rebuild.
    """
    groups: dict[int, BindingGroup] = {}
    order: list[int] = []

    for state in exposed:
        assignment = bindings.get(state.id)
        if assignment is None:
            continue
        key = id(assignment)
        group = groups.get(key)
        if group is None:
            group = BindingGroup(assignment=assignment)
            groups[key] = group
            order.append(key)
        group.states.append(state)

    ordered = [groups[key] for key in order]
    # Stable partition: FSM order is preserved within each half.
    return [g for g in ordered if g.is_configured] + [g for g in ordered if not g.is_configured]


def state_label(state: StateModel) -> str:
    """Display name for one fight — the bot author's label, or the raw id.

    A group lists its fights vertically, one label per line, rather than joining
    them into a single elided string.  A joined title truncates, and a truncated
    membership list is the one thing this panel must not do: a fight you cannot
    see is bound to a strategy you are about to edit.  One line each also means a
    long name elides against its own width and carries its own tooltip, instead
    of consuming the space its neighbors needed.
    """
    return state.label or state.id


def apply_membership(
    bindings: dict[str, StateAssignment],
    assignment: StateAssignment,
    chosen_ids: list[str],
) -> None:
    """Set which fights belong to ``assignment``, updating ``bindings`` in place.

    ``chosen_ids`` is the complete desired membership, as returned by the
    "Applies to…" dialog.  Fights are moved in from whatever group currently
    holds them, and fights no longer chosen are detached per :func:`detach_state`.
    """
    chosen = list(dict.fromkeys(chosen_ids))
    leaving = [sid for sid in assignment.state_ids if sid not in chosen]
    joining = [sid for sid in chosen if sid not in assignment.state_ids]

    for state_id in leaving:
        detach_state(bindings, assignment, state_id)

    for state_id in joining:
        current = bindings.get(state_id)
        # A fight moving between groups keeps nothing: it is about to be given
        # the destination group's config, so copying the one it is leaving would
        # only create an orphan nobody asked for.
        if current is not None and current is not assignment:
            _remove_state(current, state_id)
        assignment.add_state(state_id)
        bindings[state_id] = assignment


def detach_state(
    bindings: dict[str, StateAssignment],
    assignment: StateAssignment,
    state_id: str,
) -> StateAssignment:
    """Split one fight out of a group and return its new standalone binding.

    The departing fight falls back to the bot default.  It cannot keep the link:
    a separate assignment naming the same strategy is precisely what
    :func:`merge_linked` folds back together, so it would rejoin the group it just
    left.  Nothing is lost — the strategy is still there, one click away on the
    fight's own chip.
    """
    _remove_state(assignment, state_id)
    detached = StateAssignment(fights=[FightRef(state=state_id)])
    bindings[state_id] = detached
    return detached


def _remove_state(assignment: StateAssignment, state_id: str) -> None:
    assignment.remove_state(state_id)
