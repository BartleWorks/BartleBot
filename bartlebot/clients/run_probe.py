"""What the run request reads from the clients before it starts a bot.

Two reads, both memory reads and so async, made in one task before any of the run's
dialogs open: which players are in a fight, and which level scaling players already have
an elixir active (#141). The run request shows its dialogs from the task's done callback,
outside any task, for the reason in "qasync + Python 3.13" in bartlebot/gui/CLAUDE.md.

**A player in a fight refuses the run (#203).** Everything before the first node needs the
players out of battle: decks cannot be changed mid-fight, and forming the group opens the
social window over the cards, which is how players come to sit out rounds until the game
asks "You Are Idle". The user can end a fight in seconds; the bot would have to guess
whether to fight it or flee it.

Players are named by number, as everywhere else the run speaks to the user.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from bartlebot.nodes.combat_runner import slots_in_battle

logger = logging.getLogger(__name__)


class RunProbe(NamedTuple):
    fighting: list[int]
    """1-based numbers of the players in a fight. A client that cannot be read is not counted."""
    elixir_players: tuple[int, ...]
    """1-based numbers of the level scaling players with an elixir already active."""


async def probe_before_run(clients: list, scaling_players) -> RunProbe:
    """Read who is fighting, and who among *scaling_players* already has an elixir.

    Never raises for an unreadable client: these reads gate dialogs, and a client that
    cannot be read must not make the bot unrunnable. A failed elixir read warns about nobody.
    """
    flags = await asyncio.gather(*(c.in_battle() for c in clients), return_exceptions=True)
    fighting = [slot + 1 for slot in slots_in_battle(flags)]

    elixirs: tuple[int, ...] = ()
    if scaling_players and not fighting:
        from bartlebot.nodes.utility.handle_level_scaling import players_with_elixir  # noqa: PLC0415

        try:
            elixirs = tuple(await players_with_elixir(clients, scaling_players))
        except Exception:
            logger.warning("Could not read the players' elixirs before the run", exc_info=True)
    return RunProbe(fighting, elixirs)


def refusal_text(fighting: list[int]) -> str:
    """The dialog's text when *fighting* (1-based player numbers) are in a fight."""
    if len(fighting) == 1:
        who, verb, pronoun = f"Player {fighting[0]}", "is", "it"
    else:
        who, verb, pronoun = f"Players {', '.join(str(n) for n in fighting[:-1])} and {fighting[-1]}", "are", "them"
    return (
        f"{who} {verb} in a fight. Finish or flee {pronoun}, then start the bot again.\n\n"
        "Before the first node runs, BartleBot applies decks and forms the group, and neither "
        "can be done mid-fight."
    )
