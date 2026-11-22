"""A Bot Combat Strategy as core's strategy working set sees it: a ``StrategyOwner``.

The working set, the summaries and the tags are core's (``bartleby.runtime.combat``),
shared with BartleBuddy, which has no Bot Combat Strategy (BartleBuddy #31, Stage 5).
What makes a Bot Combat Strategy one to them is :class:`BotStrategyOwner`: its own duel
and player strategies, the library duels it names for its fights, and how it is
written. :func:`load_working_set` is how BartleBot opens one, with every Bot Combat
Strategy counted and the loaded bot's held open.

The working set says *owner* where BartleBot says Bot Combat Strategy, and *own* where
it says the bot's own. :func:`bot_strategy_of` is the Bot Combat Strategy behind the
open owner, for code that needs one.
"""

from __future__ import annotations

import json
from pathlib import Path

from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared.strategy_library import StrategyKind

from bartlebot.combat.bot_combat_strategy import StrategyLookup
from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.strategy_store import list_all_bot_strategies, save_bot_strategy
from bartlebot.content.store_roots import resolve_shared_roots

#: The Source value of an entry that belongs to the active Bot Combat Strategy, not the
#: library: the word on its pill and on its filter chip alike (#204). The section heading
#: above the cards already names which Bot Combat Strategy it is.
THIS_BOT_SOURCE = "This bot"


class BotStrategyOwner:
    """A Bot Combat Strategy, written under *root* / *user_root* in the strategy store's convention."""

    nouns = ("Bot Combat Strategy", "Bot Combat Strategies")
    source = THIS_BOT_SOURCE

    def __init__(
        self, strategy: BotCombatStrategy, root: str | Path | None = None, user_root: str | Path | None = None
    ) -> None:
        self.strategy = strategy
        self._root = root
        self._user_root = user_root

    @property
    def id(self) -> str:
        return self.strategy.id

    @property
    def name(self) -> str:
        return self.strategy.name

    def own(self, kind: StrategyKind) -> list:
        """Its own duels or player strategies: the live list."""
        return self.strategy.duel_strategies if kind is StrategyKind.DUEL else self.strategy.player_strategies

    def refers_to(self, duel_id: str) -> bool:
        """Whether its default, or any of its fights, names the duel *duel_id*."""
        return self.strategy.default_strategy_id == duel_id or any(
            a.duel_combat_strategy_id == duel_id for a in self.strategy.state_assignments
        )

    def snapshot(self) -> str:
        return json.dumps(self.strategy.model_dump(), sort_keys=True, default=str)

    def copy(self) -> BotStrategyOwner:
        return BotStrategyOwner(self.strategy.model_copy(deep=True), self._root, self._user_root)

    def save(self) -> None:
        save_bot_strategy(self.strategy, root=self._root, user_root=self._user_root)


def load_working_set(
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    *,
    bot_strategy_id: str | None = None,
    bot_id: str | None = None,
) -> StrategyWorkingSet:
    """The strategy library from the ordered roots, with every Bot Combat Strategy counted: the dialog's entry point.

    *bot_strategy_id* is the active Bot Combat Strategy to hold open, read from the same
    load as every other so the two can never disagree. *bot_id* is the loaded bot, for
    when it has none yet: the working set can then make one for it. Leave both unset for
    the library view. *root* and *user_root* are the strategy store's.
    """
    owners = [BotStrategyOwner(b, root, user_root) for b in list_all_bot_strategies(root, user_root)]
    active = next((o for o in owners if o.id == bot_strategy_id), None) if bot_strategy_id else None
    if active is not None:
        bot_id = active.strategy.bot_id

    def make_owner(name: str) -> BotStrategyOwner:
        return BotStrategyOwner(BotCombatStrategy(name=name, bot_id=bot_id), root, user_root)

    return StrategyWorkingSet.from_store(
        resolve_shared_roots(root, user_root),
        owner=active,
        others=owners,
        make_owner=make_owner if bot_id is not None else None,
    )


def bot_strategy_of(ws: StrategyWorkingSet) -> BotCombatStrategy | None:
    """The Bot Combat Strategy *ws* holds open, as its live copy; None in the library view or before one is made."""
    owner = ws.owner
    return owner.strategy if isinstance(owner, BotStrategyOwner) else None


def lookup(ws: StrategyWorkingSet) -> StrategyLookup:
    """Every duel and player strategy in reach of *ws*, the bot's own first: for summaries and the Combat tab."""
    return StrategyLookup(bot_strategy_of(ws), ws.duels, ws.players)
