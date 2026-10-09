"""Zones BartleBot refuses to run in, and the checks that enforce that.

WHY
    This tool automates PvE monotony. Pointing it at PvP hands one player an
    unearned advantage over another, which is a different thing entirely from
    grinding a dungeon nobody else is standing in. So the restricted set is
    scoped to PvP: the Beastmoon world, and the arenas a ranked match is played
    in. PvE is deliberately not restricted, raids included -- a raid is exactly
    the kind of repetitive content this exists for.

    A guard in an open-source tool is not a wall; anyone can edit this file.
    What it does is make the shipped build have no supported path into PvP, and
    that is the claim worth being able to make.

MATCHING
    ``world`` matches the first path segment exactly, which covers a whole world
    and any subzone added to it later. ``exact`` matches the full zone path.

    Substring matching is never used, and that is not fussiness: "Arena" appears
    in ``Darkmoor/Interiors/DM_Z01I18_ArenaOfSwords``,
    ``DragonSpire/.../DS_Arena_Gauntlet_6Room``, ``Krokotopia/.../KT_Arena``,
    the Wysteria story tournament and half a dozen housing zones -- all PvE, and
    several of them prime farming targets. A substring rule would ban the
    content the tool is for while adding nothing.

    ``WizardCity/WC_Duel_Arena`` is the Arena *lobby* and is deliberately absent:
    players queue there and are moved to a match zone, so the lobby itself is
    ordinary questing space. ``WC_Duel_Arena_New`` is a match zone despite the
    name and is restricted.

TWO SIGNALS
    ``check_zone`` is the list, and a list only knows the zones that existed when
    it was written. ``check_duel`` reads the game's own flags off the current
    duel, so a PvP zone added by a future patch is caught even though no rule
    names it. It can only speak once a fight has started, which is why it
    backstops the zone watch rather than replacing it.

    Neither ``m_bPVP`` nor ``m_bBattleground`` is by itself a PvP fight. Both
    select a *ruleset* -- match timers, the pass penalty, ``m_bIsPlayerTimedDuel``
    -- and Challenge Mode borrows that ruleset wholesale for ordinary PvE
    dungeons, setting both flags on a dungeon boss with no other player anywhere
    in the fight. Taking either at face value stopped the same Marleybone farm
    mid-fight, first as a "PvP duel" and then as a "Beastmoon duel".

    What separates the two is who is in the duel: a real PvP match has players
    on *both* teams, while a PvE fight -- Challenge Mode, minions,
    mind-controlled enemies -- only ever has players on one. So a flag is the
    precondition and the team make-up is the confirmation, and neither half is
    ever used alone. The team make-up cannot stand alone either: it is also what
    a PvE Team Up would look like the moment any future mode splits players
    across teams.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bartlebot.bot.schema import BotConfig, StateModel

logger = logging.getLogger(__name__)

RESTRICTED_ZONES_PATH = Path(__file__).resolve().parent / "data" / "restricted_zones.json"

WORLD = "world"
"""Rule kind: match the first segment of the zone path exactly."""

EXACT = "exact"
"""Rule kind: match the whole zone path exactly."""


@dataclass(frozen=True)
class Restriction:
    """One reason a client may not be automated where it currently is.

    ``pattern`` is the rule that matched for a zone rule, or the name of the duel
    flag that was set. ``reason`` is written for the user, not the log: it ends up
    in the dialog explaining why their bot just stopped.
    """

    pattern: str
    kind: str
    reason: str

    def describe(self) -> str:
        """One line naming both what matched and why it is restricted."""
        return f"{self.reason} ({self.pattern})"


DUEL_PVP = Restriction("duel.pvp", "duel_flag", "PvP duel")
"""A duel under the PvP ruleset *with players on both teams*, whatever zone it is in.

The flag alone is not enough -- see TWO SIGNALS above.
"""

DUEL_BATTLEGROUND = Restriction("duel.battleground", "duel_flag", "Beastmoon duel")
"""A battleground-ruleset duel *with players on both teams* -- Beastmoon, by any route in.

