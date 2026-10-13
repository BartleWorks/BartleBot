"""The shipped default duel strategies, and the DSL a fight runs when no file can be read.

Every product by this vendor fights, and the defaults are shipped content they share
(``docs/COMBAT_DEFAULTS.md``), so the ids and the last-resort DSL live here and import
nothing product-specific.

The three duels are Official items in ``strategies/duel/``, found by id:

- :data:`SHIPPED_SOLO_DUEL_ID`: one player, who runs Default.
- :data:`SHIPPED_TEAM_DUEL_ID`: P1 runs Default, P2 to P4 run Default Support, which
  supports while a boss is up and hits otherwise.
- :data:`SHIPPED_EVERYONE_ATTACKS_DUEL_ID`: every player runs Default.

:func:`default_duel` is levels 3 and 4 of a fight's resolution: the user's Solo or Team duel
when one is set and fits the team, and the shipped one otherwise. BartleBuddy calls the same
function, so a default set in either product is the default in both.

An ambient fight, one no node started, gets Everyone Attacks as its shipped Team default
instead. Its players take their seats in whatever order they arrive, so Default Support's
blade on ``ally(last)`` would land on whoever happened to join last, not on P1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bartlebot.shared.preferences import PreferenceStore
    from bartlebot.shared.strategy_models import DuelCombatStrategy

_SEPARATOR = " | "

#: Card choice priority, highest first: buffs and debuffs before damage, falling back
#: gracefully when a preferred card is not in hand. Generic to any school.
_PRIORITY = [
    "any<trap & inc_damage>[potent] @ enemy",
    "any<trap & inc_damage & aoe>[potent]",
    "any<blade & out_damage>[sharp] @ self",
    "any<blade & out_damage & aoe>[sharp]",
    "any<global>",
    "any<aura & out_damage>",
    "any<shadow>",
    "any<damage & aoe>[any<mod_damage>]",
    "any<damage>[any<mod_damage>] @ enemy",
]

#: The generic single-wizard DSL. The shipped Default player strategy carries the same
#: text, and it is what a fight runs when the shipped files cannot be read at all.
LAST_RESORT_DSL = _SEPARATOR.join(_PRIORITY)

#: Default Solo Duel: one player.
SHIPPED_SOLO_DUEL_ID = "530aa34e-1567-4492-b507-12529a41f0db"
#: Default Team Duel: P1 hits, P2 to P4 support while a boss is up and hit otherwise.
SHIPPED_TEAM_DUEL_ID = "9594ae6a-2cdc-4990-9ee7-3c43bb715121"
#: Default Team Duel (Everyone Attacks): every player runs Default.
SHIPPED_EVERYONE_ATTACKS_DUEL_ID = "f51b5e89-ba2b-40da-81ef-301e26ca5dd5"
#: The Default player strategy, which the three duels above link.
SHIPPED_DEFAULT_PLAYER_ID = "a6ddb3e8-5a97-42de-a8d6-174a8353a589"
#: The Default Support player strategy.
SHIPPED_DEFAULT_SUPPORT_ID = "d2496f8b-85c5-44f5-98da-13fb0c7ddea5"


class TeamSize(Enum):
    """The two sizes a default is chosen for."""

    SOLO = "Solo"
    TEAM = "Team"


class DefaultSource(Enum):
    """Whose default answered: the user's own, or the one that ships."""

    YOURS = "yours"
    SHIPPED = "shipped"


def team_size(clients: int) -> TeamSize:
    """Solo for one connected client, Team for two or more."""
    return TeamSize.SOLO if clients <= 1 else TeamSize.TEAM


#: The key in ``preferences.json`` that holds the user's duel id for each size. Absent
#: means "recommended": the shipped duel, so a later fix to it reaches everyone who kept it.
PREFERENCE_KEYS = {
    TeamSize.SOLO: "combat.default_duel.solo",
    TeamSize.TEAM: "combat.default_duel.team",
}

SHIPPED_DUEL_IDS = {
    TeamSize.SOLO: SHIPPED_SOLO_DUEL_ID,
    TeamSize.TEAM: SHIPPED_TEAM_DUEL_ID,
}

#: The shipped default for an ambient fight. Nothing orders who joins it, so no seat is
#: known to be the hitter's, and every player hits.
SHIPPED_AMBIENT_DUEL_IDS = {
    TeamSize.SOLO: SHIPPED_SOLO_DUEL_ID,
    TeamSize.TEAM: SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
}


def players_text(duel: DuelCombatStrategy) -> str:
    """``1 player``, ``4 players`` or ``2 to 4 players``: how many *duel* takes."""
    low, high = duel.min_players, duel.max_players
    noun = "player" if high == 1 else "players"
    return f"{low} {noun}" if low == high else f"{low} to {high} {noun}"


def shortfall_text(duel: DuelCombatStrategy, clients: int) -> str:
    """``'Boss' takes 4 players, and 2 are connected``: why *duel* does not accept the team."""
    verb = "is" if clients == 1 else "are"
    return f"'{duel.name}' takes {players_text(duel)}, and {clients} {verb} connected"


@dataclass(frozen=True)
class DefaultDuel:
    """What :func:`default_duel` found.

    ``duel`` is None only when the shipped default could not be read either, and ``source``
    is then None too. ``reasons`` say why the user's default was passed over, in the user's
    words, and are empty when it answered or none was set.
    """

    size: TeamSize
    duel: DuelCombatStrategy | None
    source: DefaultSource | None
    reasons: tuple[str, ...] = ()


def default_duel(
    clients: int,
    prefs: PreferenceStore,
    library: Callable[[str], DuelCombatStrategy | None],
    *,
    ambient: bool = False,
) -> DefaultDuel:
    """The default duel strategy for *clients* connected clients.

    The user's duel for the team size when one is stored, readable, and accepts *clients*;
    otherwise the shipped one, from :data:`SHIPPED_AMBIENT_DUEL_IDS` for an *ambient* fight.
    The user's default answers an ambient fight too: they chose it. The shipped duel is never
    checked against *clients*: it was written for its size, and the alternative to it is the
    last-resort DSL.

    *library* finds a duel by id and returns None for one it cannot. Nothing is written.
    """
    size = team_size(clients)
    reasons: list[str] = []
    stored = prefs.get(PREFERENCE_KEYS[size])
    if isinstance(stored, str) and stored:
        mine = library(stored)
        if mine is None:
            reasons.append(f"your {size.value} default is not in the library")
        elif not mine.accepts(clients):
            reasons.append(f"your {size.value} default {shortfall_text(mine, clients)}")
        else:
            return DefaultDuel(size, mine, DefaultSource.YOURS)
    shipped = library((SHIPPED_AMBIENT_DUEL_IDS if ambient else SHIPPED_DUEL_IDS)[size])
    if shipped is None:
        return DefaultDuel(size, None, None, tuple(reasons))
    return DefaultDuel(size, shipped, DefaultSource.SHIPPED, tuple(reasons))
