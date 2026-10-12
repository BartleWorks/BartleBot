"""Which duel strategy a fight runs: the four levels, most tailored first (epic #201).

For each fight, with ``n`` clients connected:

1. **The user's pick** for this bot: the Bot Combat Strategy chosen on the Combat tab.
2. **The bot's Official** Bot Combat Strategy: the shipped one whose duels fit ``n`` for
   the most of the bot's fights.
3. **The user's default**: their Solo or Team Duel Strategy, in ``preferences.json``.
4. **The shipped default**: Default Solo Duel or Default Team Duel.

A Bot Combat Strategy covers a fight with the duel assigned to it, or with its own default
duel when the fight has no assignment; only one with neither falls through. A duel
:meth:`~bartlebot.shared.strategy_models.DuelConfig.accepts` ``n`` when ``n`` is within its
player range. At levels 2 to 4 a duel that does not accept ``n`` falls to the next level and
the reason is kept. At level 1 the duel is used anyway, because the user chose it: the
pre-run check (:mod:`bartlebot.combat.combat_warnings`) is what tells them it does not fit,
and "Run anyway" means running what they chose.

Nothing here writes. No file is created or changed to make a level answer, which is what
keeps every bot file byte-identical and every level reachable (``docs/BOT_OPTIONS.md`` says
the same of options). Qt-free, like the modules beside it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from bartlebot.combat import strategy_store
from bartlebot.combat.bot_combat_strategy import StrategyLookup
from bartlebot.combat.fight_keys import bind, exposed_combat_states
from bartlebot.shared.combat_defaults import (
    DefaultDuel,
    DefaultSource,
    TeamSize,
    default_duel,
    shortfall_text,
    team_size,
)
from bartlebot.shared.preferences import PreferenceStore

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from bartlebot.bot.schema import BotConfig
    from bartlebot.combat.bot_strategy_models import BotCombatStrategy
    from bartlebot.shared.strategy_models import DuelCombatStrategy

logger = logging.getLogger(__name__)

NO_PICK_REASON = "this bot has no Bot Combat Strategy"

#: What the log calls the DSL a fight runs when no strategy file can be read.
LAST_RESORT_SOURCE = "BartleBot built-in default"

#: The fight a node with ``use_default_strategy`` runs: the bot's default, not a state's own.
DEFAULT_FIGHT = None

# A strategy id is too long to show; enough to find the file by.
_ID_PREVIEW = 8


class Level(Enum):
    """Where a fight's duel strategy came from, most tailored first."""

    PICK = "pick"
    OFFICIAL = "official"
    YOUR_DEFAULT = "your_default"
    SHIPPED_DEFAULT = "shipped_default"
    #: Not a level of the design: what a fight runs when no file can be read at all.
    LAST_RESORT = "last_resort"


@dataclass(frozen=True)
class FightResolution:
    """The answer for one fight.

    ``duel`` is None only at :attr:`Level.LAST_RESORT`. ``lookup`` finds the player
    strategies it links, its owner's own first. ``owner`` is the Bot Combat Strategy that
    answered, at levels 1 and 2. ``via`` names the assignment that chose the duel there, and
    ``unassigned`` says the fight had none of its own, so its owner's default covered it.
    ``reasons`` are what was passed over on the way, in the user's words, so the log can say
    why a fight is not on a more tailored level.
    """

    level: Level
    duel: DuelCombatStrategy | None
    lookup: StrategyLookup | None
    owner: BotCombatStrategy | None
    size: TeamSize
    via: str = ""
    unassigned: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def where(self) -> str:
        """Which level answered, and why not a more tailored one: ``your Team default; this bot has …``."""
        if self.level is Level.PICK:
            head = f"your Bot Combat Strategy '{self.owner.name}'"
        elif self.level is Level.OFFICIAL:
            head = f"Official Bot Combat Strategy '{self.owner.name}'"
        elif self.level is Level.YOUR_DEFAULT:
            head = f"your {self.size.value} default"
        elif self.level is Level.SHIPPED_DEFAULT:
            head = f"the shipped {self.size.value} default"
        else:
            head = "no strategy file could be read"
        return "; ".join([head, *self.reasons])

    @property
    def source_label(self) -> str:
        """One phrase for the log: the duel, the assignment that chose it, and the level."""
        if self.duel is None:
            return f"{LAST_RESORT_SOURCE} ({self.where})"
        via = f"via {self.via}; " if self.via else ""
        return f"duel strategy '{self.duel.name}' ({via}{self.where})"


def _short(uuid: str) -> str:
    return f"{uuid[:_ID_PREVIEW]}…"


