"""A combat profile's own strategies, and the one rule for finding a strategy by id (#118).

A *combat profile* is a ``BotCombatStrategy``: one user's combat setup for one bot. Besides
its fights and decks it holds duel and player strategies of its own, for the fight that
needs something no other fight does. Those live inside the profile file rather than in
the library, so a one-off never clutters the list every other bot sees.

LOOKUP ORDER
------------

**The active profile first, then the library.** Every surface that turns a strategy id
into a strategy goes through :class:`StrategyLookup`, because a surface that resolves ids
its own way is how two surfaces come to disagree about which strategy a fight uses. That
covers ``resolve_combat``, the pre-run warnings, the usage counts and the Combat tab.

Profile strategies have ids like any other entry and are linked with the existing fields
(``StateAssignment.duel_combat_strategy_id``, ``BotCombatStrategy.default_strategy_id``,
``PlayerAssignment.player_combat_strategy_id``), so moving one into the library keeps it
linked with no rewrite.

LIBRARY CONTENT NEVER LINKS PROFILE CONTENT
-------------------------------------------

A library duel is shared by every bot and profile, so it may only use library player
strategies; otherwise it would depend on one user's profile. The dialog's assignment menu
enforces that, and :func:`library_links_to_profile` finds a violation that got in some
other way, such as a hand-edited file.

Qt-free, like :mod:`bartlebot.strategy_summary` and :mod:`bartlebot.combat_warnings`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from uuid import uuid4

from bartlebot import strategy_store
from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DuelCombatStrategy,
    PlayerCombatStrategy,
)

logger = logging.getLogger(__name__)


def _merged(profile_items: Iterable, library_items: Iterable) -> list:
    """Profile entries first, then every library entry the profile does not shadow."""
    profile_items = list(profile_items)
    shadowed = {item.id for item in profile_items}
    return profile_items + [item for item in library_items if item.id not in shadowed]


class StrategyLookup:
    """Finds a duel or player strategy by id: the active profile first, then the library.

    *duels* and *players* are the library, as lists. Leave either as None to load entries
    from the strategy store on demand instead, under *root* / *user_root* -- which is what
    the run-time resolver and the pre-run warnings want, since they touch a handful of ids
    and the library holds far more.
    """

    def __init__(
        self,
        profile: BotCombatStrategy | None,
        duels: Iterable[DuelCombatStrategy] | None = None,
        players: Iterable[PlayerCombatStrategy] | None = None,
        *,
        root: str | Path | None = None,
        user_root: str | Path | None = None,
    ) -> None:
        self._profile = profile
        self._library_duels = None if duels is None else list(duels)
        self._library_players = None if players is None else list(players)
        self._root = root
        self._user_root = user_root

    @property
    def profile(self) -> BotCombatStrategy | None:
        return self._profile

    # -- one entry ------------------------------------------------------

    def duel(self, uuid: str | None) -> DuelCombatStrategy | None:
        """The duel strategy *uuid* names, or None if neither the profile nor the library has it."""
        if uuid is None:
            return None
        found = self.profile_duel(uuid)
        if found is not None:
            return found
        if self._library_duels is not None:
            return next((d for d in self._library_duels if d.id == uuid), None)
        return self._load(strategy_store.load_duel_strategy, uuid)

    def player(self, uuid: str | None) -> PlayerCombatStrategy | None:
        """The player strategy *uuid* names, or None if neither the profile nor the library has it."""
        if uuid is None:
            return None
        found = self.profile_player(uuid)
        if found is not None:
            return found
        if self._library_players is not None:
            return next((p for p in self._library_players if p.id == uuid), None)
        return self._load(strategy_store.load_player_strategy, uuid)

    def profile_duel(self, uuid: str) -> DuelCombatStrategy | None:
        """*uuid* among the profile's own duel strategies only."""
        if self._profile is None:
            return None
        return next((d for d in self._profile.duel_strategies if d.id == uuid), None)

    def profile_player(self, uuid: str) -> PlayerCombatStrategy | None:
        """*uuid* among the profile's own player strategies only."""
        if self._profile is None:
            return None
        return next((p for p in self._profile.player_strategies if p.id == uuid), None)

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
        """Every duel strategy in reach, the profile's first. Needs the library as a list."""
        profile = self._profile.duel_strategies if self._profile is not None else []
        return _merged(profile, self._library_duels or [])

    @property
    def players(self) -> list[PlayerCombatStrategy]:
        """Every player strategy in reach, the profile's first. Needs the library as a list."""
        profile = self._profile.player_strategies if self._profile is not None else []
        return _merged(profile, self._library_players or [])


def profile_players_of(duel: DuelCombatStrategy, profile: BotCombatStrategy | None) -> list[PlayerCombatStrategy]:
    """The profile's own player strategies *duel* links, in assignment order, each once.

    What has to move with a profile duel into the library, since a library duel may not
    link profile content.
    """
    if profile is None:
        return []
    own = {p.id: p for p in profile.player_strategies}
    return [own[pcs_id] for pcs_id in dict.fromkeys(duel.linked_pcs_ids()) if pcs_id in own]


def library_links_to_profile(
    profile: BotCombatStrategy | None, library_duels: Iterable[DuelCombatStrategy]
) -> list[tuple[DuelCombatStrategy, PlayerCombatStrategy]]:
    """Every ``(library duel, profile player strategy)`` link that breaks the one-way rule.

    Only a hand-edited file gets one in: the dialog never offers a profile player strategy
    to a library duel. A profile duel shadowing a library one is the profile's, so it is
    skipped.
    """
    if profile is None:
        return []
    own_duels = {d.id for d in profile.duel_strategies}
    violations = []
    for duel in library_duels:
        if duel.id in own_duels:
            continue
        for pcs in profile_players_of(duel, profile):
            violations.append((duel, pcs))
    return violations


def duplicate_profile(bcs: BotCombatStrategy, name: str) -> BotCombatStrategy:
    """A copy of *bcs* under a fresh id and *name*, with fresh ids for everything it owns.

    Every profile strategy in the copy gets a new id, and every link inside the copy is
    rewritten to follow -- fight cards, the default, and duel-to-player links. Sharing ids
    would be harmless only until one copy was moved into the library: lookup checks the
    profile first, so the other profile's copy would then silently hide the library one.
    """
    copy = bcs.model_copy(deep=True, update={"id": str(uuid4()), "name": name})
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


def read_bot_names(bots_dir: str | Path) -> dict[str, str]:
    """``{bot id: bot name}`` for every bot file under *bots_dir*, for naming a profile's bot.

    Read raw rather than through ``BotConfig``: only two fields are wanted, from every bot
    in the library, and a file that does not parse is simply left out.
    """
    names: dict[str, str] = {}
    for path in Path(bots_dir).rglob("*.json"):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            names[data["id"]] = data.get("name") or path.stem
    return names
