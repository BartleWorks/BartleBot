"""Which player strategies aim at a slot, and whether a duel fixes who is in it.

``ally(0)`` and ``ally(last)`` name a slot on the team, not a player: the team lists players
in the order they joined. Default Support's blade on ``ally(last)`` reaches P1 only because
Default Team Duel's combat order, 4, 3, 2, 1, puts P1 in the last slot. A duel with no
combat order sends every player in at once, so the slots fill in whatever order they arrive
and the blade lands on a different player from fight to fight (#212).

Everything here is pure and Qt-free; :mod:`bartlebot.combat.strategy_summary` words the
warning, and :mod:`bartlebot.combat.combat_warnings` logs it before a run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bartleby.shared.strategy_models import DuelConfig

#: ``ally(<index>)``. ``ally`` alone picks by the move, not by slot, so it is not one.
_SLOT = re.compile(r"\bally\s*\(\s*([^()\s]+)\s*\)", re.IGNORECASE)
#: A quoted spell or enemy name, which may hold anything and targets nothing.
_QUOTED = re.compile(r'"[^"]*"')


def slot_targets(dsl: str) -> tuple[str, ...]:
    """The slots *dsl* names, as ``ally(last)``, ``ally(0)``, in first-use order."""
    slots: dict[str, None] = {}
    for match in _SLOT.finditer(_QUOTED.sub('""', dsl or "")):
        slots[f"ally({match.group(1).lower()})"] = None
    return tuple(slots)


def unfixed_slot_uses(config: DuelConfig, pcs_dsl: Mapping[str, str]) -> list[tuple[list[int], tuple[str, ...]]]:
    """The assignments whose strategy names a slot this duel leaves to chance.

    Each is ``(players, slots)``. None when the duel has a combat order, which fixes every
    slot, or has room for one player only, whose every slot is itself. ``pcs_dsl`` maps a
    linked player strategy's id to its DSL; one missing from it is reported elsewhere.
    """
    if config.combat_order or config.max_players < 2:
        return []
    uses = []
    for assignment in config.player_assignments:
        if not assignment.players:
            continue
        dsl = pcs_dsl.get(assignment.player_combat_strategy_id, "") if assignment.is_linked else assignment.dsl
        slots = slot_targets(dsl)
        if slots:
            uses.append((sorted(assignment.players), slots))
    return uses
