"""Which duel strategy a fight runs: the four levels, most tailored first (epic #201).

For each fight, with ``n`` clients connected:

1. **The user's pick** for this bot: the Bot Combat Strategy chosen on the Combat tab.
2. **The bot's Official** Bot Combat Strategy: the shipped one whose duels fit ``n`` for
   the most of the bot's fights.
3. **The user's default**: their Solo or Team Duel Strategy, in ``preferences.json``.
4. **The shipped default**: Default Solo Duel or Default Team Duel, and for an ambient
   fight, one no node started, Default Team Duel (Everyone Attacks) in place of the latter.

A Bot Combat Strategy covers a fight with the duel assigned to it, or with its own default
duel when the fight has no assignment; only one with neither falls through. The user's
one falls past level 2 to the defaults, never to the Official one: its gaps are not filled
from the author's. Level 2 answers only when the user has no Bot Combat Strategy for the
bot, or its file is missing. A duel
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
    players_text,
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

    @property
    def is_default(self) -> bool:
        """Levels 3 and 4: a Solo or Team default, the user's or the shipped one, and nothing tailored.

        What a fight fled on is marked for (Stage 7 of epic #201). The last resort is not
        one: it runs when no file can be read, which tailoring would not fix.
        """
        return self in (Level.YOUR_DEFAULT, Level.SHIPPED_DEFAULT)


@dataclass(frozen=True)
class FightResolution:
    """The answer for one fight.

    ``duel`` is None only at :attr:`Level.LAST_RESORT`. ``lookup`` finds the player
    strategies it links, its owner's own first. ``owner`` is the Bot Combat Strategy that
    answered, at levels 1 and 2. ``via`` names the assignment that chose the duel there, and
    ``unassigned`` says the fight had none of its own, so its owner's default covered it.
    ``reasons`` are what was passed over on the way, in the user's words, so the log can say
    why a fight is not on a more tailored level. ``ambient`` is a fight no node started.
    """

    level: Level
    duel: DuelCombatStrategy | None
    lookup: StrategyLookup | None
    owner: BotCombatStrategy | None
    size: TeamSize
    via: str = ""
    unassigned: bool = False
    reasons: tuple[str, ...] = ()
    ambient: bool = False

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
            if self.ambient:
                head += " for an ambient fight"
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
    *ambient* resolves fights no node started, which changes only the shipped Team default.
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
        ambient: bool = False,
    ) -> None:
        self._bot_config = bot_config
        self._clients = clients
        self._pick = pick or None
        self._prefs = prefs
        self._ambient = ambient
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

        # Level 2: the bot's Official strategy, but only when the user has no Bot Combat
        # Strategy to speak of. One that loaded and left this fight uncovered goes to the
        # defaults instead: a run made of the user's strategy and the author's, fight by
        # fight, has no single owner to explain it (epic #201's decisions).
        official = self.official if self.picked is None else None
        if official is not None:
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
        return FightResolution(
            level, answer.duel, self._library, None, size, reasons=tuple(reasons), ambient=self._ambient
        )

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
            self._default = default_duel(self._clients, prefs, self._library.duel, ambient=self._ambient)
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
    ambient: bool = False,
) -> FightResolution:
    """Resolve one fight: *state_id* is its state, or None for the bot's default.

    :class:`CombatResolver` resolves several, reading each file once.
    """
    resolver = CombatResolver(bot_config, clients, pick, prefs=prefs, root=root, user_root=user_root, ambient=ambient)
    return resolver.fight(state_id)


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


# ---------------------------------------------------------------------------
# Saying it on screen: the Combat tab's banner and default card (Stage 5 of epic #201)
# ---------------------------------------------------------------------------

#: The banner's caption over a Bot Combat Strategy, the user's or an Official one.
BOT_STRATEGY_CAPTION = "Bot Combat Strategy"
#: The banner's caption when the bot has none, so the line under it is not read as one's name.
NO_BOT_STRATEGY_CAPTION = "No Bot Combat Strategy"
#: What the banner names when the defaults answer every fight.
DEFAULTS_NAME = "Default Duel Strategies"
#: The pill beside a shipped strategy's name: the banner's Official one, a shipped default.
OFFICIAL_PILL = "Official"
#: The pill beside the user's own Solo or Team default.
YOUR_DEFAULT_PILL = "Your default"

#: How a default is qualified in a line that names it: kept, or chosen.
_RECOMMENDED = "recommended"
_YOURS = "yours"


@dataclass(frozen=True)
class SourceText:
    """What the Combat tab's banner says about where a bot's fights get their duel strategies.

    ``level`` is the source the banner names: :attr:`Level.PICK` or :attr:`Level.OFFICIAL`
    for a Bot Combat Strategy, otherwise the level the defaults answered at. ``pill`` is
    empty or :data:`OFFICIAL_PILL`. ``reasons`` are what was passed over on the way, for
    the tooltip: the same words the log uses.
    """

    level: Level
    caption: str
    name: str
    detail: str = ""
    pill: str = ""
    reasons: tuple[str, ...] = ()


def team_sizes_for(clients: int) -> tuple[int, ...]:
    """The team sizes to describe: the one connected, or one and two when nobody is.

    With no client connected nothing says which default a run will use, so both are named.
    """
    return (clients,) if clients > 0 else (1, 2)


def defaults_in_use(
    clients: int,
    *,
    prefs: PreferenceStore | None = None,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> list[DefaultDuel]:
    """Levels 3 and 4 for the team on the field, or for Solo and Team with nobody connected."""
    prefs = prefs if prefs is not None else PreferenceStore()
    library = StrategyLookup(None, root=root, user_root=user_root)
    return [default_duel(n, prefs, library.duel) for n in team_sizes_for(clients)]


def default_pill(answers: Iterable[DefaultDuel]) -> str:
    """:data:`OFFICIAL_PILL` or :data:`YOUR_DEFAULT_PILL` when every answer is one or the other, else empty.

    With nobody connected, a Solo default of the user's and the recommended Team one share
    a card; one pill would be wrong about one of them, and the line under it names both.
    """
    sources = {a.source for a in answers if a.duel is not None}
    if sources == {DefaultSource.SHIPPED}:
        return OFFICIAL_PILL
    if sources == {DefaultSource.YOURS}:
        return YOUR_DEFAULT_PILL
    return ""


def defaults_text(answers: Iterable[DefaultDuel]) -> str:
    """``Team: Default Team Duel (recommended)``, one part per size, joined with a dot."""
    parts = []
    for answer in answers:
        if answer.duel is None:
            parts.append(f"{answer.size.value}: the built-in DSL, no strategy file could be read")
        else:
            whose = _YOURS if answer.source is DefaultSource.YOURS else _RECOMMENDED
            parts.append(f"{answer.size.value}: {answer.duel.name} ({whose})")
    return " · ".join(parts)


#: The team sizes each default answers for.
SIZE_RANGE = {TeamSize.SOLO: (1,), TeamSize.TEAM: (2, 3, 4)}


def _counts_text(counts: list[int]) -> str:
    """``2``, ``2 or 3``, ``2, 3 or 4``."""
    words = [str(n) for n in counts]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} or {words[-1]}"


def default_choice_notes(
    size: TeamSize,
    duel_id: str | None,
    *,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> list[str]:
    """What is wrong with choosing *duel_id* as the *size* default, in the user's words; empty when nothing is.

    None is the recommended one, which is never checked: it was written for its size. A
    choice that is gone, or does not fit some of the sizes it answers for, leaves those to
    the recommended one at run time, as :func:`~bartlebot.shared.combat_defaults.default_duel`
    does. A player strategy it links that is gone runs the built-in DSL in that slot.
    """
    if not duel_id:
        return []
    lookup = StrategyLookup(None, root=root, user_root=user_root)
    duel = lookup.duel(duel_id)
    if duel is None:
        return ["It is no longer in the library, so the recommended one is used."]
    notes = []
    unfit = [n for n in SIZE_RANGE[size] if not duel.accepts(n)]
    if unfit:
        noun = "client" if unfit == [1] else "clients"
        notes.append(
            f"It takes {players_text(duel)}, so with {_counts_text(unfit)} {noun} connected the recommended one is used."
        )
    missing = [
        a.player_combat_strategy_id
        for a in duel.player_assignments
        if a.player_combat_strategy_id and lookup.player(a.player_combat_strategy_id) is None
    ]
    if missing:
        notes.append("It links a player strategy that is missing, so those players fight with the built-in DSL.")
    return notes


def describe_source(
    bot_config: BotConfig | None,
    clients: int,
    pick: str | None,
    *,
    prefs: PreferenceStore | None = None,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> SourceText:
    """Where the bot's fights get their duel strategies, in the banner's words.

    The user's Bot Combat Strategy by name, then the bot's Official one, then the Default
    Duel Strategies with the Solo or Team duel in use. A Bot Combat Strategy that leaves
    its default fight uncovered says which default those fights fall to. Resolved as a run
    would resolve it (:class:`CombatResolver`), and nothing is written.
    """
    prefs = prefs if prefs is not None else PreferenceStore()
    resolver = CombatResolver(bot_config, max(clients, 1), pick, prefs=prefs, root=root, user_root=user_root)
    fight = resolver.fight(DEFAULT_FIGHT)
    answers = defaults_in_use(clients, prefs=prefs, root=root, user_root=user_root)
    picked = resolver.picked
    official = resolver.official if picked is None else None

    if picked is not None or official is not None:
        owner = picked if picked is not None else official
        level = Level.PICK if picked is not None else Level.OFFICIAL
        covered = fight.level is level
        detail = "" if covered else f"Fights with no duel strategy: {defaults_text(answers)}"
        return SourceText(
            level,
            BOT_STRATEGY_CAPTION,
            owner.name,
            detail,
            OFFICIAL_PILL if level is Level.OFFICIAL else "",
            fight.reasons,
        )

    if all(a.duel is None for a in answers):
        level = Level.LAST_RESORT
    elif any(a.source is DefaultSource.YOURS for a in answers):
        level = Level.YOUR_DEFAULT
    else:
        level = Level.SHIPPED_DEFAULT
    reasons = tuple(dict.fromkeys([*fight.reasons, *(r for a in answers for r in a.reasons)]))
    return SourceText(level, NO_BOT_STRATEGY_CAPTION, DEFAULTS_NAME, defaults_text(answers), reasons=reasons)