def duel_choice(
    bcs: BotCombatStrategy, bot_config: BotConfig | None, state_id: str | None
) -> tuple[str | None, str, bool]:
    """The duel id *bcs* gives a fight, the assignment it came from, and whether that was the default for want of one.

    ``state_id`` None is a fight that uses the bot's default (``use_default_strategy``). An
    assignment that links nothing is the same as none, and a fight with no assignment uses
    the strategy's own default, which may be unset too.
    """
    if state_id is None:
        return bcs.default_strategy_id, "default strategy", False
    assignment = bind(bcs, bot_config).get(state_id)
    dcs_id = assignment.duel_combat_strategy_id if assignment is not None else None
    if dcs_id:
        return dcs_id, f"state assignment for '{state_id}'", False
    return bcs.default_strategy_id, f"default strategy (state '{state_id}' has no assignment)", True


def fight_ids(bot_config: BotConfig | None) -> list[str | None]:
    """The fights a run could reach: the default, then each fight that asked for a strategy of its own."""
    if bot_config is None:
        return [DEFAULT_FIGHT]
    return [DEFAULT_FIGHT, *(s.id for s in exposed_combat_states(bot_config))]


def ranked_officials(
    bot_config: BotConfig,
    clients: int,
    candidates: Iterable[BotCombatStrategy] | None = None,
    *,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> list[tuple[int, BotCombatStrategy]]:
    """The bot's shipped Bot Combat Strategies, best fit for *clients* first, each with its score.

    The score is how many of the bot's fights (its default counts as one) get a duel that
    exists and accepts *clients*. One that fits none is left out. Ties keep the order of
    *candidates*, which is arbitrary, so a shipped-content test refuses two that tie and a
    run never depends on it.

    *candidates* defaults to the shipped ones for the bot, read as they ship: a user's copy
    of one is the user's, and only ever their pick (``strategy_store.list_shipped``).
    """
    if candidates is None:
        shipped = strategy_store.list_shipped(strategy_store.StrategyKind.BOT, root, user_root)
        candidates = [bcs for bcs in shipped if bcs.bot_id == bot_config.id]
    fights = fight_ids(bot_config)
    ranked: list[tuple[int, BotCombatStrategy]] = []
    for bcs in candidates:
        lookup = StrategyLookup(bcs, root=root, user_root=user_root)
        score = 0
        for fight in fights:
            duel = lookup.duel(duel_choice(bcs, bot_config, fight)[0])
            if duel is not None and duel.accepts(clients):
                score += 1
        if score:
            ranked.append((score, bcs))
    ranked.sort(key=lambda pair: -pair[0])
    return ranked


def official_strategy_for(
    bot_config: BotConfig,
    clients: int,
    *,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> BotCombatStrategy | None:
    """The bot's shipped Bot Combat Strategy that fits *clients* best, or None."""
    ranked = ranked_officials(bot_config, clients, root=root, user_root=user_root)
    return ranked[0][1] if ranked else None


class CombatResolver:
    """Resolves the fights of one bot with *clients* connected, reading each file once.

    *pick* is the id of the user's Bot Combat Strategy for the bot, or None. *prefs* is
    where the user's defaults are read; None reads the vendor-wide ``preferences.json``.
    """

    def __init__(
        self,
        bot_config: BotConfig | None,
        clients: int,
        pick: str | None,
        *,
        prefs: PreferenceStore | None = None,
        root: str | Path | None = None,
        user_root: str | Path | None = None,
    ) -> None:
        self._bot_config = bot_config
        self._clients = clients
        self._pick = pick or None
        self._prefs = prefs
        self._root = root
        self._user_root = user_root
        self._library = StrategyLookup(None, root=root, user_root=user_root)
        self._picked: BotCombatStrategy | None = None
        self._picked_read = False
        self._official: BotCombatStrategy | None = None
        self._official_read = False
        self._default: DefaultDuel | None = None

    @property
    def bot_config(self) -> BotConfig | None:
        return self._bot_config

    @property
    def clients(self) -> int:
        return self._clients

    @property
    def pick(self) -> str | None:
        return self._pick

    @property
    def picked(self) -> BotCombatStrategy | None:
        """The user's pick, read; None when there is none or its file cannot be read."""
        if not self._picked_read:
            self._picked_read = True
            if self._pick:
                try:
                    self._picked = strategy_store.load_bot_strategy(self._pick, self._root, self._user_root)
                except Exception:
                    logger.debug("Could not read Bot Combat Strategy %s", self._pick, exc_info=True)
        return self._picked

    @property
    def official(self) -> BotCombatStrategy | None:
        """The bot's best-fitting shipped Bot Combat Strategy, or None."""
        if not self._official_read:
            self._official_read = True
            if self._bot_config is not None:
                self._official = official_strategy_for(
                    self._bot_config, self._clients, root=self._root, user_root=self._user_root
                )
        return self._official

    def fight(self, state_id: str | None) -> FightResolution:
        """Resolve the fight on *state_id*, or the bot's default fight for None."""
        size = team_size(self._clients)
        reasons: list[str] = []

        # Level 1: the user's pick, used even when it does not accept the team.
        if not self._pick:
            reasons.append(NO_PICK_REASON)
        elif self.picked is None:
            reasons.append(f"your Bot Combat Strategy's file is missing (id: {_short(self._pick)})")
        else:
            found, why = self._from(self.picked, state_id)
            if found is None:
                reasons.append(why)
            else:
                duel, lookup, via, unassigned = found
                note = () if duel.accepts(self._clients) else (shortfall_text(duel, self._clients),)
                return FightResolution(Level.PICK, duel, lookup, self.picked, size, via, unassigned, note)

        # Level 2: the bot's Official strategy, unless that is what the user picked.
        official = self.official
        if official is not None and official.id != self._pick:
            found, _why = self._from(official, state_id)
            if found is not None:
                duel, lookup, via, unassigned = found
                if duel.accepts(self._clients):
                    return FightResolution(
                        Level.OFFICIAL, duel, lookup, official, size, via, unassigned, tuple(reasons)
                    )
                reasons.append(f"Official {shortfall_text(duel, self._clients)}")

        # Levels 3 and 4: the defaults.
        answer = self._defaults()
        reasons.extend(answer.reasons)
        if answer.duel is None:
            return FightResolution(Level.LAST_RESORT, None, None, None, size, reasons=tuple(reasons))
        level = Level.YOUR_DEFAULT if answer.source is DefaultSource.YOURS else Level.SHIPPED_DEFAULT
        return FightResolution(level, answer.duel, self._library, None, size, reasons=tuple(reasons))

    def fights(self) -> dict[str | None, FightResolution]:
        """Every fight a run could reach, resolved: the default, then each exposed fight."""
        return {fight: self.fight(fight) for fight in fight_ids(self._bot_config)}

    def _from(self, bcs: BotCombatStrategy, state_id: str | None):
        """``((duel, lookup, via, unassigned), "")`` for the duel *bcs* gives the fight, or ``(None, why not)``."""
        dcs_id, via, unassigned = duel_choice(bcs, self._bot_config, state_id)
        if not dcs_id:
            return None, f"'{bcs.name}' has no duel strategy for this fight and no default"
        lookup = StrategyLookup(bcs, root=self._root, user_root=self._user_root)
        duel = lookup.duel(dcs_id)
        if duel is None:
            return None, f"'{bcs.name}' links a duel strategy that is missing (id: {_short(dcs_id)})"
        return (duel, lookup, via, unassigned), ""

    def _defaults(self) -> DefaultDuel:
        if self._default is None:
            prefs = self._prefs if self._prefs is not None else PreferenceStore()
            self._default = default_duel(self._clients, prefs, self._library.duel)
        return self._default


def resolve_fight(
    bot_config: BotConfig | None,
    state_id: str | None,
    clients: int,
    pick: str | None,
    *,
    prefs: PreferenceStore | None = None,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> FightResolution:
    """Resolve one fight: *state_id* is its state, or None for the bot's default.

    :class:`CombatResolver` resolves several, reading each file once.
    """
    return CombatResolver(bot_config, clients, pick, prefs=prefs, root=root, user_root=user_root).fight(state_id)


def fight_label(bot_config: BotConfig | None, state_id: str | None) -> str:
    """How a fight is named to the user: its label, else its id. The default fight is "the other fights"."""
    if state_id is None:
        return "the other fights"
    if bot_config is not None:
        for state in bot_config.states:
            if state.id == state_id:
                return f"'{state.label or state.id}'"
    return f"'{state_id}'"


def group_fights(resolutions: dict[str | None, FightResolution]) -> dict[tuple[str, str], list[str | None]]:
    """Fights grouped by the duel they run and the level that answered, in first-seen order."""
    groups: dict[tuple[str, str], list[str | None]] = {}
    for fight, res in resolutions.items():
        name = f"duel strategy '{res.duel.name}'" if res.duel is not None else LAST_RESORT_SOURCE
        groups.setdefault((name, res.where), []).append(fight)
    return groups


def describe_run(resolver: CombatResolver) -> str:
    """One line naming where the run's fights get their duel strategies, for the log at run start.

    Fights that run the same duel from the same level are one entry, so a bot whose eight
    exposed fights all fall to the default says so once.
    """
    groups = group_fights(resolver.fights())
    if len(groups) == 1:
        (name, where), _ = next(iter(groups.items()))
        return f"Combat: {name} ({where}) for every fight"
    parts = [
        f"{name} ({where}) for {', '.join(fight_label(resolver.bot_config, f) for f in fights)}"
        for (name, where), fights in groups.items()
    ]
    return "Combat: " + "; ".join(parts)
