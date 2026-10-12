"""A Bot Combat Strategy's own strategies, and the one rule for finding a strategy by id (#118).

A ``BotCombatStrategy`` is one user's combat setup for one bot. Besides its fights and
decks it holds duel and player strategies of its own, for the fight that needs something
no other fight does. Those live inside its file rather than in the library, so a one-off
never clutters the list every other bot sees. The UI calls them **This bot**'s.

LOOKUP ORDER
------------

**The active Bot Combat Strategy's own first, then the library.** Every surface that
turns a strategy id into a strategy goes through :class:`StrategyLookup`, because a
surface that resolves ids its own way is how two surfaces come to disagree about which
strategy a fight uses. That covers ``combat_resolution``, the pre-run check, the usage
counts and the Combat tab.

Its own strategies have ids like any other entry and are linked with the existing fields
(``StateAssignment.duel_combat_strategy_id``, ``BotCombatStrategy.default_strategy_id``,
``PlayerAssignment.player_combat_strategy_id``), so moving one into the library keeps it
linked with no rewrite.

LIBRARY CONTENT NEVER LINKS A BOT'S OWN
---------------------------------------

A library duel is shared by every bot, so it may only use library player strategies;
otherwise it would depend on one user's setup for one bot. The dialog's assignment menu
enforces that, and :func:`library_links_to_own` finds a violation that got in some
other way, such as a hand-edited file.

Qt-free, like :mod:`bartlebot.combat.strategy_summary` and :mod:`bartlebot.combat.combat_warnings`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from uuid import uuid4

from bartlebot.combat import strategy_store
from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy

logger = logging.getLogger(__name__)


def _merged(own_items: Iterable, library_items: Iterable) -> list:
    """Its own entries first, then every library entry they do not shadow."""
    own_items = list(own_items)
    shadowed = {item.id for item in own_items}
    return own_items + [item for item in library_items if item.id not in shadowed]


class StrategyLookup:
    """Finds a duel or player strategy by id: the active Bot Combat Strategy's own first, then the library.

    *duels* and *players* are the library, as lists. Leave either as None to load entries
    from the strategy store on demand instead, under *root* / *user_root* -- which is what
    the run-time resolver and the pre-run check want, since they touch a handful of ids
    and the library holds far more.
    """

    def __init__(
        self,
        bot_strategy: BotCombatStrategy | None,
        duels: Iterable[DuelCombatStrategy] | None = None,
        players: Iterable[PlayerCombatStrategy] | None = None,
        *,
        root: str | Path | None = None,
        user_root: str | Path | None = None,
    ) -> None:
        self._bot_strategy = bot_strategy
        self._library_duels = None if duels is None else list(duels)
        self._library_players = None if players is None else list(players)
        self._root = root
        self._user_root = user_root

    @property
    def bot_strategy(self) -> BotCombatStrategy | None:
        return self._bot_strategy

    # -- one entry ------------------------------------------------------

    def duel(self, uuid: str | None) -> DuelCombatStrategy | None:
        """The duel strategy *uuid* names, or None if neither the bot's own nor the library has it."""
        if uuid is None:
            return None
        found = self.own_duel(uuid)
        if found is not None:
            return found
        if self._library_duels is not None:
            return next((d for d in self._library_duels if d.id == uuid), None)
        return self._load(strategy_store.load_duel_strategy, uuid)

    def player(self, uuid: str | None) -> PlayerCombatStrategy | None:
        """The player strategy *uuid* names, or None if neither the bot's own nor the library has it."""
        if uuid is None:
            return None
        found = self.own_player(uuid)
        if found is not None:
            return found
        if self._library_players is not None:
            return next((p for p in self._library_players if p.id == uuid), None)
        return self._load(strategy_store.load_player_strategy, uuid)

    def own_duel(self, uuid: str) -> DuelCombatStrategy | None:
        """*uuid* among the Bot Combat Strategy's own duel strategies only."""
        if self._bot_strategy is None:
            return None
        return next((d for d in self._bot_strategy.duel_strategies if d.id == uuid), None)

    def own_player(self, uuid: str) -> PlayerCombatStrategy | None:
        """*uuid* among the Bot Combat Strategy's own player strategies only."""
        if self._bot_strategy is None:
            return None
        return next((p for p in self._bot_strategy.player_strategies if p.id == uuid), None)

    def _load(self, loader: Callable, uuid: str):
        try:
            return loader(uuid, root=self._root, user_root=self._user_root)
        except Exception:
            # The caller says what a missing strategy means where it is; this only says why.
            logger.debug("No strategy %s in the library", uuid, exc_info=True)
            return None

    # -- whole lists ----------------------------------------------------

    @property
    def duels(self) -> list[DuelCombatStrategy]:
        """Every duel strategy in reach, the bot's own first. Needs the library as a list."""
        own = self._bot_strategy.duel_strategies if self._bot_strategy is not None else []
        return _merged(own, self._library_duels or [])

    @property
    def players(self) -> list[PlayerCombatStrategy]:
        """Every player strategy in reach, the bot's own first. Needs the library as a list."""
        own = self._bot_strategy.player_strategies if self._bot_strategy is not None else []
        return _merged(own, self._library_players or [])


