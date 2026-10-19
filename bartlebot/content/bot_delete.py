"""Delete a bot from inside the app (#268): the file, and everything the user stored against its id.

Only the user's own bots can go. An Official bot ships in the install directory, which an
update replaces whole, so deleting it would bring it back (:func:`~bartlebot.content.bot_roots.is_shipped`).

Nothing *links* a bot the way a bot links a subgraph, so there is nothing to break and
nothing to warn about on that account. A fork's ``forked_from`` is a record of where it
came from, read only to say whether the Official bot has changed since; deleting either end
leaves the other running as it was. What does hang on a bot is what the user keyed on its
id: option values, accepted quicksell sales, and Bot Combat Strategies with the active
pick. With the file gone none of it can be reached again, so it goes too, and the
confirmation counts the strategies, which are the user's work.

No Qt here; the controller asks, and tells the canvas to re-list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bartlebot.bot.schema import BotConfig
from bartlebot.combat import strategy_store
from bartlebot.content.store_roots import declared_id, resolve_roots

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotDeletion:
    """What deleting one bot file takes with it."""

    path: Path
    bot_id: str
    name: str
    #: The user's own Bot Combat Strategies for this bot, by id. An Official one is not
    #: the user's to delete and is not listed.
    strategy_ids: tuple[str, ...]


def plan_deletion(
    path: str | Path, *, root: str | Path | None = None, user_root: str | Path | None = None
) -> BotDeletion:
    """What deleting the bot at ``path`` would remove. Raises what :meth:`BotConfig.load` raises."""
    bot = BotConfig.load(path)
    _, user = resolve_roots(root, user_root)
    folder = user / "bot_strategies"
    in_user_root = {declared_id(p) for p in folder.glob("*.json")} if folder.is_dir() else set()
    own = sorted(
        bcs.id for bcs in strategy_store.list_bot_strategies(bot.id, root, user_root) if bcs.id in in_user_root
    )
    return BotDeletion(Path(path), bot.id, bot.name, tuple(own))


def delete_bot(
    plan: BotDeletion, settings, *, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    """Remove the file first, then what was stored against its id. Raises ``OSError`` if the file stays.

    The file goes first so a failure leaves the bot whole, strategies and values included.
    Whatever is stored against the id after that is cleaned up best-effort: a leftover
    is unreachable, so it costs nothing but disk.
    """
    plan.path.unlink()
    for strategy_id in plan.strategy_ids:
        try:
            strategy_store.delete_bot_strategy(strategy_id, root, user_root)
        except OSError:
            logger.warning("Could not delete Bot Combat Strategy %s of a deleted bot", strategy_id, exc_info=True)
    settings.forget_bot(plan.bot_id)
    logger.info("Deleted bot '%s' and %d Bot Combat Strategy(ies)", plan.name, len(plan.strategy_ids))
