"""Which player strategies aim at a seat, and whether a duel fixes who sits in it.

``ally(0)`` and ``ally(last)`` name a seat on the team, not a player: the team lists players
in the order they took their seats. Default Support's blade on ``ally(last)`` reaches P1
only because Default Team Duel's combat order, 4, 3, 2, 1, seats P1 last. A duel with no
combat order sends every player in at once, so the seats go in whatever order they arrive
and the blade lands on a different player from fight to fight (#212).

Everything here is pure and Qt-free; :mod:`bartlebot.combat.strategy_summary` words the
warning, and :mod:`bartlebot.combat.combat_warnings` logs it before a run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bartlebot.shared.strategy_models import DuelConfig

#: ``ally(<index>)``. ``ally`` alone picks by the move, not by seat, so it is not one.
_SEAT = re.compile(r"\bally\s*\(\s*([^()\s]+)\s*\)", re.IGNORECASE)
#: A quoted spell or enemy name, which may hold anything and targets nothing.
_QUOTED = re.compile(r'"[^"]*"')


def seat_targets(dsl: str) -> tuple[str, ...]:
    """The seats *dsl* names, as ``ally(last)``, ``ally(0)``, in first-use order."""
    seats: dict[str, None] = {}
    for match in _SEAT.finditer(_QUOTED.sub('""', dsl or "")):
        seats[f"ally({match.group(1).lower()})"] = None
    return tuple(seats)


def unfixed_seat_uses(config: DuelConfig, pcs_dsl: Mapping[str, str]) -> list[tuple[list[int], tuple[str, ...]]]:
    """The assignments whose strategy names a seat this duel leaves to chance.

    Each is ``(players, seats)``. None when the duel has a combat order, which fixes every
    seat, or seats one player only, whose every seat is itself. ``pcs_dsl`` maps a linked
    player strategy's id to its DSL; one missing from it is reported elsewhere.
    """
    if config.combat_order or config.max_players < 2:
        return []
    uses = []
    for assignment in config.player_assignments:
        if not assignment.players:
            continue
        dsl = pcs_dsl.get(assignment.player_combat_strategy_id, "") if assignment.is_linked else assignment.dsl
        seats = seat_targets(dsl)
        if seats:
            uses.append((sorted(assignment.players), seats))
    return uses