def own_players_of(duel: DuelCombatStrategy, bot_strategy: BotCombatStrategy | None) -> list[PlayerCombatStrategy]:
    """*bot_strategy*'s own player strategies *duel* links, in assignment order, each once.

    What has to move with one of its own duels into the library, since a library duel may
    not link a bot's own content.
    """
    if bot_strategy is None:
        return []
    own = {p.id: p for p in bot_strategy.player_strategies}
    return [own[pcs_id] for pcs_id in dict.fromkeys(duel.linked_pcs_ids()) if pcs_id in own]


def library_links_to_own(
    bot_strategy: BotCombatStrategy | None, library_duels: Iterable[DuelCombatStrategy]
) -> list[tuple[DuelCombatStrategy, PlayerCombatStrategy]]:
    """Every ``(library duel, own player strategy)`` link that breaks the one-way rule.

    Only a hand-edited file gets one in: the dialog never offers a bot's own player
    strategy to a library duel. One of its own duels shadowing a library one is its own,
    so it is skipped.
    """
    if bot_strategy is None:
        return []
    own_duels = {d.id for d in bot_strategy.duel_strategies}
    violations = []
    for duel in library_duels:
        if duel.id in own_duels:
            continue
        for pcs in own_players_of(duel, bot_strategy):
            violations.append((duel, pcs))
    return violations


def duplicate_bot_strategy(bcs: BotCombatStrategy, name: str) -> BotCombatStrategy:
    """A copy of *bcs* under a fresh id and *name*, with fresh ids for everything it owns.

    Every strategy of its own in the copy gets a new id, and every link inside the copy is
    rewritten to follow -- fight cards, the default, and duel-to-player links. Sharing ids
    would be harmless only until one copy was moved into the library: lookup checks the
    bot's own first, so the other copy would then silently hide the library one.
    """
    # A new item, so a fresh history too (#169): the one it was copied from is not its past.
    copy = bcs.model_copy(deep=True, update={"id": str(uuid4()), "name": name, "history": [], "sent_sha256": None})
    id_map: dict[str, str] = {}
    for item in (*copy.duel_strategies, *copy.player_strategies):
        new_id = str(uuid4())
        id_map[item.id] = new_id
        item.id = new_id
    for duel in copy.duel_strategies:
        for assignment in duel.player_assignments:
            pcs_id = assignment.player_combat_strategy_id
            if pcs_id in id_map:
                assignment.player_combat_strategy_id = id_map[pcs_id]
    for assignment in copy.state_assignments:
        dcs_id = assignment.duel_combat_strategy_id
        if dcs_id in id_map:
            assignment.duel_combat_strategy_id = id_map[dcs_id]
    if copy.default_strategy_id in id_map:
        copy.default_strategy_id = id_map[copy.default_strategy_id]
    return copy


def read_bot_names(*bots_dirs: str | Path) -> dict[str, str]:
    """``{bot id: bot name}`` for every bot file under each of *bots_dirs*, for naming a Bot Combat Strategy's bot.

    Read raw rather than through ``BotConfig``: only two fields are wanted, from every bot
    in the library, and a file that does not parse is simply left out.
    """
    names: dict[str, str] = {}
    for path in (p for d in bots_dirs for p in Path(d).rglob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            names[data["id"]] = data.get("name") or path.stem
    return names