The flag alone is not enough; Challenge Mode sets it too. See TWO SIGNALS above.
"""


@cache
def _rules() -> tuple[Restriction, ...]:
    """Load and cache the rule list. A malformed file is fatal, on purpose.

    Every other data file in this package degrades gracefully when it cannot be
    read, because the alternative is a bot that will not start. This one inverts
    that: a safety guard that silently becomes a no-op is worse than an app that
    refuses to launch, and the failure is a packaging bug, not something a user
    can hit by playing the game.
    """
    raw = json.loads(RESTRICTED_ZONES_PATH.read_text(encoding="utf-8"))
    rules = tuple(
        Restriction(pattern=entry["pattern"], kind=entry["kind"], reason=entry["reason"]) for entry in raw["rules"]
    )
    unknown = [r for r in rules if r.kind not in (WORLD, EXACT)]
    if unknown:
        raise ValueError(f"restricted_zones.json has rules with unknown kind: {unknown}")
    logger.debug("Loaded %d restricted-zone rules", len(rules))
    return rules


def rules() -> tuple[Restriction, ...]:
    """Every restricted-zone rule, in file order."""
    return _rules()


def check_zone(zone_name: str | None) -> Restriction | None:
    """Return the rule restricting *zone_name*, or None if it is allowed.

    ``None`` and empty names are allowed: a client on a loading screen has no zone,
    and treating "unknown" as restricted would stop the bot on every transition.
    The zone becomes readable again a moment later, and the watcher polls fast
    enough that a genuinely restricted arrival is caught on the next tick.
    """
    if not zone_name:
        return None
    world = zone_name.split("/", 1)[0]
    for rule in _rules():
        if rule.kind == WORLD and world == rule.pattern:
            return rule
        if rule.kind == EXACT and zone_name == rule.pattern:
            return rule
    return None


def check_duel(*, pvp: bool, battleground: bool, players_on_both_teams: bool) -> Restriction | None:
    """Return the restriction implied by the current duel, if any.

    Callers must only pass values read from a client actually in combat -- the duel
    struct holds whatever the last fight left behind otherwise, and a stale True
    would hard-stop a bot standing in a questing zone.

    *pvp* and *battleground* are the game's ``m_bPVP`` / ``m_bBattleground`` ruleset
    flags, both of which Challenge Mode sets on PvE dungeons. *players_on_both_teams*
    is what tells a match from a dungeon, so every restriction here needs a flag
    **and** the team make-up. Which flag is set only decides how the stop is named.
    """
    if not players_on_both_teams:
        return None
    if pvp:
        return DUEL_PVP
    if battleground:
        return DUEL_BATTLEGROUND
    return None


def _zone_param_keys(params: dict) -> list[str]:
    """Param keys that hold a zone path.

    Matched by suffix rather than a fixed list so a node added later is covered
    without anyone remembering to update this. The convention across the node
    library is ``zone`` / ``to_zone`` / ``from_zone`` / ``mark_zone`` /
    ``dungeon_zone``; ``zone_list_entry`` is a UI row label, not a path, and is
    correctly excluded by the suffix test.
    """
    return [key for key in params if key == "zone" or key.endswith("_zone")]


#: Said after the list of zones wherever a bot is refused for one: at run start, and on import.
PVE_ONLY_TEXT = (
    "BartleBot is for PvE. PvP zones and Beastmoon are off limits, and this is not something that can be turned off."
)


def describe_found(found: Iterable[tuple[str, str, Restriction]]) -> str:
    """One bullet per restricted zone :func:`check_bot_config` found: the node, the zone, and why."""
    return "\n".join(f"• {label}: {zone} — {hit.reason}" for label, zone, hit in found)


def check_bot_config(config: BotConfig) -> list[tuple[str, str, Restriction]]:
    """Return (state label, zone, restriction) for every restricted zone a bot names.

    This is the pre-run check. Catching a restricted zone in the bot file is
    strictly better than catching it 250 ms after the client arrives there: the
    client never goes, so there is nothing to back out of.
    """
    return check_states(config.states)


def check_states(states: Iterable[StateModel]) -> list[tuple[str, str, Restriction]]:
    """:func:`check_bot_config` for any list of states, such as a subgraph definition's.

    What an import checks every bot and subgraph in a share file with (#171), so a file
    is refused for the same reason, in the same words, as running the bot would be.
    """
    found: list[tuple[str, str, Restriction]] = []
    for state in states:
        params = state.params or {}
        for key in _zone_param_keys(params):
            value = params.get(key)
            if not isinstance(value, str):
                continue
            hit = check_zone(value)
            if hit is not None:
                found.append((state.label or state.id, value, hit))
    return found
