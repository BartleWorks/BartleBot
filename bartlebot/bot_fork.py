"""Make my own copy: the user's own copy of an Official bot, with everything the user set for it (#149).

An Official bot is read-only: it ships in the install directory, and every app update
replaces it. Run mode needs no copy, because what the user sets for a bot -- its option
values, its Bot Combat Strategies and which one is active -- lives in settings and in
``bot_strategies/``, never in the bot file. Editing the graph does, and this is that copy.

A copy is a fork, not an override (``docs/SHARED_CONTENT.md``): a new id, so the Official
bot keeps its own, and :class:`~bartlebot.bot.schema.ForkedFrom` naming the bot it came
from and that bot's content hash, so "the Official bot has changed since" can be told
later. Unlike Save As, which deliberately takes none of it, the copy takes everything
keyed on the bot's id, so it runs exactly as the Official bot did:

- every option value the user stored (``bot_option_overrides``), and the quicksell sales
  they accepted, which are consent given against those values;
- every Bot Combat Strategy of the Official bot, duplicated through
  :func:`~bartlebot.combat_profile.duplicate_profile` and pointed at the copy;
- the active pick, mapped to the duplicate of the strategy that was active.

No Qt here; the controller asks for the name and opens the result.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from bartlebot.bot.schema import BotConfig, ForkedFrom
from bartlebot.combat_profile import duplicate_profile
from bartlebot.new_bot import file_stem, unique_file_name
from bartlebot.strategy_store import list_bot_strategies, save_bot_strategy

logger = logging.getLogger(__name__)


def content_sha256(config: BotConfig) -> str:
    """The SHA-256 of a bot's content: what it would write, dumped with sorted keys.

    Content rather than file bytes, so a file reformatted, or written by an older
    version with a default spelled out, hashes the same as long as it says the same
    thing. Stage 3 of the shared content plan (#169) adopts this as the one content hash.
    """
    text = json.dumps(config.file_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_fork(official: BotConfig, name: str) -> BotConfig:
    """The user's copy of ``official``: a new id, ``name``, and ``forked_from`` naming where it came from.

    ``official`` should be the bot as its file has it, not as composed in memory, so
    the hash recorded is the Official file's. Everything else is copied as it is,
    ``template`` included: a copy of a bot has the same base.
    """
    fork = official.model_copy(deep=True)
    fork.id = str(uuid4())
    fork.name = name.strip() or official.name
    fork.forked_from = ForkedFrom(id=official.id, sha256=content_sha256(official))
    return fork


def fork_path(official_path: str | Path, user_bots_dir: str | Path, world_folder: str | None) -> Path:
    """Where the copy is written: the user's folder, in the Official bot's world folder, under its file name.

    The Official file's own name, so the copy sits where the user would look for it,
    with ``-2`` and on past a file already there.
    """
    folder = Path(user_bots_dir) / world_folder if world_folder else Path(user_bots_dir)
    stem = Path(official_path).stem or file_stem("")
    return folder / unique_file_name(stem, folder)


@dataclass
class CarriedOver:
    """What :func:`carry_over` copied, for the log and the tests."""

    #: How many option values were copied under the new id.
    options: int = 0
    #: The new Bot Combat Strategies, by the id of the one each was duplicated from.
    strategies: dict[str, str] = field(default_factory=dict)
    #: The active pick under the new id, or None when the Official bot had none.
    active: str | None = None


def carry_over(
    old_id: str,
    new_id: str,
    settings,
    *,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> CarriedOver:
    """Copy everything stored against ``old_id`` to ``new_id``: option values, combat, the active pick.

    ``settings`` is the ``SettingsManager`` (or a test's stand-in) holding the option
    values and the active pick; ``root`` and ``user_root`` locate ``bot_strategies/``,
    as every strategy store function takes them. The Official bot's own entries are
    left as they are, so it still runs as before. Each Bot Combat Strategy keeps its
    name: they belong to a different bot now, so nothing collides.
    """
    result = CarriedOver()

    overrides = settings.get("bot_option_overrides", old_id) or {}
    if isinstance(overrides, dict):
        for name, value in overrides.items():
            settings.set_option_override(new_id, name, value)
        result.options = len(overrides)
    accepted = settings.accepted_quicksell_sales(old_id)
    if accepted:
        settings.accept_quicksell_sales(new_id, accepted)
        settings.save()

    for bcs in list_bot_strategies(old_id, root, user_root):
        copy = duplicate_profile(bcs, bcs.name)
        copy.bot_id = new_id
        save_bot_strategy(copy, root, user_root)
        result.strategies[bcs.id] = copy.id

    active = settings.get_active_strategy(old_id)
    if active in result.strategies:
        result.active = result.strategies[active]
        settings.set_active_strategy(new_id, result.active)
    logger.info(
        "Carried %d option value(s) and %d Bot Combat Strategy(ies) to the copy",
        result.options,
        len(result.strategies),
    )
    return result
